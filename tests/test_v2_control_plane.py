from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw_v2.config import ControlPlaneConfig
from yeehaw_v2.control_plane import ControlPlane
from yeehaw_v2.db import connect as connect_db
from yeehaw_v2.models import RuntimeKind, SessionHandle
from yeehaw_v2.store import (
    add_dispatcher_decision,
    create_project,
    create_task,
    create_task_batch,
)


class _FakeRuntime:
    def __init__(self, outputs: list[str] | None = None, alive: bool = True) -> None:
        self.outputs = outputs or []
        self.alive = alive
        self.started = []
        self.terminated: list[SessionHandle] = []
        self.inputs: list[tuple[str, str]] = []
        self._output_idx = 0

    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.LOCAL_PTY

    def start_session(self, spec):
        self.started.append(spec)
        return SessionHandle(
            runtime_kind=spec.runtime_kind,
            session_id=f"fake-{spec.task_id}",
            target=f"fake-target-{spec.task_id}",
            pid=1234,
        )

    def send_user_input(self, handle: SessionHandle, text: str) -> None:  # pragma: no cover - not used
        self.inputs.append((handle.session_id, text))
        return None

    def capture_output(self, handle: SessionHandle, lines: int = 400) -> str:
        if not self.outputs:
            return ""
        idx = min(self._output_idx, len(self.outputs) - 1)
        self._output_idx += 1
        return self.outputs[idx]

    def is_session_alive(self, handle: SessionHandle) -> bool:
        return self.alive

    def terminate_session(self, handle: SessionHandle) -> None:  # pragma: no cover - not used
        self.terminated.append(handle)
        return None


class _StartFailRuntime(_FakeRuntime):
    def start_session(self, spec):
        raise RuntimeError("agent failed to start")


def _db_path(tmp_path: Path, name: str) -> Path:
    return tmp_path / name


def test_control_plane_applies_latest_dispatcher_decision_before_dispatch(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_dispatcher.db")
    conn = connect_db(db_path)

    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Implement task",
        runtime_kind=RuntimeKind.TMUX,
    )
    add_dispatcher_decision(
        conn,
        proposal={"runtime_kind": "local_pty", "preferred_agent": "fake-agent", "priority": 77},
        task_id=task_id,
        rationale="prefer local pty",
        confidence=0.9,
    )

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }

    stats = cp.tick()
    assert stats.dispatched == 1
    assert len(fake_runtime.started) == 1
    assert fake_runtime.started[0].command == "fake-agent"
    assert fake_runtime.started[0].runtime_kind == RuntimeKind.LOCAL_PTY

    task_row = conn.execute(
        "SELECT status, runtime_kind, preferred_agent, assigned_agent, priority FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert task_row is not None
    assert task_row["status"] == "running"
    assert task_row["runtime_kind"] == "local_pty"
    assert task_row["preferred_agent"] == "fake-agent"
    assert task_row["assigned_agent"] == "fake-agent"
    assert int(task_row["priority"]) == 77

    decision_row = conn.execute(
        "SELECT applied, overridden FROM dispatcher_decisions WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert decision_row is not None
    assert int(decision_row["applied"]) == 1
    assert int(decision_row["overridden"]) == 0


def test_control_plane_ingests_runtime_usage_as_deltas(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_usage.db")
    conn = connect_db(db_path)

    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Track usage",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (task_id,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-1', 'sid-1', 'active')
        """,
        (task_id, project_id),
    )
    conn.commit()

    fake_runtime = _FakeRuntime(
        outputs=[
            "provider=openai model=gpt-5 input tokens: 100 output tokens: 20 cost_usd=0.50",
            "provider=openai model=gpt-5 input tokens: 120 output tokens: 45 cost_usd=0.70",
            "provider=openai model=gpt-5 input tokens: 120 output tokens: 45 cost_usd=0.70",
        ],
        alive=True,
    )
    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }

    cp.tick()
    cp.tick()
    cp.tick()

    rows = conn.execute(
        """
        SELECT provider, model, input_tokens, output_tokens, cost_usd, source
        FROM usage_records
        ORDER BY id ASC
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["provider"] == "openai"
    assert rows[0]["model"] == "gpt-5"
    assert int(rows[0]["input_tokens"]) == 100
    assert int(rows[0]["output_tokens"]) == 20
    assert float(rows[0]["cost_usd"]) == 0.5
    assert rows[0]["source"] == "runtime_parse"
    assert int(rows[1]["input_tokens"]) == 20
    assert int(rows[1]["output_tokens"]) == 25
    assert float(rows[1]["cost_usd"]) == pytest.approx(0.2)


def test_control_plane_batch_pause_resume_preempt(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_batch_controls.db")
    conn = connect_db(db_path)

    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    running_task = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Running task",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    queued_task = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Queued task",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (running_task,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-running', 'sid-running', 'active')
        """,
        (running_task, project_id),
    )
    conn.commit()

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }

    paused = cp.pause_batch(batch_id)
    assert paused.task_rows_changed == 2
    assert paused.sessions_ended == 1
    assert len(fake_runtime.terminated) == 1
    statuses = conn.execute("SELECT status FROM tasks WHERE batch_id = ? ORDER BY id ASC", (batch_id,)).fetchall()
    assert [str(row["status"]) for row in statuses] == ["paused", "paused"]
    sess_status = conn.execute(
        "SELECT status FROM agent_sessions WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (running_task,),
    ).fetchone()
    assert sess_status is not None
    assert sess_status["status"] == "ended"

    resumed = cp.resume_batch(batch_id)
    assert resumed.task_rows_changed == 2
    statuses_after_resume = conn.execute("SELECT status FROM tasks WHERE batch_id = ? ORDER BY id ASC", (batch_id,)).fetchall()
    assert [str(row["status"]) for row in statuses_after_resume] == ["queued", "queued"]

    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (running_task,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-running-2', 'sid-running-2', 'active')
        """,
        (running_task, project_id),
    )
    conn.commit()

    preempted = cp.preempt_batch(batch_id)
    assert preempted.task_rows_changed == 1
    assert preempted.sessions_ended == 1
    row_running = conn.execute("SELECT status FROM tasks WHERE id = ?", (running_task,)).fetchone()
    row_queued = conn.execute("SELECT status FROM tasks WHERE id = ?", (queued_task,)).fetchone()
    assert row_running is not None and row_running["status"] == "queued"
    assert row_queued is not None and row_queued["status"] == "queued"


def test_control_plane_stuck_interactive_trap_requeues_task(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_stuck_interactive.db")
    conn = connect_db(db_path)

    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Needs operator input",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    conn.execute("UPDATE tasks SET status = 'running', attempt_count = 1 WHERE id = ?", (task_id,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-stuck', 'sid-stuck', 'active')
        """,
        (task_id, project_id),
    )
    conn.execute(
        "UPDATE scheduler_config SET max_global_sessions = 0, max_project_sessions = 0, auto_reassign = 1 WHERE id = 1"
    )
    conn.commit()

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(outputs=["[sudo] password for escoubas: "], alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }

    stats = cp.tick()
    assert stats.stuck == 1
    row = conn.execute("SELECT status, blocked_question FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert "interactive command trap" in str(row["blocked_question"])
    alert = conn.execute(
        "SELECT kind, status FROM alerts WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert alert is not None
    assert alert["kind"] == "task_stuck"
    assert alert["status"] == "open"
    session = conn.execute(
        "SELECT status FROM agent_sessions WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert session is not None
    assert session["status"] == "ended"


def test_control_plane_stuck_marks_failed_at_attempt_limit(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_stuck_failed.db")
    conn = connect_db(db_path)

    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Fail after retries",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    conn.execute("UPDATE tasks SET status = 'running', attempt_count = 3 WHERE id = ?", (task_id,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-fail', 'sid-fail', 'active')
        """,
        (task_id, project_id),
    )
    conn.execute(
        "UPDATE scheduler_config SET max_global_sessions = 0, max_project_sessions = 0, auto_reassign = 1 WHERE id = 1"
    )
    conn.commit()

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(outputs=["continue? [y/N]"], alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }

    stats = cp.tick()
    assert stats.stuck == 1
    assert stats.failed == 1
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row is not None
    assert row["status"] == "failed"


def test_control_plane_reply_to_task_sends_input_and_resumes(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_reply_task.db")
    conn = connect_db(db_path)
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(
        conn,
        batch_id=batch_id,
        project_id=project_id,
        title="Blocked task",
        runtime_kind=RuntimeKind.LOCAL_PTY,
    )
    conn.execute("UPDATE tasks SET status = 'awaiting_input', blocked_question = 'need choice' WHERE id = ?", (task_id,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'local_pty', 'sid-reply', 'sid-reply', 'paused')
        """,
        (task_id, project_id),
    )
    conn.commit()

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }
    cp.reply_to_task(task_id, "continue with option a")
    assert fake_runtime.inputs == [("sid-reply", "continue with option a")]

    task_row = conn.execute("SELECT status, blocked_question FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert task_row is not None
    assert task_row["status"] == "running"
    assert task_row["blocked_question"] is None
    session_row = conn.execute(
        "SELECT status FROM agent_sessions WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert session_row is not None
    assert session_row["status"] == "active"
    op_message = conn.execute(
        "SELECT direction, body FROM operator_messages ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert op_message is not None
    assert op_message["direction"] == "to_agent"
    assert op_message["body"] == "continue with option a"


def test_control_plane_dispatch_persists_worktree_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _db_path(tmp_path, "v2_dispatch_worktree.db")
    conn = connect_db(db_path)
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(conn, batch_id=batch_id, project_id=project_id, title="Task", runtime_kind=RuntimeKind.LOCAL_PTY)

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fake_runtime = _FakeRuntime(alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fake_runtime,
        RuntimeKind.TMUX: fake_runtime,
    }
    prepared_path = (tmp_path / "prepared-wt").resolve()
    prepared_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cp,
        "_prepare_task_worktree",
        lambda *args, **kwargs: (prepared_path, "yeehaw/demo/b1-t1-a1", "deadbeef"),
    )

    stats = cp.tick()
    assert stats.dispatched == 1
    row = conn.execute("SELECT branch_name, worktree_path, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row is not None
    assert row["status"] == "running"
    assert row["branch_name"] == "yeehaw/demo/b1-t1-a1"
    assert Path(str(row["worktree_path"])) == prepared_path
    wt = conn.execute("SELECT path, branch_name, base_sha, state FROM git_worktrees WHERE task_id = ?", (task_id,)).fetchone()
    assert wt is not None
    assert wt["path"] == str(prepared_path)
    assert wt["branch_name"] == "yeehaw/demo/b1-t1-a1"
    assert wt["base_sha"] == "deadbeef"
    assert wt["state"] == "active"


def test_control_plane_dispatch_failure_marks_task_failed(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path, "v2_dispatch_failed.db")
    conn = connect_db(db_path)
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(conn, batch_id=batch_id, project_id=project_id, title="Task", runtime_kind=RuntimeKind.LOCAL_PTY)

    cp = ControlPlane(ControlPlaneConfig(db_path=db_path, poll_seconds=0.01))
    fail_runtime = _StartFailRuntime(alive=True)
    cp.runtimes._adapters = {  # type: ignore[attr-defined]
        RuntimeKind.LOCAL_PTY: fail_runtime,
        RuntimeKind.TMUX: fail_runtime,
    }
    stats = cp.tick()
    assert stats.dispatched == 0
    assert stats.failed == 1
    row = conn.execute("SELECT status, blocked_question FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert "dispatch failed" in str(row["blocked_question"])
