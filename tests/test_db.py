from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from yeehaw import db
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


def _sample_roadmap() -> RoadmapDef:
    stage = StageDef(id="s1", title="S1", goal="Goal", instructions="i", deliverables=["a"], timeout_minutes=2)
    track = TrackDef(id="t1", topic="topic", agent="codex", command="codex", stages=[stage])
    return RoadmapDef(version=1, name="rm", guidelines=["g"], tracks=[track], raw_text="raw")


def test_default_db_path_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YEEHAW_DB", str(tmp_path / "x.db"))
    assert db.default_db_path() == (tmp_path / "x.db").resolve()
    monkeypatch.delenv("YEEHAW_DB", raising=False)
    assert db.default_db_path().name == "yeehaw.db"


def test_connect_and_migrate(tmp_path: Path) -> None:
    p = tmp_path / "db.sqlite"
    conn = db.connect(p)
    conn.close()

    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.row_factory = sqlite3.Row
    old.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, root_path TEXT, guidelines TEXT)")
    db._migrate_projects_table(old)
    cols = {r["name"] for r in old.execute("PRAGMA table_info(projects)").fetchall()}
    assert {"git_remote_url", "default_branch", "head_sha"}.issubset(cols)
    old.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER,
            project_id INTEGER,
            title TEXT
        )
        """
    )
    db._migrate_tasks_table(old)
    task_cols = {r["name"] for r in old.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "worktree_path" in task_cols


def test_project_and_roadmap_and_run_lifecycle(conn: sqlite3.Connection) -> None:
    pid = db.create_project(conn, "p", "/tmp/p", "g", "remote", "main", "sha")
    assert pid > 0
    pid2 = db.create_project(conn, "p", "/tmp/p2", "g2")
    assert pid2 == pid

    projects = db.list_projects(conn)
    assert len(projects) == 1
    project = db.get_project(conn, "p")
    assert project is not None
    assert project["root_path"] == "/tmp/p2"

    rm_id = db.insert_roadmap(conn, pid, _sample_roadmap())
    assert rm_id > 0

    run_id = db.create_run(conn, pid, rm_id, "sess")
    db.set_run_status(conn, run_id, "running")
    db.set_run_status(conn, run_id, "completed", finished=True)

    trk_id = db.create_track_run(conn, run_id, _sample_roadmap().tracks[0], "w")
    db.set_track_run_state(conn, trk_id, "ready")
    db.set_track_run_state(conn, trk_id, "in_progress", current_stage_index=1)
    db.set_track_run_state(conn, trk_id, "awaiting_input", waiting_question="q")
    db.set_track_run_state(conn, trk_id, "done", last_pane="pane")

    stage = _sample_roadmap().tracks[0].stages[0]
    sr_id = db.create_stage_run(conn, trk_id, stage, "tok", 1, 2)
    db.set_stage_run_awaiting_input(conn, sr_id, "pane")
    db.complete_stage_run(conn, sr_id, "completed", "summary", "artifacts", "pane2")

    assert db.get_stage_summaries(conn, trk_id) == ["summary"]

    db.add_event(conn, run_id, "info", "m1", track_run_id=trk_id, stage_run_id=sr_id)
    db.add_event(conn, run_id, "warn", "m2")

    assert len(db.latest_runs(conn, limit=5)) == 1
    assert len(db.run_tracks(conn, run_id)) == 1
    events = db.run_events(conn, run_id, limit=5)
    assert len(events) == 2
    run = db.get_run(conn, run_id)
    assert run is not None
    assert run["project_name"] == "p"


def test_to_json_array() -> None:
    assert db._to_json_array(["a", "b"]) == '["a", "b"]'


def test_create_project_runtime_error_branch() -> None:
    class FakeCursor:
        def fetchone(self):
            return None

    class FakeConn:
        def execute(self, *_a, **_k):
            return FakeCursor()

        def commit(self):
            return None

    with pytest.raises(RuntimeError, match="failed to upsert project"):
        db.create_project(FakeConn(), "n", "/r", "g")


def test_scheduler_and_task_tables(conn: sqlite3.Connection, tmp_path: Path) -> None:
    pid = db.create_project(conn, "sched", str(tmp_path / "repo"), "guidelines")

    cfg = db.scheduler_config(conn)
    assert int(cfg["max_global_sessions"]) == 20

    db.update_scheduler_config(
        conn,
        max_global_sessions=30,
        max_project_sessions=12,
        default_stuck_minutes=9,
        auto_reassign=False,
        preemption_enabled=False,
    )
    cfg2 = db.scheduler_config(conn)
    assert int(cfg2["max_global_sessions"]) == 30
    assert int(cfg2["max_project_sessions"]) == 12
    assert int(cfg2["default_stuck_minutes"]) == 9
    assert int(cfg2["auto_reassign"]) == 0
    assert int(cfg2["preemption_enabled"]) == 0

    batch_id = db.create_task_batch(
        conn,
        project_id=pid,
        name="b1",
        source_text="src",
        roadmap_path="/tmp/r.md",
        roadmap_text="raw",
        status="draft",
    )
    db.set_task_batch_status(conn, batch_id, "ready")
    db.update_task_batch_roadmap(conn, batch_id, "/tmp/r2.md", "raw2")
    batches = db.list_task_batches(conn, project_id=pid, limit=10)
    assert batches and int(batches[0]["id"]) == batch_id
    assert db.get_task_batch(conn, batch_id) is not None

    t1 = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T1", description="d1", priority="high", preferred_agent="codex")
    t2 = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T2", description="d2", priority="low")
    assert len(db.list_tasks(conn, batch_id=batch_id, limit=20)) >= 2
    assert db.get_task(conn, t1) is not None
    assert db.count_active_tasks(conn) == 0

    db.mark_task_dispatching(conn, t1, "codex", "b", str(tmp_path / "repo" / ".yeehaw" / "worktrees" / "task-1-a1"), "sha", "sess", "sess:task.0")
    db.set_task_state(conn, t1, "running", last_output_hash="h1", loop_count=1)
    db.touch_task_progress(conn, t1, last_output_hash="h2")
    db.touch_task_progress(conn, t1)
    db.set_task_state(conn, t2, "awaiting_input", blocked_question="q?")
    db.set_task_resume_ready(conn, t2)
    assert db.count_active_tasks(conn) >= 1
    queued = db.next_queued_tasks(conn, limit=10)
    assert isinstance(queued, list)

    db.add_task_event(conn, t1, "info", "m1")
    assert db.task_events(conn, t1, limit=5)

    alert_id = db.create_alert(conn, level="warn", kind="stuck", message="m", task_id=t1, project_id=pid)
    assert db.list_alerts(conn, only_open=True, limit=10)
    db.resolve_alert(conn, alert_id)
    assert db.list_alerts(conn, only_open=False, limit=10)

    sess_id = db.create_agent_session(conn, t1, pid, "codex", "running", "sess", "sess:task.0")
    db.heartbeat_agent_session(conn, sess_id, progress=False)
    db.heartbeat_agent_session(conn, sess_id, progress=True)
    db.set_agent_session_status(conn, sess_id, "completed", ended=True)
    assert isinstance(db.active_agent_sessions(conn), list)

    reply_id = db.save_operator_reply(conn, t1, "Q", "A")
    assert reply_id > 0

    rev_id = db.add_roadmap_revision(conn, project_id=pid, batch_id=batch_id, path="/tmp/r.md", source="manual", raw_text="x")
    assert rev_id > 0
    assert db.roadmap_revision_count(conn, pid, batch_id=batch_id) >= 1
    cp_id = db.add_phase_checkpoint(conn, t1, "summary", "dec", "ctx")
    assert cp_id > 0
    assert db.list_task_batches(conn, project_id=None, limit=20)
    assert db.list_tasks(conn, status="running", project_id=pid, batch_id=batch_id, limit=20)
    assert db.roadmap_revision_count(conn, pid) >= 1


def test_scheduler_config_missing_row_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Cur:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self):
            self.calls = 0

        def execute(self, *_a, **_k):
            self.calls += 1
            if self.calls <= 2:
                return _Cur(None)
            return _Cur({"max_global_sessions": 1, "max_project_sessions": 1, "default_stuck_minutes": 1, "auto_reassign": 1, "preemption_enabled": 1})

        def commit(self):
            return None

    c = _Conn()
    row = db.scheduler_config(c)
    assert row is not None

    class _BadConn(_Conn):
        def execute(self, *_a, **_k):
            return _Cur(None)

    with pytest.raises(RuntimeError, match="scheduler config row missing"):
        db.scheduler_config(_BadConn())
