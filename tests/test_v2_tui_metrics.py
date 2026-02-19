from __future__ import annotations

from pathlib import Path

from yeehaw_v2.db import connect as connect_db
from yeehaw_v2.models import RuntimeKind
from yeehaw_v2.store import add_dispatcher_decision, add_usage_record, create_project, create_task, create_task_batch
from yeehaw_v2.tui import (
    _fetch_batch_detail,
    _fetch_batches,
    _fetch_dashboard_signals,
    _fetch_open_alerts,
    _fetch_pending_dispatcher_decisions,
    _fetch_sessions,
    _fetch_session_usage,
    _fetch_tasks,
    _resolve_alert,
    _upsert_project_from_onboarding,
)


def test_fetch_session_usage_aggregates_and_tracks_latest_provider_model(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_tui_usage.db")
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id = create_task_batch(conn, project_id=project_id, name="batch")
    task_id = create_task(conn, batch_id=batch_id, project_id=project_id, title="task", runtime_kind=RuntimeKind.TMUX)

    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'sid-1', 'sid-1', 'active')
        """,
        (task_id, project_id),
    )
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'sid-2', 'sid-2', 'active')
        """,
        (task_id, project_id),
    )
    conn.commit()
    rows = conn.execute("SELECT id FROM agent_sessions ORDER BY id").fetchall()
    sid1 = int(rows[0]["id"])
    sid2 = int(rows[1]["id"])

    add_usage_record(conn, provider="openai", model="gpt-5", input_tokens=100, output_tokens=40, cost_usd=0.5, session_id=sid1, task_id=task_id)
    add_usage_record(conn, provider="openai", model="gpt-5", input_tokens=30, output_tokens=15, cost_usd=0.2, session_id=sid1, task_id=task_id)
    add_usage_record(
        conn,
        provider="anthropic",
        model="claude-sonnet-4",
        input_tokens=10,
        output_tokens=8,
        cost_usd=0.1,
        session_id=sid2,
        task_id=task_id,
    )

    data = _fetch_session_usage(conn, [sid1, sid2, 9999])
    assert set(data.keys()) == {sid1, sid2}
    assert data[sid1]["provider"] == "openai"
    assert data[sid1]["model"] == "gpt-5"
    assert data[sid1]["input_tokens"] == 130
    assert data[sid1]["output_tokens"] == 55
    assert float(data[sid1]["cost_usd"]) == 0.7
    assert data[sid2]["provider"] == "anthropic"
    assert data[sid2]["model"] == "claude-sonnet-4"


def test_fetch_dashboard_signals_global_and_project_scope(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_tui_signals.db")
    p1 = create_project(conn, "p1", tmp_path / "p1")
    p2 = create_project(conn, "p2", tmp_path / "p2")
    b1 = create_task_batch(conn, project_id=p1, name="b1")
    b2 = create_task_batch(conn, project_id=p2, name="b2")
    t1 = create_task(conn, batch_id=b1, project_id=p1, title="t1", runtime_kind=RuntimeKind.TMUX)
    t2 = create_task(conn, batch_id=b1, project_id=p1, title="t2", runtime_kind=RuntimeKind.LOCAL_PTY)
    t3 = create_task(conn, batch_id=b2, project_id=p2, title="t3", runtime_kind=RuntimeKind.TMUX)

    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (t1,))
    conn.execute("UPDATE tasks SET status = 'awaiting_input' WHERE id = ?", (t2,))
    conn.execute("UPDATE tasks SET status = 'queued' WHERE id = ?", (t3,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'a', 'a', 'active')
        """,
        (t1, p1),
    )
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'b', 'b', 'ended')
        """,
        (t3, p2),
    )
    add_dispatcher_decision(conn, proposal={"preferred_agent": "codex"}, task_id=t1)
    d2 = add_dispatcher_decision(conn, proposal={"preferred_agent": "claude"}, task_id=t3)
    conn.execute("UPDATE dispatcher_decisions SET applied = 1 WHERE id = ?", (d2,))
    conn.execute(
        """
        INSERT INTO alerts(task_id, level, kind, message, status)
        VALUES (?, 'warn', 'task_stuck', 'stuck', 'open')
        """,
        (t1,),
    )
    conn.execute(
        """
        INSERT INTO alerts(task_id, level, kind, message, status)
        VALUES (?, 'warn', 'other', 'issue', 'open')
        """,
        (t3,),
    )
    conn.commit()

    global_signals = _fetch_dashboard_signals(conn, None)
    assert global_signals["queued"] == 1
    assert global_signals["running"] == 1
    assert global_signals["awaiting"] == 1
    assert global_signals["sessions_active"] == 1
    assert global_signals["pending_dispatch"] == 1
    assert global_signals["alerts_open"] == 2
    assert global_signals["stuck_open"] == 1

    p1_signals = _fetch_dashboard_signals(conn, p1)
    assert p1_signals["queued"] == 0
    assert p1_signals["running"] == 1
    assert p1_signals["awaiting"] == 1
    assert p1_signals["sessions_active"] == 1
    assert p1_signals["pending_dispatch"] == 1
    assert p1_signals["alerts_open"] == 1
    assert p1_signals["stuck_open"] == 1


def test_fetch_open_alerts_and_resolve_filtering(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_tui_alerts.db")
    p1 = create_project(conn, "p1", tmp_path / "p1")
    p2 = create_project(conn, "p2", tmp_path / "p2")
    b1 = create_task_batch(conn, project_id=p1, name="b1")
    b2 = create_task_batch(conn, project_id=p2, name="b2")
    t1 = create_task(conn, batch_id=b1, project_id=p1, title="t1", runtime_kind=RuntimeKind.TMUX)
    t2 = create_task(conn, batch_id=b2, project_id=p2, title="t2", runtime_kind=RuntimeKind.TMUX)

    conn.execute(
        "INSERT INTO alerts(task_id, level, kind, message, status) VALUES (?, 'warn', 'task_stuck', 'a1', 'open')",
        (t1,),
    )
    conn.execute(
        "INSERT INTO alerts(task_id, level, kind, message, status) VALUES (?, 'warn', 'other', 'a2', 'open')",
        (t2,),
    )
    conn.execute(
        "INSERT INTO alerts(task_id, level, kind, message, status) VALUES (?, 'warn', 'other', 'closed', 'resolved')",
        (t2,),
    )
    conn.commit()

    global_alerts = _fetch_open_alerts(conn, None)
    assert len(global_alerts) == 2
    p1_alerts = _fetch_open_alerts(conn, p1)
    assert len(p1_alerts) == 1
    alert_id = int(p1_alerts[0]["id"])
    _resolve_alert(conn, alert_id)
    p1_after = _fetch_open_alerts(conn, p1)
    assert p1_after == []


def test_fetch_pending_dispatcher_decisions_filtering(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_tui_decisions.db")
    p1 = create_project(conn, "p1", tmp_path / "p1")
    p2 = create_project(conn, "p2", tmp_path / "p2")
    b1 = create_task_batch(conn, project_id=p1, name="b1")
    b2 = create_task_batch(conn, project_id=p2, name="b2")
    t1 = create_task(conn, batch_id=b1, project_id=p1, title="t1", runtime_kind=RuntimeKind.TMUX)
    t2 = create_task(conn, batch_id=b2, project_id=p2, title="t2", runtime_kind=RuntimeKind.TMUX)
    d1 = add_dispatcher_decision(conn, proposal={"preferred_agent": "codex"}, task_id=t1, confidence=0.5)
    d2 = add_dispatcher_decision(conn, proposal={"preferred_agent": "claude"}, task_id=t2, confidence=0.9)
    conn.execute("UPDATE dispatcher_decisions SET applied = 1 WHERE id = ?", (d2,))
    conn.commit()

    global_pending = _fetch_pending_dispatcher_decisions(conn, None)
    assert len(global_pending) == 1
    assert int(global_pending[0]["id"]) == d1
    p1_pending = _fetch_pending_dispatcher_decisions(conn, p1)
    assert len(p1_pending) == 1
    p2_pending = _fetch_pending_dispatcher_decisions(conn, p2)
    assert p2_pending == []


def test_fetch_batches_and_batch_scoped_task_session_queries(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_tui_batches.db")
    project_id = create_project(conn, "demo", tmp_path / "repo")
    b1 = create_task_batch(conn, project_id=project_id, name="b1")
    b2 = create_task_batch(conn, project_id=project_id, name="b2")
    t1 = create_task(conn, batch_id=b1, project_id=project_id, title="t1", runtime_kind=RuntimeKind.TMUX)
    t2 = create_task(conn, batch_id=b1, project_id=project_id, title="t2", runtime_kind=RuntimeKind.LOCAL_PTY)
    t3 = create_task(conn, batch_id=b2, project_id=project_id, title="t3", runtime_kind=RuntimeKind.TMUX)
    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (t1,))
    conn.execute("UPDATE tasks SET status = 'paused' WHERE id = ?", (t2,))
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (t3,))
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'sid-1', 'sid-1', 'active')
        """,
        (t1, project_id),
    )
    conn.execute(
        """
        INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status)
        VALUES (?, ?, 'tmux', 'sid-2', 'sid-2', 'ended')
        """,
        (t3, project_id),
    )
    conn.commit()

    batches = _fetch_batches(conn, project_id)
    assert len(batches) == 2
    by_id = {int(row["id"]): row for row in batches}
    assert int(by_id[b1]["task_total"]) == 2
    assert int(by_id[b1]["running_count"]) == 1
    assert int(by_id[b1]["paused_count"]) == 1
    assert int(by_id[b1]["completed_count"]) == 0
    assert int(by_id[b2]["task_total"]) == 1
    assert int(by_id[b2]["completed_count"]) == 1

    tasks_b1 = _fetch_tasks(conn, project_id=project_id, batch_id=b1)
    assert {int(row["id"]) for row in tasks_b1} == {t1, t2}
    tasks_b2 = _fetch_tasks(conn, project_id=project_id, batch_id=b2)
    assert [int(row["id"]) for row in tasks_b2] == [t3]

    sessions_b1 = _fetch_sessions(conn, project_id=project_id, batch_id=b1)
    assert len(sessions_b1) == 1
    assert int(sessions_b1[0]["task_id"]) == t1
    sessions_b2 = _fetch_sessions(conn, project_id=project_id, batch_id=b2)
    assert len(sessions_b2) == 1
    assert int(sessions_b2[0]["task_id"]) == t3

    detail = _fetch_batch_detail(conn, b1)
    assert detail is not None
    assert int(detail["batch"]["id"]) == b1
    assert len(detail["tasks"]) == 2
    assert detail["timeline"] == []


def test_upsert_project_from_onboarding_validates_git_root_and_guidelines(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_onboarding.db")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    guidelines = tmp_path / "guidelines.md"
    guidelines.write_text("Always run tests", encoding="utf-8")

    project_id = _upsert_project_from_onboarding(
        conn=conn,
        name="demo",
        root_path=str(repo),
        guidelines_file=str(guidelines),
    )
    row = conn.execute("SELECT name, root_path, guidelines FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row is not None
    assert row["name"] == "demo"
    assert Path(str(row["root_path"])) == repo.resolve()
    assert "Always run tests" in str(row["guidelines"])


def test_upsert_project_from_onboarding_rejects_non_git_root(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_onboarding_invalid.db")
    not_repo = tmp_path / "plain-dir"
    not_repo.mkdir(parents=True)
    try:
        _upsert_project_from_onboarding(conn=conn, name="demo", root_path=str(not_repo), guidelines_file=None)
        raise AssertionError("expected ValueError for non-git root")
    except ValueError as exc:
        assert "not a git repository" in str(exc)
