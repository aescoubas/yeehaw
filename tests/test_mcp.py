"""Tests for MCP server tool functions."""

from __future__ import annotations

from pathlib import Path

import pytest

import yeehaw.mcp.server as mcp_server
from yeehaw.store.store import Store


@pytest.fixture
def mcp_store(tmp_path: Path) -> Store:
    """Initialize module-level MCP store for tests."""
    store = Store(tmp_path / ".yeehaw" / "yeehaw.db")
    mcp_server._store = store
    try:
        yield store
    finally:
        mcp_server._store = None
        store.close()


def test_create_project_and_list_projects(mcp_store: Store) -> None:
    created = mcp_server.create_project("proj-a", "/tmp/repo-a")

    assert created["name"] == "proj-a"
    assert created["id"] > 0

    projects = mcp_server.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "proj-a"


def test_create_roadmap_and_approve_flow(mcp_store: Store) -> None:
    mcp_server.create_project("proj-a", "/tmp/repo-a")

    markdown = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Build store
Implement store.
## Phase 2: Integration
### Task 2.1: Wire orchestrator
Implement orchestrator.
""".strip()

    created = mcp_server.create_roadmap("proj-a", markdown)
    assert "roadmap_id" in created
    assert created["phases"] == 2
    assert created["tasks"] == 2

    status_before = mcp_server.get_project_status("proj-a")
    assert status_before["roadmap_status"] == "draft"

    approved = mcp_server.approve_roadmap("proj-a")
    assert approved == {"approved": True, "queued_tasks": 1}

    tasks = mcp_server.list_tasks(project_name="proj-a")
    by_number = {task["task_number"]: task["status"] for task in tasks}
    assert by_number["1.1"] == "queued"
    assert by_number["2.1"] == "pending"


def test_create_roadmap_validation_error(mcp_store: Store) -> None:
    mcp_server.create_project("proj-a", "/tmp/repo-a")

    result = mcp_server.create_roadmap(
        "proj-a",
        "# Roadmap: proj-a\n## Phase 2: Wrong\n### Task 2.1: x\ntext",
    )

    assert result["error"] == "Validation failed"
    assert "details" in result
