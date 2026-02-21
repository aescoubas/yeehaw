"""FastMCP server exposing yeehaw task management tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap
from yeehaw.store.store import Store

_store: Store | None = None

mcp = FastMCP("yeehaw")


def _get_store() -> Store:
    assert _store is not None, "Store not initialized"
    return _store


@mcp.tool()
def create_project(name: str, repo_root: str) -> dict[str, Any]:
    """Create a new project entry."""
    store = _get_store()
    project_id = store.create_project(name, repo_root)
    return {"id": project_id, "name": name, "repo_root": repo_root}


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """List all registered projects."""
    return _get_store().list_projects()


@mcp.tool()
def create_roadmap(project_name: str, markdown: str) -> dict[str, Any]:
    """Parse and persist a roadmap markdown for a project."""
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    try:
        roadmap = parse_roadmap(markdown)
    except ValueError as exc:
        return {"error": str(exc)}

    errors = validate_roadmap(roadmap)
    if errors:
        return {"error": "Validation failed", "details": errors}

    roadmap_id = store.create_roadmap(project["id"], markdown)
    task_count = 0
    for phase in roadmap.phases:
        phase_id = store.create_phase(
            roadmap_id,
            phase.number,
            phase.title,
            phase.verify_cmd,
        )
        for task in phase.tasks:
            store.create_task(roadmap_id, phase_id, task.number, task.title, task.description)
            task_count += 1

    return {
        "roadmap_id": roadmap_id,
        "phases": len(roadmap.phases),
        "tasks": task_count,
    }


@mcp.tool()
def list_tasks(
    project_name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks optionally filtered by project and status."""
    store = _get_store()
    project_id = None
    if project_name:
        project = store.get_project(project_name)
        if not project:
            return [{"error": f"Project '{project_name}' not found"}]
        project_id = project["id"]
    return store.list_tasks(project_id=project_id, status=status)


@mcp.tool()
def get_project_status(project_name: str) -> dict[str, Any]:
    """Return roadmap and phase progress summary for a project."""
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        return {"project": project_name, "roadmap": None, "phases": []}

    phases = store.list_phases(roadmap["id"])
    phase_status: list[dict[str, Any]] = []
    for phase in phases:
        tasks = store.list_tasks_by_phase(phase["id"])
        phase_status.append(
            {
                "phase": phase["phase_number"],
                "title": phase["title"],
                "status": phase["status"],
                "tasks_total": len(tasks),
                "tasks_done": sum(1 for task in tasks if task["status"] == "done"),
                "tasks_in_progress": sum(
                    1 for task in tasks if task["status"] == "in-progress"
                ),
                "tasks_failed": sum(1 for task in tasks if task["status"] == "failed"),
            }
        )

    return {
        "project": project_name,
        "roadmap_id": roadmap["id"],
        "roadmap_status": roadmap["status"],
        "phases": phase_status,
    }


@mcp.tool()
def approve_roadmap(project_name: str) -> dict[str, Any]:
    """Approve active draft roadmap and queue phase-1 tasks."""
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        return {"error": "No active roadmap"}
    if roadmap["status"] != "draft":
        return {"error": f"Roadmap is '{roadmap['status']}', not 'draft'"}

    store.update_roadmap_status(roadmap["id"], "approved")

    phases = store.list_phases(roadmap["id"])
    queued = 0
    if phases:
        phase_1 = phases[0]
        tasks = store.list_tasks_by_phase(phase_1["id"])
        for task in tasks:
            store.queue_task(task["id"])
            queued += 1
        store.update_phase_status(phase_1["id"], "executing")

    store.update_roadmap_status(roadmap["id"], "executing")

    return {"approved": True, "queued_tasks": queued}


@mcp.tool()
def update_task(
    task_id: int,
    status: str | None = None,
    assigned_agent: str | None = None,
) -> dict[str, Any]:
    """Update task status and assignment metadata."""
    store = _get_store()
    task = store.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    if status:
        if status in ("done", "blocked"):
            store.complete_task(task_id, status)
        elif status == "failed":
            store.fail_task(task_id, "Manually marked as failed")
        elif status == "queued":
            store.queue_task(task_id)

    if assigned_agent and task.get("status") == "pending":
        store._conn.execute(
            "UPDATE tasks SET assigned_agent = ?, updated_at = ? WHERE id = ?",
            (assigned_agent, store._now(), task_id),
        )
        store._conn.commit()

    return {"task_id": task_id, "updated": True}


def main() -> None:
    """Entry point for MCP server process."""
    parser = argparse.ArgumentParser(description="Yeehaw MCP Server")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    args = parser.parse_args()

    global _store
    _store = Store(Path(args.db))

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
