"""Tests for sqlite store operations."""

from __future__ import annotations

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

    store.update_scheduler_config(max_global_tasks=7, tick_interval_sec=10)
    updated = store.get_scheduler_config()
    assert updated["max_global_tasks"] == 7
    assert updated["tick_interval_sec"] == 10
