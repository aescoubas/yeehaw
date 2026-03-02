"""Tests for sqlite store operations."""

from __future__ import annotations

import pytest

from yeehaw.roadmap.parser import parse_roadmap
from yeehaw.store.store import Store


def test_project_crud(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    assert project_id > 0

    loaded = store.get_project("proj-a")
    assert loaded is not None
    assert loaded["id"] == project_id
    assert loaded["repo_root"] == "/tmp/repo-a"

    listed = store.list_projects()
    assert [project["name"] for project in listed] == ["proj-a"]

    assert store.delete_project("proj-a") is True
    assert store.get_project("proj-a") is None
    assert store.delete_project("proj-a") is False


def test_roadmap_phase_task_lifecycle(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", "pytest")
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")

    roadmap = store.get_roadmap(roadmap_id)
    assert roadmap is not None
    assert roadmap["status"] == "draft"

    phase = store.get_phase(phase_id)
    assert phase is not None
    assert phase["title"] == "Foundation"

    tasks = store.list_tasks_by_phase(phase_id)
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id

    store.queue_task(task_id)
    store.assign_task(task_id, "codex", "branch-a", "/tmp/wt", "/tmp/signal")

    task = store.get_task(task_id)
    assert task is not None
    assert task["status"] == "in-progress"
    assert task["assigned_agent"] == "codex"
    assert task["attempts"] == 1

    assert store.count_active_tasks() == 1
    assert store.count_active_tasks(project_id=project_id) == 1

    store.complete_task(task_id, "done")
    finished = store.get_task(task_id)
    assert finished is not None
    assert finished["status"] == "done"
    assert finished["completed_at"] is not None

    store.update_phase_status(phase_id, "completed")
    updated_phase = store.get_phase(phase_id)
    assert updated_phase is not None
    assert updated_phase["status"] == "completed"

    store.update_roadmap_status(roadmap_id, "approved")
    active = store.get_active_roadmap(project_id)
    assert active is not None
    assert active["id"] == roadmap_id


def test_failed_task_and_list_filters(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task1 = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")
    task2 = store.create_task(roadmap_id, phase_id, "1.2", "Test", "desc")

    store.fail_task(task1, "boom")
    store.queue_task(task2)

    failed = store.list_tasks(project_id=project_id, status="failed")
    assert [task["task_number"] for task in failed] == ["1.1"]
    assert failed[0]["last_failure"] == "boom"

    queued = store.list_tasks(status="queued")
    assert [task["task_number"] for task in queued] == ["1.2"]


def test_pause_and_resume_task(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")

    store.queue_task(task_id)
    assert store.pause_task(task_id) is True
    paused = store.get_task(task_id)
    assert paused is not None
    assert paused["status"] == "paused"

    assert store.pause_task(task_id) is False
    assert store.resume_task(task_id) is True
    resumed = store.get_task(task_id)
    assert resumed is not None
    assert resumed["status"] == "queued"
    assert resumed["completed_at"] is None
    assert store.resume_task(task_id) is False

    store.assign_task(task_id, "codex", "branch-a", "/tmp/wt", "/tmp/signal")
    assert store.pause_task(task_id) is True
    paused_again = store.get_task(task_id)
    assert paused_again is not None
    assert paused_again["status"] == "paused"


def test_create_roadmap_supersedes_previous_for_same_project(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_1 = store.create_roadmap(project_id, "# Roadmap 1")
    roadmap_2 = store.create_roadmap(project_id, "# Roadmap 2")

    first = store.get_roadmap(roadmap_1)
    second = store.get_roadmap(roadmap_2)
    active = store.get_active_roadmap(project_id)

    assert first is not None
    assert second is not None
    assert first["status"] == "invalid"
    assert second["status"] == "draft"
    assert active is not None
    assert active["id"] == roadmap_2


def test_events_alerts_and_scheduler_config(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")

    store.log_event("task_started", "started", project_id=project_id, task_id=task_id)
    events = store.list_events()
    assert len(events) == 1
    assert events[0]["kind"] == "task_started"

    store.create_alert("warn", "check", project_id=project_id, task_id=task_id)
    alerts = store.list_alerts()
    assert len(alerts) == 1
    alert_id = alerts[0]["id"]

    store.ack_alert(alert_id)
    assert len(store.list_alerts()) == 0
    assert len(store.list_alerts(acked=True)) == 1

    config = store.get_scheduler_config()
    assert config["id"] == 1
    assert config["max_global_tasks"] == 5
    assert config["max_per_project"] == 5
    assert config["tick_interval_sec"] == 5
    assert config["task_timeout_min"] == 60

    store.update_scheduler_config(max_global_tasks=7, tick_interval_sec=10)
    updated = store.get_scheduler_config()
    assert updated["max_global_tasks"] == 7
    assert updated["tick_interval_sec"] == 10


def test_delete_roadmap_removes_dependencies(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")
    task_2 = store.create_task(roadmap_id, phase_id, "1.2", "Validate", "**Depends on:** 1.1")

    store.log_event("evt", "msg", project_id=project_id, task_id=task_id)
    store.create_alert("warn", "msg", project_id=project_id, task_id=task_id)
    store._conn.execute(
        "INSERT INTO git_worktrees (task_id, branch, path) VALUES (?, ?, ?)",
        (task_id, "b", "/tmp/wt"),
    )
    store._conn.execute(
        "INSERT INTO task_dependencies (blocked_task_id, blocker_task_id) VALUES (?, ?)",
        (task_2, task_id),
    )
    store._conn.commit()

    assert store.delete_roadmap(roadmap_id) is True
    assert store.get_roadmap(roadmap_id) is None
    assert store.list_phases(roadmap_id) == []
    assert store.list_tasks(project_id=project_id) == []

    events = store.list_events()
    assert len(events) == 1
    assert events[0]["task_id"] is None

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["task_id"] is None

    assert (
        store._conn.execute("SELECT COUNT(*) FROM git_worktrees").fetchone()[0]
        == 0
    )
    assert (
        store._conn.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]
        == 0
    )
    assert store.delete_roadmap(roadmap_id) is False


def test_edit_roadmap_in_place_inserts_task_for_executing_phase(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    raw = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
### Task 1.2: Build core
Implement core
### Task 1.3: Add tests
Add baseline tests
""".strip()
    roadmap_id = store.create_roadmap(project_id, raw)
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Setup", "Initial setup")
    task_12 = store.create_task(roadmap_id, phase_id, "1.2", "Build core", "Implement core")
    task_13 = store.create_task(roadmap_id, phase_id, "1.3", "Add tests", "Add baseline tests")

    store.update_roadmap_status(roadmap_id, "executing")
    store.update_phase_status(phase_id, "executing")
    store.complete_task(task_11, "done")
    store.queue_task(task_12)

    edited_raw = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
### Task 1.2: Build core
Implement core
### Task 1.3: Implement migration
Create migration helpers before tests
### Task 1.4: Add tests
Add baseline tests
""".strip()
    edited = parse_roadmap(edited_raw)

    stats = store.edit_roadmap_in_place(roadmap_id, edited_raw, edited)
    assert stats["tasks_created"] == 1
    assert stats["tasks_updated"] == 1
    assert stats["tasks_deleted"] == 0
    assert stats["tasks_queued"] == 1

    roadmap = store.get_roadmap(roadmap_id)
    assert roadmap is not None
    assert "Implement migration" in roadmap["raw_md"]

    tasks = store.list_tasks_by_phase(phase_id)
    by_number = {task["task_number"]: task for task in tasks}
    assert [task["task_number"] for task in tasks] == ["1.1", "1.2", "1.3", "1.4"]
    assert by_number["1.1"]["status"] == "done"
    assert by_number["1.2"]["status"] == "queued"
    assert by_number["1.3"]["status"] == "queued"
    assert by_number["1.4"]["status"] == "pending"
    assert by_number["1.1"]["id"] == task_11
    assert by_number["1.2"]["id"] == task_12
    assert by_number["1.4"]["id"] == task_13


def test_edit_roadmap_in_place_rejects_modifying_done_task(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    raw = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
Initial setup
""".strip()
    roadmap_id = store.create_roadmap(project_id, raw)
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Setup", "Initial setup")
    store.complete_task(task_id, "done")

    edited_raw = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup updated
Initial setup
""".strip()
    edited = parse_roadmap(edited_raw)

    with pytest.raises(ValueError, match="Cannot modify task 1.1"):
        store.edit_roadmap_in_place(roadmap_id, edited_raw, edited)


def test_apply_roadmap_dependencies_and_satisfaction(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    raw = """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
**Depends on:** none
Do setup
### Task 1.2: Build
**Depends on:** 1.1
Do build
""".strip()
    roadmap = parse_roadmap(raw)
    roadmap_id = store.create_roadmap(project_id, raw)
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Setup", roadmap.phases[0].tasks[0].description)
    task_12 = store.create_task(roadmap_id, phase_id, "1.2", "Build", roadmap.phases[0].tasks[1].description)

    store.apply_roadmap_dependencies(roadmap_id, roadmap)

    dep_count = store._conn.execute(
        "SELECT COUNT(*) FROM task_dependencies WHERE blocked_task_id = ? AND blocker_task_id = ?",
        (task_12, task_11),
    ).fetchone()[0]
    assert dep_count == 1

    assert store.are_task_dependencies_satisfied(task_12) is False
    store.complete_task(task_11, "done")
    assert store.are_task_dependencies_satisfied(task_12) is True


def test_set_roadmap_integration_branch_persists(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    store.set_roadmap_integration_branch(roadmap_id, "yeehaw/roadmap-123")
    roadmap = store.get_roadmap(roadmap_id)
    assert roadmap is not None
    assert roadmap["integration_branch"] == "yeehaw/roadmap-123"


def test_hook_run_persistence_and_filters(store: Store) -> None:
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Foundation", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Build", "desc")

    first_id = store.create_hook_run(
        project_id=project_id,
        roadmap_id=roadmap_id,
        phase_id=phase_id,
        task_id=task_id,
        event_name="pre_dispatch",
        event_id="event-1",
        hook_name="notify",
        status="ok",
        duration_ms=12,
        summary="queued for dispatch",
    )
    second_id = store.create_hook_run(
        project_id=project_id,
        roadmap_id=roadmap_id,
        phase_id=phase_id,
        task_id=task_id,
        event_name="on_fail",
        event_id="event-2",
        hook_name="notify",
        status="failed",
        duration_ms=44,
        summary="merge conflict",
        error="Hook process exited with 1",
        returncode=1,
    )

    first = store.get_hook_run(first_id)
    assert first is not None
    assert first["status"] == "ok"
    assert first["duration_ms"] == 12
    assert first["summary"] == "queued for dispatch"

    all_runs = store.list_hook_runs(limit=10, task_id=task_id)
    assert [row["id"] for row in all_runs] == [second_id, first_id]

    failure_runs = store.list_hook_runs(limit=10, event_name="on_fail")
    assert len(failure_runs) == 1
    assert failure_runs[0]["id"] == second_id
    assert failure_runs[0]["status"] == "failed"
    assert failure_runs[0]["duration_ms"] == 44
    assert failure_runs[0]["summary"] == "merge conflict"
    assert failure_runs[0]["returncode"] == 1
