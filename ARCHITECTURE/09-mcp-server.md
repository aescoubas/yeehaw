# 09 — MCP Server (The Brain Interface)

## Purpose

The FastMCP server exposes the SQLite store as **MCP tools** that the Planner
agent can call. This is the bridge between the AI Planner and the database.

The MCP server is a **separate process** started by `yeehaw plan`. It runs
alongside a Planner agent session (Claude or Gemini) that connects to it.

## Implementation

```python
from fastmcp import FastMCP

mcp = FastMCP("yeehaw")

@mcp.tool()
def create_project(name: str, repo_root: str) -> dict:
    """Create a new project for task tracking."""
    project_id = store.create_project(name, repo_root)
    return {"id": project_id, "name": name}

@mcp.tool()
def list_projects() -> list[dict]:
    """List all registered projects."""
    return store.list_projects()

@mcp.tool()
def create_roadmap(project_name: str, markdown: str) -> dict:
    """Create a roadmap from structured markdown. Returns roadmap ID and parsed phases/tasks."""
    project = store.get_project(project_name)
    roadmap = parser.parse_roadmap(markdown)
    errors = parser.validate_roadmap(roadmap)
    if errors:
        return {"error": "Validation failed", "details": errors}

    roadmap_id = store.create_roadmap(project["id"], markdown)
    for phase in roadmap.phases:
        phase_id = store.create_phase(roadmap_id, phase.number, phase.title, phase.verify_cmd)
        for task in phase.tasks:
            store.create_task(roadmap_id, phase_id, task.number, task.title, task.description)

    return {"roadmap_id": roadmap_id, "phases": len(roadmap.phases),
            "tasks": sum(len(p.tasks) for p in roadmap.phases)}

@mcp.tool()
def create_task(project_name: str, phase_number: int, title: str, description: str) -> dict:
    """Create a single task in an existing roadmap phase."""
    # ... resolve project → active roadmap → phase → create task
    return {"task_id": task_id}

@mcp.tool()
def update_task_status(task_id: int, status: str) -> dict:
    """Update a task's status (pending, queued, done, failed, blocked)."""
    store.complete_task(task_id, status)
    return {"task_id": task_id, "status": status}

@mcp.tool()
def list_tasks(project_name: str | None = None, status: str | None = None) -> list[dict]:
    """List tasks, optionally filtered by project and/or status."""
    project_id = store.get_project(project_name)["id"] if project_name else None
    return store.list_tasks(project_id=project_id, status=status)

@mcp.tool()
def get_project_status(project_name: str) -> dict:
    """Get comprehensive project status including phase progress and task counts."""
    # ... aggregate status across phases and tasks
    return status_dict

@mcp.tool()
def approve_roadmap(project_name: str) -> dict:
    """Approve the active roadmap and queue Phase 1 tasks for execution."""
    # ... approve roadmap, queue phase 1 tasks
    return {"approved": True, "queued_tasks": count}
```

## MCP Server Lifecycle

### Start (during `yeehaw plan`)

```python
import subprocess
import sys

def start_mcp_server(db_path: Path, port: int = 0) -> subprocess.Popen:
    """Start the MCP server as a subprocess."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "yeehaw.mcp.server", "--db", str(db_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc
```

### Connection Mode: stdio

The MCP server runs in **stdio mode** by default, which is what Claude Code
and Gemini CLI expect when configured as an MCP server:

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### Configuration for Planner Agent

The CLI generates a temporary MCP config that points the Planner agent to the
yeehaw MCP server:

```json
{
  "mcpServers": {
    "yeehaw": {
      "command": "python",
      "args": ["-m", "yeehaw.mcp.server", "--db", "/path/to/.yeehaw/yeehaw.db"]
    }
  }
}
```

## Planner Session Flow

1. `yeehaw plan briefing.md` starts:
   - Reads the briefing file
   - Writes MCP config to temp file
   - Launches Planner agent (e.g., Claude Code) with MCP config
   - Agent connects to yeehaw MCP server automatically
2. User interacts with Planner agent naturally
3. Planner calls `create_project()`, `create_roadmap()`, etc. via MCP
4. Session ends when user exits the Planner agent
5. `yeehaw roadmap show` to review what the Planner created

## Security

- MCP server runs locally, no network exposure
- stdio transport means no open ports
- DB path is explicit, no default access to system files
- Tools are scoped to yeehaw operations only
