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


def test_preview_roadmap_verbose_format_with_color(mcp_store_extra: Store) -> None:
    del mcp_store_extra  # fixture initializes module-level store for tools

    markdown = """
# Roadmap: chamicore-lib
## Phase 0: Foundation (chamicore-lib)
### P0.1: httputil — envelope types and response helpers [x]
**Depends on:** none
**Repo:** chamicore-lib
**Files:**
- `httputil/envelope.go` — envelope model types
**Description:**
Implement envelope types and helpers.
**Done when:**
- [ ] Envelope JSON round-trips
""".strip()

    preview = mcp_server.preview_roadmap(markdown, color=True)
    assert preview["valid"] is True
    assert preview["errors"] == []
    assert preview["phases"] == 1
    assert preview["tasks"] == 1
    assert "\x1b[" in preview["preview"]
    assert "Phase 0: Foundation (chamicore-lib)" in preview["preview"]
    assert "Task 0.1: httputil — envelope types and response helpers" in preview["preview"]


def test_preview_roadmap_parse_error(mcp_store_extra: Store) -> None:
    del mcp_store_extra  # fixture initializes module-level store for tools

    preview = mcp_server.preview_roadmap("## Phase 1: Missing header", color=False)
    assert preview["valid"] is False
    assert "Missing roadmap header" in preview["errors"][0]
    assert "Roadmap parse error" in preview["preview"]


def test_mcp_get_and_edit_roadmap_in_place(mcp_store_extra: Store) -> None:
    del mcp_store_extra
    mcp_server.create_project("proj-a", "/tmp/repo-a")

    original = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
### Task 1.2: Build core
Implement core
### Task 1.3: Add tests
Add baseline tests
""".strip()
    created = mcp_server.create_roadmap("proj-a", original)
    assert "roadmap_id" in created
    assert mcp_server.approve_roadmap("proj-a")["approved"] is True

    tasks_before = mcp_server.list_tasks(project_name="proj-a")
    task_11 = next(task for task in tasks_before if task["task_number"] == "1.1")
    assert mcp_server.update_task(int(task_11["id"]), status="done")["updated"] is True

    current = mcp_server.get_roadmap("proj-a", color=False)
    assert current["roadmap_id"] == created["roadmap_id"]
    assert current["roadmap_status"] == "executing"
    assert current["valid"] is True
    assert "Build core" in current["markdown"]

    edited = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
### Task 1.2: Build core
Implement core
### Task 1.3: Add migration
Add migration helper before tests
### Task 1.4: Add tests
Add baseline tests
""".strip()
    edit_result = mcp_server.edit_roadmap("proj-a", edited)
    assert edit_result["roadmap_id"] == created["roadmap_id"]
    assert edit_result["tasks_created"] == 1
    assert edit_result["tasks_updated"] == 1
    assert edit_result["tasks_deleted"] == 0
    assert edit_result["tasks_queued"] == 1

    tasks_after = mcp_server.list_tasks(project_name="proj-a")
    by_number = {task["task_number"]: task for task in tasks_after}
    assert sorted(by_number.keys()) == ["1.1", "1.2", "1.3", "1.4"]
    assert by_number["1.1"]["status"] == "done"
    assert by_number["1.2"]["status"] == "queued"
    assert by_number["1.3"]["status"] == "queued"
    assert by_number["1.4"]["status"] == "queued"


def test_mcp_edit_roadmap_rejects_modifying_done_task(mcp_store_extra: Store) -> None:
    del mcp_store_extra
    mcp_server.create_project("proj-a", "/tmp/repo-a")
    md = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
""".strip()
    mcp_server.create_roadmap("proj-a", md)
    mcp_server.approve_roadmap("proj-a")
    task = mcp_server.list_tasks(project_name="proj-a")[0]
    mcp_server.update_task(int(task["id"]), status="done")

    edited = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup changed
Initial setup
""".strip()
    result = mcp_server.edit_roadmap("proj-a", edited)
    assert "error" in result
    assert "Cannot modify task 1.1" in result["error"]


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
