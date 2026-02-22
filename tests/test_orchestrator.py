"""Tests for orchestrator dispatch, retries, and phase progression."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import yeehaw.orchestrator.engine as engine
from yeehaw.orchestrator.engine import Orchestrator
from yeehaw.store.store import Store


@pytest.fixture
def orchestrator_store(tmp_path: Path) -> tuple[Store, Path]:
    """Create a temporary store + repo root for orchestrator tests."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = Store(repo_root / ".yeehaw" / "yeehaw.db")
    try:
        yield store, repo_root
    finally:
        store.close()


def _seed_single_task(store: Store, status: str = "pending") -> dict[str, int]:
    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    if status == "queued":
        store.queue_task(task_id)
    return {
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "phase_id": phase_id,
        "task_id": task_id,
    }


def test_dispatch_queued_launches_task(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    launched: dict[str, Any] = {}

    def fake_prepare_worktree(_repo_root: Path, _branch: str) -> Path:
        worktree = repo_root / "worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        return worktree

    def fake_launch_agent(session: str, working_dir: str, command: str) -> None:
        launched["session"] = session
        launched["working_dir"] = working_dir
        launched["command"] = command
    piped: dict[str, str] = {}
    def fake_pipe_output(session: str, log_path: str) -> None:
        piped["session"] = session
        piped["log_path"] = log_path

    monkeypatch.setattr(engine, "prepare_worktree", fake_prepare_worktree)
    monkeypatch.setattr(engine, "launch_agent", fake_launch_agent)
    monkeypatch.setattr(engine, "pipe_output", fake_pipe_output)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "in-progress"
    assert task["attempts"] == 1
    assert task["assigned_agent"] == "claude"
    assert launched["session"] == f"yeehaw-task-{ids['task_id']}"
    assert piped["session"] == f"yeehaw-task-{ids['task_id']}"
    assert f".yeehaw/logs/task-{ids['task_id']}/attempt-01-claude.log" in piped["log_path"]

    events = store.list_events()
    assert events[0]["kind"] == "task_launched"
    assert "log:" in events[0]["message"]


def test_dispatch_queued_uses_default_agent_override(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root, default_agent="codex")
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["assigned_agent"] == "codex"


def test_dispatch_queued_applies_worker_runtime_config(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    config_path = repo_root / ".yeehaw" / "workers.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "extra_args": ["--global-flag"],
                "env": {"GLOBAL_ENV": "yes"},
                "agents": {
                    "claude": {
                        "disable_default_mcp": False,
                        "extra_args": ["--agent-flag"],
                        "env": {"AGENT_ENV": "yes"},
                    }
                },
            }
        )
    )

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    captured: dict[str, Any] = {}

    def fake_write_launcher(
        script_path: Path,
        profile: Any,
        prompt: str,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        captured["script_path"] = script_path
        captured["profile_name"] = profile.name
        captured["prompt"] = prompt
        captured["extra_args"] = extra_args or []
        captured["env"] = env or {}

    monkeypatch.setattr(engine, "write_launcher", fake_write_launcher)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._dispatch_queued(project_id=None)

    assert captured["profile_name"] == "claude"
    assert captured["extra_args"] == ["--global-flag", "--agent-flag"]
    assert captured["env"]["GLOBAL_ENV"] == "yes"
    assert captured["env"]["AGENT_ENV"] == "yes"
    prompt_file = Path(captured["env"]["YEEHAW_TASK_PROMPT_FILE"])
    assert prompt_file.exists()
    assert "# Task 1.1: Task 1" in prompt_file.read_text()
    assert "Persistent Task Context" in captured["prompt"]


def test_dispatch_queued_disables_default_mcp_by_default(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    _seed_single_task(store, status="queued")

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    captured: dict[str, Any] = {}

    def fake_write_launcher(
        _script_path: Path,
        _profile: Any,
        _prompt: str,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        captured["extra_args"] = extra_args or []
        captured["env"] = env or {}

    monkeypatch.setattr(engine, "write_launcher", fake_write_launcher)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._dispatch_queued(project_id=None)

    assert "--strict-mcp-config" in captured["extra_args"]
    assert "--mcp-config" in captured["extra_args"]
    assert "YEEHAW_TASK_PROMPT_FILE" in captured["env"]


def test_dispatch_queued_invalid_worker_config_fails_task(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    cfg_path = repo_root / ".yeehaw" / "workers.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{not-json")

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "failed"

    alerts = store.list_alerts()
    assert any("Failed to launch task" in alert["message"] for alert in alerts)


def test_process_signal_done_completes_task(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    worktree = repo_root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    store.assign_task(
        ids["task_id"],
        agent="codex",
        branch="b",
        worktree=str(worktree),
        signal_dir=str(signal_dir),
    )

    signal_file = signal_dir / "signal.json"
    signal_file.write_text(
        json.dumps({"task_id": ids["task_id"], "status": "done", "summary": "ok"}),
    )

    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda _repo_root, _worktree: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_run_verification", lambda _task: True)
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "done"

    roadmap = store.get_roadmap(ids["roadmap_id"])
    assert roadmap is not None
    assert roadmap["status"] == "completed"

    events = store.list_events(limit=10)
    kinds = [event["kind"] for event in events]
    assert "task_done" in kinds


def test_process_signal_failed_queues_retry(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    worktree = repo_root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    store.assign_task(
        ids["task_id"],
        agent="codex",
        branch="b",
        worktree=str(worktree),
        signal_dir=str(signal_dir),
    )

    signal_file = signal_dir / "signal.json"
    signal_file.write_text(
        json.dumps(
            {
                "task_id": ids["task_id"],
                "status": "failed",
                "summary": "compilation error",
            },
        ),
    )

    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda _repo_root, _worktree: None)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "queued"

    events = store.list_events(limit=10)
    assert any(event["kind"] == "task_retry" for event in events)


def test_process_signal_failed_exhausted_retries_creates_alert(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    worktree = repo_root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    store.assign_task(
        ids["task_id"],
        agent="codex",
        branch="b",
        worktree=str(worktree),
        signal_dir=str(signal_dir),
    )
    store._conn.execute(
        "UPDATE tasks SET attempts = 1, max_attempts = 1 WHERE id = ?",
        (ids["task_id"],),
    )
    store._conn.commit()

    signal_file = signal_dir / "signal.json"
    signal_file.write_text(
        json.dumps(
            {
                "task_id": ids["task_id"],
                "status": "failed",
                "summary": "error",
            },
        ),
    )

    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda _repo_root, _worktree: None)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "failed"

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert "exhausted" in alerts[0]["message"]


def test_check_phase_completion_queues_next_phase(
    orchestrator_store: tuple[Store, Path],
) -> None:
    store, repo_root = orchestrator_store

    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_1 = store.create_phase(roadmap_id, 1, "Phase 1", None)
    phase_2 = store.create_phase(roadmap_id, 2, "Phase 2", None)

    task_1 = store.create_task(roadmap_id, phase_1, "1.1", "Task 1", "desc")
    task_2 = store.create_task(roadmap_id, phase_2, "2.1", "Task 2", "desc")

    store.complete_task(task_1, "done")

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._check_phase_completion(phase_1)

    phase_1_row = store.get_phase(phase_1)
    phase_2_row = store.get_phase(phase_2)
    next_task = store.get_task(task_2)

    assert phase_1_row is not None
    assert phase_2_row is not None
    assert next_task is not None

    assert phase_1_row["status"] == "completed"
    assert phase_2_row["status"] == "executing"
    assert next_task["status"] == "queued"


def test_is_timed_out_true_for_old_started_at(orchestrator_store: tuple[Store, Path]) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    worktree = repo_root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    store.assign_task(
        ids["task_id"],
        agent="codex",
        branch="b",
        worktree=str(worktree),
        signal_dir=str(signal_dir),
    )

    old_start = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    store._conn.execute(
        "UPDATE tasks SET started_at = ? WHERE id = ?",
        (old_start, ids["task_id"]),
    )
    store._conn.commit()

    orchestrator = Orchestrator(store, repo_root)
    task = store.get_task(ids["task_id"])
    assert task is not None
    assert orchestrator._is_timed_out(task) is True


def test_handle_timeout_records_log_hints(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    worktree = repo_root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    store.assign_task(
        ids["task_id"],
        agent="claude",
        branch="b",
        worktree=str(worktree),
        signal_dir=str(signal_dir),
    )
    task = store.get_task(ids["task_id"])
    assert task is not None

    log_dir = repo_root / ".yeehaw" / "logs" / f"task-{ids['task_id']}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "attempt-01-claude.log").write_text("agent output")

    monkeypatch.setattr(engine, "capture_pane", lambda _session: "pane output")
    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._handle_timeout(task, "yeehaw-task-1")

    updated = store.get_task(ids["task_id"])
    assert updated is not None
    assert updated["status"] == "queued"
    assert "Task timed out" in (updated["last_failure"] or "")
    assert "Check log:" in (updated["last_failure"] or "")
    assert "Pane snapshot:" in (updated["last_failure"] or "")
