"""Tests for orchestrator dispatch, retries, and phase progression."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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


def _init_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Yeehaw Test"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "yeehaw@example.com"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo_root, check=True, capture_output=True)


def _write_default_policy(repo_root: Path, payload: dict[str, Any]) -> None:
    policy_path = repo_root / ".yeehaw" / "policies" / "default.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(payload))


def _hook_definition_for_events(
    repo_root: Path,
    *,
    name: str,
    events: tuple[str, ...],
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        entrypoint=repo_root / f"{name}.sh",
        events=events,
        source="runtime",
        metadata_path=repo_root / f"{name}.json",
    )


def test_dispatch_queued_launches_task(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    launched: dict[str, Any] = {}
    prepare_calls: list[tuple[Path, str]] = []

    def fake_prepare_worktree(
        _repo_root: Path,
        _runtime_root: Path,
        _branch: str,
        base_ref: str = "HEAD",
    ) -> Path:
        _ = base_ref
        prepare_calls.append((_repo_root, _branch))
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
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "in-progress"
    assert task["attempts"] == 1
    assert task["assigned_agent"] == "claude"
    assert launched["session"] == f"yeehaw-task-{ids['task_id']}"
    assert piped["session"] == f"yeehaw-task-{ids['task_id']}"
    assert f".yeehaw/logs/task-{ids['task_id']}/attempt-01-claude.log" in piped["log_path"]
    assert prepare_calls
    assert prepare_calls[0][0] == Path("/tmp/repo")

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
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["assigned_agent"] == "codex"


def test_dispatch_queued_clears_stale_signal_file(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{ids['task_id']}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    stale_signal = signal_dir / "signal.json"
    stale_signal.write_text(json.dumps({"task_id": ids["task_id"], "status": "done"}))

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    assert stale_signal.exists() is False


def test_dispatch_queued_reuses_existing_worktree_path(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    existing_worktree = repo_root / "existing-worktree"
    existing_worktree.mkdir(parents=True, exist_ok=True)
    store._conn.execute(
        "UPDATE tasks SET branch_name = ?, worktree_path = ?, attempts = 1 WHERE id = ?",
        ("yeehaw/task-1.1-task-1", str(existing_worktree), ids["task_id"]),
    )
    store._conn.commit()

    prepare_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        engine,
        "prepare_worktree",
        lambda repo_root_arg, _runtime_root_arg, branch_arg, **_kwargs: prepare_calls.append(
            (repo_root_arg, branch_arg)
        )
        or repo_root,
    )

    launched: dict[str, str] = {}
    monkeypatch.setattr(
        engine,
        "launch_agent",
        lambda _session, working_dir, _command: launched.setdefault("working_dir", working_dir),
    )
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "in-progress"
    assert task["attempts"] == 2
    assert prepare_calls == []
    assert launched["working_dir"] == str(existing_worktree)


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
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
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
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
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
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "failed"

    alerts = store.list_alerts()
    assert any("Failed to launch task" in alert["message"] for alert in alerts)


def test_dispatch_queued_holds_overlapping_file_targets(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store

    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    task_12 = store.create_task(roadmap_id, phase_id, "1.2", "Task 2", "desc")
    store.set_task_file_targets(task_11, ["src/conflict.py"])
    store.set_task_file_targets(task_12, ["src/conflict.py"])
    store.queue_task(task_11)
    store.queue_task(task_12)

    launched: list[str] = []
    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(
        engine,
        "launch_agent",
        lambda session, _working_dir, _command: launched.append(session),
    )
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    first = store.get_task(task_11)
    second = store.get_task(task_12)
    assert first is not None
    assert second is not None
    assert first["status"] == "in-progress"
    assert second["status"] == "queued"
    assert launched == [f"yeehaw-task-{task_11}"]


def test_dispatch_queued_allows_non_overlapping_file_targets(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store

    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    task_12 = store.create_task(roadmap_id, phase_id, "1.2", "Task 2", "desc")
    store.set_task_file_targets(task_11, ["src/a.py"])
    store.set_task_file_targets(task_12, ["src/b.py"])
    store.queue_task(task_11)
    store.queue_task(task_12)

    launched: list[str] = []
    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(
        engine,
        "launch_agent",
        lambda session, _working_dir, _command: launched.append(session),
    )
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    first = store.get_task(task_11)
    second = store.get_task(task_12)
    assert first is not None
    assert second is not None
    assert first["status"] == "in-progress"
    assert second["status"] == "in-progress"
    assert launched == [f"yeehaw-task-{task_11}", f"yeehaw-task-{task_12}"]


def test_dispatch_queued_allows_explicit_safe_overlap(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store

    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    task_12 = store.create_task(
        roadmap_id,
        phase_id,
        "1.2",
        "Task 2",
        "**Safe to overlap:** yes",
    )
    store.set_task_file_targets(task_11, ["src/conflict.py"])
    store.set_task_file_targets(task_12, ["src/conflict.py"])
    store.queue_task(task_11)
    store.queue_task(task_12)

    launched: list[str] = []
    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(
        engine,
        "launch_agent",
        lambda session, _working_dir, _command: launched.append(session),
    )
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    first = store.get_task(task_11)
    second = store.get_task(task_12)
    assert first is not None
    assert second is not None
    assert first["status"] == "in-progress"
    assert second["status"] == "in-progress"
    assert launched == [f"yeehaw-task-{task_11}", f"yeehaw-task-{task_12}"]


def test_dispatch_queued_skips_tasks_with_unsatisfied_dependencies(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store

    project_id = store.create_project("proj-a", "/tmp/repo")
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_11 = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    task_12 = store.create_task(roadmap_id, phase_id, "1.2", "Task 2", "**Depends on:** 1.1")
    store.queue_task(task_11)
    store.queue_task(task_12)
    store._conn.execute(
        "INSERT INTO task_dependencies (blocked_task_id, blocker_task_id) VALUES (?, ?)",
        (task_12, task_11),
    )
    store._conn.commit()

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_ensure_integration_branch",
        lambda _task: "yeehaw/roadmap-1",
    )
    orchestrator._dispatch_queued(project_id=None)

    first = store.get_task(task_11)
    second = store.get_task(task_12)
    assert first is not None
    assert second is not None
    assert first["status"] == "in-progress"
    assert second["status"] == "queued"


def test_dispatch_queued_creates_integration_branch_and_uses_as_base(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    _init_git_repo(repo_root)

    project_id = store.create_project("proj-a", str(repo_root))
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")
    store.queue_task(task_id)

    captured: dict[str, Any] = {}

    def fake_prepare(
        _repo_root: Path,
        _runtime_root: Path,
        _branch: str,
        base_ref: str = "HEAD",
    ) -> Path:
        captured["base_ref"] = base_ref
        return repo_root

    monkeypatch.setattr(engine, "prepare_worktree", fake_prepare)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    orchestrator._dispatch_queued(project_id=None)

    roadmap = store.get_roadmap(roadmap_id)
    assert roadmap is not None
    assert roadmap["integration_branch"] == f"yeehaw/roadmap-{roadmap_id}"
    assert captured["base_ref"] == f"yeehaw/roadmap-{roadmap_id}"


def test_hook_events_execute_in_lifecycle_order(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    def fake_prepare_worktree(
        _repo_root: Path,
        _runtime_root: Path,
        _branch: str,
        base_ref: str = "HEAD",
    ) -> Path:
        _ = base_ref
        worktree = repo_root / "worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        return worktree

    monkeypatch.setattr(engine, "prepare_worktree", fake_prepare_worktree)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda *_args, **_kwargs: None)

    hooks = [
        _hook_definition_for_events(
            repo_root,
            name="observer",
            events=(
                "pre_dispatch",
                "post_dispatch",
                "pre_merge",
                "post_merge",
                "on_phase_complete",
                "on_roadmap_complete",
            ),
        ),
    ]
    observed_events: list[str] = []

    def fake_run_hooks(
        subscribed_hooks: list[SimpleNamespace],
        request: Any,
        **_kwargs: Any,
    ) -> list[SimpleNamespace]:
        observed_events.append(request.event_name)
        results: list[SimpleNamespace] = []
        for hook in subscribed_hooks:
            results.append(
                SimpleNamespace(
                    hook=hook,
                    request=request,
                    response=SimpleNamespace(
                        schema_version=request.schema_version,
                        event_id=request.event_id,
                        extension=hook.name,
                        status="ok",
                        summary=f"{request.event_name} handled",
                        actions=(),
                        metrics={},
                    ),
                    error=None,
                    returncode=0,
                    duration_ms=8,
                    stdout="",
                    stderr="",
                )
            )
        return results

    monkeypatch.setattr(engine, "load_hooks", lambda runtime_root: hooks)
    monkeypatch.setattr(engine, "run_hooks", fake_run_hooks)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_ensure_integration_branch", lambda _task: "yeehaw/roadmap-1")
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    signal_dir = Path(str(task["signal_dir"]))
    signal_file = signal_dir / "signal.json"
    signal_file.write_text(json.dumps({"task_id": ids["task_id"], "status": "done", "summary": "ok"}))

    monkeypatch.setattr(orchestrator, "_validate_done_signal_worktree", lambda _task: None)
    monkeypatch.setattr(orchestrator, "_merge_done_task_branch", lambda _task: None)
    orchestrator._process_signal_file(signal_file)

    assert observed_events == [
        "pre_dispatch",
        "post_dispatch",
        "pre_merge",
        "post_merge",
        "on_phase_complete",
        "on_roadmap_complete",
    ]

    hook_event_rows = store._conn.execute(
        "SELECT event_name FROM hook_runs ORDER BY id ASC",
    ).fetchall()
    assert [str(row[0]) for row in hook_event_rows] == observed_events

    persisted_rows = store._conn.execute(
        "SELECT status, duration_ms, summary FROM hook_runs ORDER BY id ASC",
    ).fetchall()
    assert all(str(row[0]) == "ok" for row in persisted_rows)
    assert all(int(row[1]) == 8 for row in persisted_rows)
    assert all(str(row[2]).endswith("handled") for row in persisted_rows)


def test_hook_failure_persists_diagnostics_and_remains_fail_open(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store, status="queued")

    monkeypatch.setattr(engine, "prepare_worktree", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(engine, "launch_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "pipe_output", lambda *_args, **_kwargs: None)

    hooks = [
        _hook_definition_for_events(repo_root, name="failing-hook", events=("pre_dispatch",)),
    ]

    def fake_run_hooks(
        subscribed_hooks: list[SimpleNamespace],
        request: Any,
        **_kwargs: Any,
    ) -> list[SimpleNamespace]:
        hook = subscribed_hooks[0]
        error = RuntimeError(
            f"Hook '{hook.name}' exited with code 1 for event '{request.event_name}'",
        )
        return [
            SimpleNamespace(
                hook=hook,
                request=request,
                response=None,
                error=error,
                returncode=1,
                duration_ms=17,
                stdout="",
                stderr="boom",
            )
        ]

    monkeypatch.setattr(engine, "load_hooks", lambda runtime_root: hooks)
    monkeypatch.setattr(engine, "run_hooks", fake_run_hooks)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_ensure_integration_branch", lambda _task: "yeehaw/roadmap-1")
    orchestrator._dispatch_queued(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "in-progress"

    hook_runs = store.list_hook_runs(limit=10)
    assert len(hook_runs) == 1
    assert hook_runs[0]["event_name"] == "pre_dispatch"
    assert hook_runs[0]["hook_name"] == "failing-hook"
    assert hook_runs[0]["status"] == "failed"
    assert hook_runs[0]["duration_ms"] == 17
    assert "exited with code 1" in str(hook_runs[0]["summary"] or "")

    events = store.list_events(limit=10)
    assert any(event["kind"] == "hook_invocation_failed" for event in events)

    alerts = store.list_alerts()
    assert any("failing-hook" in alert["message"] for alert in alerts)


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

    verify_called = {"called": False}

    def fake_verify(_task: dict[str, Any]) -> bool:
        verify_called["called"] = True
        return True

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_validate_done_signal_worktree", lambda _task: None)
    monkeypatch.setattr(orchestrator, "_merge_done_task_branch", lambda _task: None)
    monkeypatch.setattr(orchestrator, "_run_verification", fake_verify)
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "done"
    assert verify_called["called"] is False

    roadmap = store.get_roadmap(ids["roadmap_id"])
    assert roadmap is not None
    assert roadmap["status"] == "completed"

    events = store.list_events(limit=10)
    kinds = [event["kind"] for event in events]
    assert "task_done" in kinds


def test_process_signal_done_with_dirty_worktree_queues_retry(
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

    verify_called = {"called": False}

    def fake_verify(_task: dict[str, Any]) -> bool:
        verify_called["called"] = True
        return True

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(
        orchestrator,
        "_validate_done_signal_worktree",
        lambda _task: "Task reported done with uncommitted changes in worktree",
    )
    monkeypatch.setattr(orchestrator, "_run_verification", fake_verify)

    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "queued"
    assert task["last_failure"] == "Task reported done with uncommitted changes in worktree"
    assert verify_called["called"] is False


def test_process_signal_done_with_merge_failure_queues_retry(
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
    monkeypatch.setattr(orchestrator, "_validate_done_signal_worktree", lambda _task: None)
    monkeypatch.setattr(
        orchestrator,
        "_merge_done_task_branch",
        lambda _task: "Failed to merge task branch",
    )

    orchestrator._process_signal_file(signal_file)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "queued"
    assert task["last_failure"] == "Failed to merge task branch"


def test_process_signal_done_policy_violation_blocks_done_accept(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    _init_git_repo(repo_root)

    project_id = store.create_project("proj-a", str(repo_root))
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")

    source_branch = "yeehaw/task-1.1-task-1"
    target_branch = f"yeehaw/roadmap-{roadmap_id}"
    subprocess.run(
        ["git", "branch", target_branch, "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    store.set_roadmap_integration_branch(roadmap_id, target_branch)

    subprocess.run(
        ["git", "checkout", "-b", source_branch, f"refs/heads/{target_branch}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "src").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "task.py").write_text("print('task')\n")
    subprocess.run(["git", "add", "src/task.py"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "non compliant message"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "--detach"], cwd=repo_root, check=True, capture_output=True)

    _write_default_policy(
        repo_root,
        {
            "quality": {
                "required_commit_message_regex": r"^\[task-\d+\.\d+\]\s+.+",
            },
        },
    )

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{task_id}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    store.assign_task(
        task_id,
        agent="codex",
        branch=source_branch,
        worktree=str(repo_root),
        signal_dir=str(signal_dir),
    )
    signal_file = signal_dir / "signal.json"
    signal_file.write_text(json.dumps({"task_id": task_id, "status": "done", "summary": "ok"}))

    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda _repo_root, _worktree: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_validate_done_signal_worktree", lambda _task: None)
    monkeypatch.setattr(
        orchestrator,
        "_merge_done_task_branch",
        lambda _task: (_ for _ in ()).throw(AssertionError("merge should not be attempted")),
    )
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(task_id)
    assert task is not None
    assert task["status"] == "queued"
    assert "Task policy violation at done_accept" in str(task["last_failure"] or "")
    assert "policy.required_commit_message_regex" in str(task["last_failure"] or "")

    events = store.list_events(limit=20)
    policy_events = [event for event in events if event["kind"] == "task_policy_violation"]
    assert policy_events
    assert "done_accept" in policy_events[0]["message"]
    assert "source=yeehaw/task-1.1-task-1" in policy_events[0]["message"]

    alerts = store.list_alerts()
    assert any("done_accept" in alert["message"] for alert in alerts)


def test_process_signal_done_policy_violation_blocks_pre_merge(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    _init_git_repo(repo_root)

    project_id = store.create_project("proj-a", str(repo_root))
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")

    source_branch = "yeehaw/task-1.1-task-1"
    target_branch = f"yeehaw/roadmap-{roadmap_id}"
    subprocess.run(
        ["git", "branch", target_branch, "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    store.set_roadmap_integration_branch(roadmap_id, target_branch)

    subprocess.run(
        ["git", "checkout", "-b", source_branch, f"refs/heads/{target_branch}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "secrets").mkdir(parents=True, exist_ok=True)
    (repo_root / "secrets" / "token.txt").write_text("top-secret\n")
    subprocess.run(
        ["git", "add", "secrets/token.txt"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "allowed commit format"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "--detach"], cwd=repo_root, check=True, capture_output=True)

    _write_default_policy(
        repo_root,
        {
            "quality": {
                "required_commit_message_regex": r"^allowed commit format$",
            },
            "safety": {
                "blocked_paths": ["secrets/*"],
            },
        },
    )

    signal_dir = repo_root / ".yeehaw" / "signals" / f"task-{task_id}"
    signal_dir.mkdir(parents=True, exist_ok=True)
    store.assign_task(
        task_id,
        agent="codex",
        branch=source_branch,
        worktree=str(repo_root),
        signal_dir=str(signal_dir),
    )
    signal_file = signal_dir / "signal.json"
    signal_file.write_text(json.dumps({"task_id": task_id, "status": "done", "summary": "ok"}))

    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda _repo_root, _worktree: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator, "_validate_done_signal_worktree", lambda _task: None)
    orchestrator._process_signal_file(signal_file)

    task = store.get_task(task_id)
    assert task is not None
    assert task["status"] == "queued"
    assert "Task policy violation at pre_merge" in str(task["last_failure"] or "")
    assert "policy.forbidden_path_pattern" in str(task["last_failure"] or "")

    events = store.list_events(limit=20)
    policy_events = [event for event in events if event["kind"] == "task_policy_violation"]
    assert policy_events
    assert any("pre_merge" in event["message"] for event in policy_events)
    assert any("target=yeehaw/roadmap-" in event["message"] for event in policy_events)

    alerts = store.list_alerts()
    assert any("pre_merge" in alert["message"] for alert in alerts)


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
    assert not any(event["kind"] == "task_reconcile_queued" for event in events)

    phase_tasks = store.list_tasks_by_phase(ids["phase_id"])
    assert not any(str(candidate["title"]).startswith("Reconcile ") for candidate in phase_tasks)


def test_process_signal_failed_exhausted_retries_queues_reconcile_task(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)

    blocker_id = store.create_task(ids["roadmap_id"], ids["phase_id"], "1.2", "Prepare", "desc")
    blocked_id = store.create_task(ids["roadmap_id"], ids["phase_id"], "1.3", "Follow-up", "desc")
    store.complete_task(blocker_id, "done")
    store._conn.execute(
        "INSERT INTO task_dependencies (blocked_task_id, blocker_task_id) VALUES (?, ?)",
        (ids["task_id"], blocker_id),
    )
    store._conn.execute(
        "INSERT INTO task_dependencies (blocked_task_id, blocker_task_id) VALUES (?, ?)",
        (blocked_id, ids["task_id"]),
    )
    store._conn.commit()

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
        "UPDATE tasks SET attempts = 1, max_attempts = 1, last_failure = ? WHERE id = ?",
        ("first failure", ids["task_id"]),
    )
    store._conn.commit()

    signal_file = signal_dir / "signal.json"
    signal_file.write_text(
        json.dumps(
            {
                "task_id": ids["task_id"],
                "status": "failed",
                "summary": "second failure",
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

    phase_tasks = store.list_tasks_by_phase(ids["phase_id"])
    reconcile = next(
        (
            candidate
            for candidate in phase_tasks
            if str(candidate["title"]).startswith("Reconcile 1.1 after repeated failures")
        ),
        None,
    )
    assert reconcile is not None
    assert reconcile["status"] == "queued"
    assert "**Reconcile Source Task ID:**" in str(reconcile["description"])
    assert "Failure 1: first failure" in str(reconcile["description"])
    assert "Failure 2: second failure" in str(reconcile["description"])
    assert "Upstream blockers: 1.2 (done) Prepare" in str(reconcile["description"])
    assert "Downstream blocked tasks: 1.3 (pending) Follow-up" in str(reconcile["description"])

    events = store.list_events(limit=20)
    assert any(event["kind"] == "task_reconcile_queued" for event in events)
    assert not any(event["kind"] == "task_retry" for event in events)

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert "queued reconcile task" in alerts[0]["message"]


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


def test_run_verification_prefers_worktree_path(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    ids = _seed_single_task(store)
    store._conn.execute(
        "UPDATE roadmap_phases SET verify_cmd = ? WHERE id = ?",
        ("bash -n run_diagnostics.sh scripts/*.sh", ids["phase_id"]),
    )
    store._conn.commit()

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

    task = store.get_task(ids["task_id"])
    assert task is not None

    run_calls: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    orchestrator = Orchestrator(store, repo_root)
    assert orchestrator._run_verification(task) is True
    assert run_calls["cwd"] == worktree


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


def test_stop_sets_stop_event(orchestrator_store: tuple[Store, Path]) -> None:
    store, repo_root = orchestrator_store
    orchestrator = Orchestrator(store, repo_root)
    orchestrator.running = True

    orchestrator.stop()

    assert orchestrator.running is False
    assert orchestrator._stop_event.is_set() is True


def test_run_uses_stop_event_wait(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    orchestrator = Orchestrator(store, repo_root)

    monkeypatch.setattr(orchestrator, "_write_pid_file", lambda: None)
    monkeypatch.setattr(orchestrator, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(orchestrator, "_remove_pid_file", lambda: None)
    monkeypatch.setattr(orchestrator.signal_watcher, "start", lambda: None)
    monkeypatch.setattr(orchestrator.signal_watcher, "stop", lambda: None)
    monkeypatch.setattr(orchestrator, "_tick", lambda _project_id: None)

    wait_calls: list[int] = []
    monkeypatch.setattr(
        orchestrator._stop_event,
        "wait",
        lambda timeout: wait_calls.append(timeout) or True,
    )

    orchestrator.run(project_id=None)

    assert wait_calls == [orchestrator.config["tick_interval_sec"]]


def test_run_does_not_wait_after_stop_requested_in_tick(
    orchestrator_store: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo_root = orchestrator_store
    orchestrator = Orchestrator(store, repo_root)

    monkeypatch.setattr(orchestrator, "_write_pid_file", lambda: None)
    monkeypatch.setattr(orchestrator, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(orchestrator, "_remove_pid_file", lambda: None)
    monkeypatch.setattr(orchestrator.signal_watcher, "start", lambda: None)
    monkeypatch.setattr(orchestrator.signal_watcher, "stop", lambda: None)
    monkeypatch.setattr(orchestrator, "_tick", lambda _project_id: orchestrator.stop())
    monkeypatch.setattr(
        orchestrator._stop_event,
        "wait",
        lambda _timeout: (_ for _ in ()).throw(AssertionError("wait() should not be called")),
    )

    orchestrator.run(project_id=None)


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


def test_monitor_active_runtime_budget_breach_fails_task_with_alert(
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
    store._conn.execute(
        "UPDATE tasks SET started_at = ?, max_runtime_min = ? WHERE id = ?",
        (
            (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),
            5,
            ids["task_id"],
        ),
    )
    store._conn.commit()

    log_dir = repo_root / ".yeehaw" / "logs" / f"task-{ids['task_id']}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "attempt-01-claude.log").write_text("agent output")

    monkeypatch.setattr(engine, "has_session", lambda _session: True)
    monkeypatch.setattr(engine, "capture_pane", lambda _session: "")
    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator.signal_watcher, "get_ready_signals", lambda: [])
    monkeypatch.setattr(orchestrator.signal_watcher, "poll_signals", lambda: [])
    orchestrator._monitor_active(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "failed"
    assert "Runtime budget exceeded" in str(task["last_failure"] or "")
    assert "limit 5 min" in str(task["last_failure"] or "")
    assert "Check log:" in str(task["last_failure"] or "")

    alerts = store.list_alerts()
    assert any("runtime budget breached" in str(alert["message"]).lower() for alert in alerts)
    events = store.list_events(limit=10)
    assert any(
        event["kind"] == "task_budget_exceeded" and "Runtime budget exceeded" in event["message"]
        for event in events
    )


def test_monitor_active_token_budget_breach_fails_task_with_alert(
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
    assert store.set_task_budget(ids["task_id"], max_tokens=1000, max_runtime_min=None) is True

    log_dir = repo_root / ".yeehaw" / "logs" / f"task-{ids['task_id']}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "attempt-01-claude.log").write_text("Total tokens: 1,500\n")

    monkeypatch.setattr(engine, "has_session", lambda _session: True)
    monkeypatch.setattr(engine, "capture_pane", lambda _session: "")
    monkeypatch.setattr(engine, "kill_session", lambda _session: None)
    monkeypatch.setattr(engine, "cleanup_worktree", lambda *_args, **_kwargs: None)

    orchestrator = Orchestrator(store, repo_root)
    monkeypatch.setattr(orchestrator.signal_watcher, "get_ready_signals", lambda: [])
    monkeypatch.setattr(orchestrator.signal_watcher, "poll_signals", lambda: [])
    orchestrator._monitor_active(project_id=None)

    task = store.get_task(ids["task_id"])
    assert task is not None
    assert task["status"] == "failed"
    assert "Token budget exceeded" in str(task["last_failure"] or "")
    assert "1,500" in str(task["last_failure"] or "")
    assert "1,000" in str(task["last_failure"] or "")
    assert "Check log:" in str(task["last_failure"] or "")

    alerts = store.list_alerts()
    assert any("token budget breached" in str(alert["message"]).lower() for alert in alerts)
    events = store.list_events(limit=10)
    assert any(
        event["kind"] == "task_budget_exceeded" and "Token budget exceeded" in event["message"]
        for event in events
    )


def test_merge_done_task_branch_rebases_then_merges(orchestrator_store: tuple[Store, Path]) -> None:
    store, repo_root = orchestrator_store
    _init_git_repo(repo_root)

    project_id = store.create_project("proj-a", str(repo_root))
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")

    source_branch = "yeehaw/task-1.1-task-1"
    target_branch = f"yeehaw/roadmap-{roadmap_id}"
    subprocess.run(
        ["git", "branch", target_branch, "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    store.set_roadmap_integration_branch(roadmap_id, target_branch)
    store._conn.execute("UPDATE tasks SET branch_name = ? WHERE id = ?", (source_branch, task_id))
    store._conn.commit()

    subprocess.run(
        ["git", "checkout", "-b", source_branch],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "task.txt").write_text("task branch change\n")
    subprocess.run(["git", "add", "task.txt"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "task change"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    subprocess.run(["git", "checkout", target_branch], cwd=repo_root, check=True, capture_output=True)
    (repo_root / "integration.txt").write_text("integration branch change\n")
    subprocess.run(
        ["git", "add", "integration.txt"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "integration change"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    subprocess.run(["git", "checkout", "--detach"], cwd=repo_root, check=True, capture_output=True)

    orchestrator = Orchestrator(store, repo_root)
    task = store.get_task(task_id)
    assert task is not None
    error = orchestrator._merge_done_task_branch(task)
    assert error is None

    source_in_target = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            f"refs/heads/{source_branch}",
            f"refs/heads/{target_branch}",
        ],
        cwd=repo_root,
        capture_output=True,
    )
    target_in_source = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            f"refs/heads/{target_branch}",
            f"refs/heads/{source_branch}",
        ],
        cwd=repo_root,
        capture_output=True,
    )
    assert source_in_target.returncode == 0
    assert target_in_source.returncode == 0

    events = store.list_events(limit=20)
    kinds = [event["kind"] for event in events]
    assert "task_rebased" in kinds
    assert "task_merged" in kinds

    attempts = store.list_task_merge_attempts(task_id=task_id, limit=5)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["source_branch"] == source_branch
    assert attempts[0]["target_branch"] == target_branch
    assert attempts[0]["source_sha_before"] is not None
    assert attempts[0]["source_sha_after"] is not None
    assert attempts[0]["target_sha_before"] is not None
    assert attempts[0]["target_sha_after"] is not None
    assert attempts[0]["conflict_type"] is None
    assert attempts[0]["conflict_files"] == []


def test_merge_done_task_branch_reports_rebase_conflict(orchestrator_store: tuple[Store, Path]) -> None:
    store, repo_root = orchestrator_store
    _init_git_repo(repo_root)

    project_id = store.create_project("proj-a", str(repo_root))
    roadmap_id = store.create_roadmap(project_id, "# Roadmap")
    phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
    task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")

    source_branch = "yeehaw/task-1.1-task-1"
    target_branch = f"yeehaw/roadmap-{roadmap_id}"
    subprocess.run(
        ["git", "branch", target_branch, "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    store.set_roadmap_integration_branch(roadmap_id, target_branch)
    store._conn.execute("UPDATE tasks SET branch_name = ? WHERE id = ?", (source_branch, task_id))
    store._conn.commit()

    subprocess.run(
        ["git", "checkout", target_branch],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "conflict.txt").write_text("integration value\n")
    subprocess.run(["git", "add", "conflict.txt"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "integration conflict change"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", source_branch, f"refs/heads/{target_branch}~1"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    (repo_root / "conflict.txt").write_text("task value\n")
    subprocess.run(["git", "add", "conflict.txt"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "task conflict change"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    subprocess.run(["git", "checkout", "--detach"], cwd=repo_root, check=True, capture_output=True)

    orchestrator = Orchestrator(store, repo_root)
    task = store.get_task(task_id)
    assert task is not None
    error = orchestrator._merge_done_task_branch(task)
    assert error is not None
    assert "Failed to rebase" in error
    assert "content_conflict" in error
    assert "conflict.txt" in error

    attempts = store.list_task_merge_attempts(task_id=task_id, limit=5)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["source_branch"] == source_branch
    assert attempts[0]["target_branch"] == target_branch
    assert attempts[0]["conflict_type"] == "content_conflict"
    assert attempts[0]["conflict_files"] == ["conflict.txt"]
    assert "Failed to rebase" in str(attempts[0]["error_detail"] or "")
