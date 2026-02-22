"""Additional MCP server branch tests."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

import yeehaw.mcp.server as mcp_server
from yeehaw.store.store import Store


@pytest.fixture
def mcp_store_extra(tmp_path: Path) -> Store:
    store = Store(tmp_path / ".yeehaw" / "yeehaw.db")
    mcp_server._store = store
    try:
        yield store
    finally:
        mcp_server._store = None
        store.close()


def _seed_task(store: Store) -> int:
    existing = store.get_project("proj-a")
    if existing is None:
        project_id = store.create_project("proj-a", "/tmp/repo-a")
    else:
        project_id = existing["id"]
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task", "desc")
    return task_id


def test_get_store_requires_initialization() -> None:
    mcp_server._store = None
    with pytest.raises(AssertionError, match="Store not initialized"):
        mcp_server._get_store()


def test_mcp_error_paths_for_roadmap_and_status(mcp_store_extra: Store) -> None:
    assert mcp_server.create_roadmap("missing", "# Roadmap: x") == {
        "error": "Project 'missing' not found"
    }

    mcp_server.create_project("proj-a", "/tmp/repo-a")
    assert "error" in mcp_server.create_roadmap("proj-a", "## Phase 1: no header")

    tasks = mcp_server.list_tasks(project_name="missing")
    assert tasks == [{"error": "Project 'missing' not found"}]

    status_missing = mcp_server.get_project_status("missing")
    assert status_missing == {"error": "Project 'missing' not found"}

    status_none = mcp_server.get_project_status("proj-a")
    assert status_none["roadmap"] is None


def test_mcp_approve_and_update_task_branches(mcp_store_extra: Store) -> None:
    assert mcp_server.approve_roadmap("missing")["error"] == "Project 'missing' not found"

    mcp_server.create_project("proj-a", "/tmp/repo-a")
    assert mcp_server.approve_roadmap("proj-a")["error"] == "No active roadmap"

    md = "# Roadmap: proj-a\n## Phase 1: P1\n### Task 1.1: t\nbody\n"
    mcp_server.create_roadmap("proj-a", md)

    approved = mcp_server.approve_roadmap("proj-a")
    assert approved["approved"] is True

    again = mcp_server.approve_roadmap("proj-a")
    assert "not 'draft'" in again["error"]

    task = mcp_server.list_tasks(project_name="proj-a")[0]
    task_id = task["id"]

    assert mcp_server.update_task(9999)["error"] == "Task 9999 not found"

    assert mcp_server.update_task(task_id, status="failed")["updated"] is True
    assert mcp_store_extra.get_task(task_id)["status"] == "failed"

    # pending + assigned_agent update branch
    task_id_2 = _seed_task(mcp_store_extra)
    assert mcp_server.update_task(task_id_2, assigned_agent="gemini")["updated"] is True
    assert mcp_store_extra.get_task(task_id_2)["assigned_agent"] == "gemini"

    assert mcp_server.update_task(task_id_2, status="queued")["updated"] is True
    assert mcp_store_extra.get_task(task_id_2)["status"] == "queued"

    assert mcp_server.update_task(task_id_2, status="blocked")["updated"] is True
    assert mcp_store_extra.get_task(task_id_2)["status"] == "blocked"


def test_mcp_main_runs_stdio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    monkeypatch.setattr(
        mcp_server.argparse.ArgumentParser,
        "parse_args",
        lambda self: Namespace(db=str(db_path)),
    )

    called: dict[str, str] = {}

    def fake_run(*, transport: str) -> None:
        called["transport"] = transport

    monkeypatch.setattr(mcp_server.mcp, "run", fake_run)

    mcp_server.main()

    assert called["transport"] == "stdio"
    assert isinstance(mcp_server._store, Store)

    mcp_server._store.close()
    mcp_server._store = None
