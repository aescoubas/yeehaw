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
