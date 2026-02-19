from __future__ import annotations

import shutil
import sqlite3
import re
from pathlib import Path

import pytest

from yeehaw import db, orchestrator
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


def _mk_roadmap() -> RoadmapDef:
    stage1 = StageDef(id="s1", title="Stage 1", goal="Do stage 1", instructions="inst1")
    stage2 = StageDef(id="s2", title="Stage 2", goal="Do stage 2", instructions="inst2")
    track = TrackDef(id="main", topic="Main", agent="codex", stages=[stage1, stage2])
    return RoadmapDef(version=1, name="rm", guidelines=["g"], tracks=[track], raw_text="raw")


def _seed_project(conn: sqlite3.Connection, root: Path, name: str = "proj") -> int:
    root.mkdir(parents=True, exist_ok=True)
    return db.create_project(conn, name, str(root), "global-guidelines")


def test_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert orchestrator._safe_slug("A B!") == "a-b"
    assert orchestrator._safe_slug("???") == "task"
    assert "task-7" in orchestrator._branch_name("Proj", 7, "Do A Thing")
    assert orchestrator._done_marker(1).startswith("[[YEEHAW_DONE")
    assert orchestrator._input_marker(1).startswith("[[YEEHAW_NEEDS_INPUT")
    assert orchestrator._progress_marker(1).startswith("[[YEEHAW_PROGRESS")
    assert len(orchestrator._pane_hash("x")) == 40

    assert orchestrator._parse_iso_utc(None) is None
    assert orchestrator._parse_iso_utc("   ") is None
    assert orchestrator._parse_iso_utc("2020-01-01T00:00:00Z") is not None
    assert orchestrator._parse_iso_utc("bad") is None
    assert orchestrator._elapsed_minutes(None) == 0.0
    assert orchestrator._elapsed_minutes("2000-01-01T00:00:00Z") > 0.0

    class _Proc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *_a, **_k: _Proc(0, stdout="ok\n"))
    assert orchestrator._run_git("/tmp", "status") == "ok"

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *_a, **_k: _Proc(1, stderr="err"))
    with pytest.raises(RuntimeError, match="err"):
        orchestrator._run_git("/tmp", "status")

    assert orchestrator._choose_agent("claude", 0) == "claude"
    assert orchestrator._choose_agent("", 1) in {"codex", "claude", "gemini"}
    assert orchestrator._next_agent("codex") in {"claude", "gemini"}
    assert orchestrator._next_agent("unknown") == "codex"
    assert orchestrator._fresh_question_marker("[[M]]\nQuestion: q", "[[M]]") is True
    assert orchestrator._fresh_question_marker("[[M]]\nQuestion: q\nreply", "[[M]]") is False
    assert orchestrator._fresh_question_marker("[[M]]\n", "[[M]]") is False
    assert orchestrator._fresh_question_marker("[[M]]\nNotAQuestion", "[[M]]") is False
    assert orchestrator._fresh_question_marker("none", "[[M]]") is False
    assert "Branch policy" in orchestrator._stage_prompt("p", "/tmp/p", "b", "g", "t", "d", "d1", "i1", "p1")
    assert "roadmap planner" in orchestrator._planner_prompt("p", "/tmp/p", "x", "/tmp/r.md", "m").lower()
    wt = orchestrator._task_worktree_path("/tmp/proj", 7, 2)
    assert str(wt).endswith("/.yeehaw/worktrees/task-7-a2")

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(orchestrator, "_run_git", lambda root, *args: calls.append((root, args)) or "")
    orchestrator._cleanup_task_worktree("/tmp/proj", "/tmp/proj/.yeehaw/worktrees/task-1-a1")
    assert calls

    calls.clear()
    orchestrator._cleanup_task_worktree("/tmp/proj", "/tmp/proj")
    assert calls == []

    # Existing worktree removal fallback in _prepare_task_worktree.
    repo = tmp_path / "prepare-repo"
    wt_dir = repo / ".yeehaw" / "worktrees" / "task-9-a1"
    wt_dir.mkdir(parents=True, exist_ok=True)
    prepare_calls: list[tuple[str, tuple[str, ...]]] = []

    def _prepare_git(root: str, *args: str) -> str:
        prepare_calls.append((root, args))
        if args[:2] == ("worktree", "remove"):
            raise RuntimeError("remove failed")
        return ""

    monkeypatch.setattr(orchestrator, "_run_git", _prepare_git)
    prepared = orchestrator._prepare_task_worktree(str(repo), 9, 1, "b", "sha")
    assert prepared.endswith("task-9-a1")
    assert wt_dir.exists() is False
    shutil.rmtree(repo, ignore_errors=True)

    # Removal/prune fallback in _cleanup_task_worktree.
    repo2 = tmp_path / "cleanup-repo"
    repo2.mkdir(parents=True, exist_ok=True)
    stale = repo2 / ".yeehaw" / "worktrees" / "task-5-a1"
    stale.mkdir(parents=True, exist_ok=True)
    cleanup_calls: list[tuple[str, tuple[str, ...]]] = []

    def _cleanup_git(root: str, *args: str) -> str:
        cleanup_calls.append((root, args))
        if args[:2] in {("worktree", "remove"), ("worktree", "prune")}:
            raise RuntimeError("git failed")
        return ""

    monkeypatch.setattr(orchestrator, "_run_git", _cleanup_git)
    orchestrator._cleanup_task_worktree(str(repo2), str(stale))
    assert stale.exists() is False
    assert cleanup_calls
    shutil.rmtree(repo2, ignore_errors=True)


def test_create_batch_from_roadmap_and_replan(conn: sqlite3.Connection, tmp_path: Path) -> None:
    pid = _seed_project(conn, tmp_path / "repo")
    batch_id = orchestrator.create_batch_from_roadmap(
        conn=conn,
        project_id=pid,
        batch_name="B1",
        roadmap=_mk_roadmap(),
        source_text="src",
        roadmap_path=str(tmp_path / "roadmap.md"),
    )
    batch = db.get_task_batch(conn, batch_id)
    assert batch is not None
    assert batch["status"] == "queued"
    tasks = db.list_tasks(conn, batch_id=batch_id)
    assert len(tasks) == 2

    # Replan from markdown and ensure queued/stuck tasks are replaced.
    roadmap_path = tmp_path / "replan.md"
    roadmap_path.write_text(
        """## 2. Execution Phases

### Phase 1: New Plan
**Objective:**
Do work
""",
        encoding="utf-8",
    )
    db.set_task_state(conn, int(tasks[0]["id"]), "queued")
    db.set_task_state(conn, int(tasks[1]["id"]), "stuck")
    orchestrator.replan_batch_from_roadmap(batch_id=batch_id, roadmap_path=roadmap_path, db_path=tmp_path / "yeehaw.db")


def test_create_batch_from_task_list_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    _seed_project(conn, tmp_path / "repo")
    project = db.get_project(conn, "proj")
    assert project is not None

    calls: list[str] = []
    monkeypatch.setattr(orchestrator, "resolve_command", lambda *_a, **_k: ("planner", 0.0))
    monkeypatch.setattr(orchestrator, "ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "ensure_window", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "send_text", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda s: calls.append(s))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_a, **_k: None)

    mono = {"v": 0.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: mono.__setitem__("v", mono["v"] + 0.1) or mono["v"])
    captures = {"n": 0}

    def fake_capture(_target: str) -> str:
        captures["n"] += 1
        if captures["n"] == 1:
            return "baseline"
        if captures["n"] == 2:
            return "still planning"
        return "[[YEEHAW_DONE PLAN-1]]\nSummary:\n- planned\n"

    monkeypatch.setattr(orchestrator, "capture_pane", fake_capture)

    roadmap_file = tmp_path / "repo" / ".yeehaw" / "roadmaps" / "batch-1.roadmap.md"
    roadmap_file.parent.mkdir(parents=True, exist_ok=True)
    roadmap_file.write_text(
        """## 2. Execution Phases

### Phase 1: First
**Objective:**
Do first
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(orchestrator, "load_roadmap", lambda *_a, **_k: _mk_roadmap())
    batch_id = orchestrator.create_batch_from_task_list(
        project_name="proj",
        batch_name="batch",
        task_list_text="- item1",
        planner_agent="codex",
        db_path=db_path,
        timeout_minutes=1,
    )
    assert batch_id > 0
    assert calls


def test_create_batch_from_task_list_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    _seed_project(conn, tmp_path / "repo")

    monkeypatch.setattr(orchestrator, "resolve_command", lambda *_a, **_k: ("planner", 0.0))
    monkeypatch.setattr(orchestrator, "ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "ensure_window", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "send_text", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: "")
    ticks = {"v": 0.0}
    monkeypatch.setattr(
        orchestrator.time,
        "monotonic",
        lambda: ticks.__setitem__("v", ticks["v"] + 1000.0) or ticks["v"],
    )

    with pytest.raises(TimeoutError):
        orchestrator.create_batch_from_task_list(
            project_name="proj",
            batch_name="batch",
            task_list_text="- item1",
            planner_agent="codex",
            db_path=db_path,
            timeout_minutes=1,
        )

    failed = conn.execute("SELECT COUNT(*) AS c FROM task_batches WHERE status = 'failed'").fetchone()
    assert int(failed["c"]) >= 1

    # Explicit "roadmap file missing after done marker" branch.
    miss_calls = {"n": 0}
    def _miss_capture(*_a, **_k):
        miss_calls["n"] += 1
        if miss_calls["n"] == 1:
            return "planning..."
        return "[[YEEHAW_DONE PLAN-2]]\nSummary:\n- done\n"
    monkeypatch.setattr(orchestrator, "capture_pane", _miss_capture)
    t = {"v": 0.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: t.__setitem__("v", t["v"] + 0.1) or t["v"])
    with pytest.raises(RuntimeError, match="did not write roadmap"):
        orchestrator.create_batch_from_task_list(
            project_name="proj",
            batch_name="missing-roadmap",
            task_list_text="- x",
            planner_agent="codex",
            db_path=db_path,
            timeout_minutes=1,
        )

    with pytest.raises(ValueError, match="not found"):
        orchestrator.create_batch_from_task_list(
            project_name="missing",
            batch_name="batch",
            task_list_text="- x",
            db_path=db_path,
        )


def _seed_task(conn: sqlite3.Connection, project_id: int, status: str = "queued") -> int:
    batch_id = db.create_task_batch(conn, project_id=project_id, name="batch", source_text="src", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=project_id, title="T1", description="D1")
    if status != "queued":
        conn.execute(
            "UPDATE tasks SET status = ?, assigned_agent = 'codex', tmux_target = 's:task.0', tmux_session='s1', branch_name='b1', base_sha='sha', last_output_hash='h0', last_progress_at='2020-01-01T00:00:00Z' WHERE id = ?",
            (status, task_id),
        )
        conn.commit()
    return task_id


def test_scheduler_dispatch_and_complete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    task_id = _seed_task(conn, pid, status="queued")

    state = {"branch": "main", "created_worktrees": 0, "removed_worktrees": 0}

    def fake_git(_root: str, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "sha"
        if args[:2] == ("worktree", "add"):
            state["branch"] = args[4]
            state["created_worktrees"] += 1
            return ""
        if args[:2] == ("worktree", "remove"):
            state["removed_worktrees"] += 1
            return ""
        if args[:2] == ("worktree", "prune"):
            return ""
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return state["branch"]
        if args and args[0] == "rev-list":
            return ""
        return ""

    monkeypatch.setattr(orchestrator, "_run_git", fake_git)
    monkeypatch.setattr(orchestrator, "resolve_command", lambda *_a, **_k: ("codex", 0.0))
    tmux_roots: list[str] = []
    monkeypatch.setattr(orchestrator, "ensure_session", lambda _s, cwd: tmux_roots.append(cwd))
    monkeypatch.setattr(orchestrator, "ensure_window", lambda _s, _w, cwd, _c: tmux_roots.append(cwd))
    monkeypatch.setattr(orchestrator, "send_text", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_a, **_k: None)
    done = orchestrator._done_marker(task_id)
    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: f"{done}\nSummary:\n- ok\nArtifacts:\n- a.txt\n")

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=3)
    stats = scheduler.tick()
    assert stats.dispatched == 1
    assert stats.completed == 1
    row = db.get_task(scheduler.conn, task_id)
    assert row is not None
    assert row["status"] == "completed"
    assert str(row["worktree_path"]).endswith(f"task-{task_id}-a1")
    assert state["created_worktrees"] == 1
    assert state["removed_worktrees"] == 1
    assert len(tmux_roots) == 2
    assert tmux_roots[0] == tmux_roots[1]


def test_scheduler_awaiting_and_reply_and_pause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    task_id = _seed_task(conn, pid, status="running")
    marker = orchestrator._input_marker(task_id)
    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: f"{marker}\nQuestion: choose\n")
    monkeypatch.setattr(orchestrator, "send_keys", lambda *_a, **_k: None)

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=3)
    stats = scheduler.tick()
    assert stats.awaiting_input == 1

    sent: list[str] = []
    monkeypatch.setattr(orchestrator, "send_text", lambda _t, text, press_enter=True: sent.append(text))
    scheduler.reply_to_task(task_id, "continue with option A")
    assert sent

    scheduler.pause_task(task_id)
    row = db.get_task(scheduler.conn, task_id)
    assert row is not None
    assert row["status"] in {"queued", "running"}


def test_scheduler_stuck_reassign_and_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    task_id = _seed_task(conn, pid, status="running")
    pane_text = "still looping"
    conn.execute("UPDATE tasks SET loop_count = 6, assigned_agent = 'codex', attempt_count = 1 WHERE id = ?", (task_id,))
    conn.execute(
        "UPDATE tasks SET last_output_hash = ? WHERE id = ?",
        (orchestrator._pane_hash(pane_text), task_id),
    )
    conn.commit()

    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: pane_text)
    monkeypatch.setattr(orchestrator, "send_keys", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "_elapsed_minutes", lambda *_a, **_k: 0.0)

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=2)
    stats = scheduler.tick()
    assert stats.reassigned >= 1
    row = db.get_task(scheduler.conn, task_id)
    assert row is not None
    assert row["status"] == "queued"

    conn.execute("UPDATE tasks SET attempt_count = 5, status='running', tmux_target='s:task.0', tmux_session='s1' WHERE id = ?", (task_id,))
    conn.commit()
    task = db.get_task(scheduler.conn, task_id)
    assert task is not None
    scheduler._reassign_task(task, "reason")
    row2 = db.get_task(scheduler.conn, task_id)
    assert row2 is not None
    assert row2["status"] == "failed"


def test_scheduler_monitor_tmux_error_and_run_forever(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    _seed_task(conn, pid, status="running")

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01)
    monkeypatch.setattr(scheduler, "_monitor_one", lambda *_a, **_k: (_ for _ in ()).throw(orchestrator.TmuxError("boom")))
    stats = orchestrator.SchedulerStats()
    scheduler._monitor_active(stats)
    assert stats.failed >= 1

    calls = {"n": 0}

    def fake_tick():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stop")
        return orchestrator.SchedulerStats()

    monkeypatch.setattr(scheduler, "tick", fake_tick)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="stop"):
        scheduler.run_forever()


def test_replan_missing_batch_and_dispatch_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    _seed_task(conn, pid, status="queued")

    with pytest.raises(ValueError, match="not found"):
        orchestrator.replan_batch_from_roadmap(batch_id=999, roadmap_path=tmp_path / "x.md", db_path=db_path)

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=2)
    monkeypatch.setattr(scheduler, "_dispatch_task", lambda *_a, **_k: None)
    db.update_scheduler_config(conn, max_global_sessions=0, max_project_sessions=0)
    stats = orchestrator.SchedulerStats()
    scheduler._dispatch_queued(stats)
    assert stats.dispatched == 0

    db.update_scheduler_config(conn, max_global_sessions=10, max_project_sessions=0)
    scheduler._dispatch_queued(stats)
    assert stats.dispatched == 0

    db.update_scheduler_config(conn, max_global_sessions=1, max_project_sessions=10)
    _seed_task(conn, pid, status="queued")
    scheduler._dispatch_queued(stats)

    # Ensure _dispatch_task early-return path when task lookup fails.
    monkeypatch.setattr(orchestrator.db, "get_task", lambda *_a, **_k: None)
    scheduler._dispatch_task({"id": 1})


def test_scheduler_reply_pause_and_monitor_edge_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    task_id = _seed_task(conn, pid, status="running")
    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=2)

    with pytest.raises(ValueError, match="not found"):
        scheduler.reply_to_task(999, "a")
    with pytest.raises(ValueError, match="not awaiting input"):
        scheduler.reply_to_task(task_id, "a")

    conn.execute("UPDATE tasks SET status='awaiting_input', tmux_target='' WHERE id = ?", (task_id,))
    conn.commit()
    with pytest.raises(RuntimeError, match="no tmux target"):
        scheduler.reply_to_task(task_id, "a")

    conn.execute("UPDATE tasks SET status='completed' WHERE id = ?", (task_id,))
    conn.commit()
    scheduler.pause_task(task_id)
    with pytest.raises(ValueError, match="not found"):
        scheduler.pause_task(999)

    # _monitor_active skip branches
    conn.execute("UPDATE tasks SET status='awaiting_input', tmux_target='x:0.0' WHERE id = ?", (task_id,))
    conn.commit()
    stats = orchestrator.SchedulerStats()
    scheduler._monitor_active(stats)

    conn.execute("UPDATE tasks SET status='stuck', tmux_target='x:0.0' WHERE id = ?", (task_id,))
    conn.commit()
    db.update_scheduler_config(conn, auto_reassign=False)
    scheduler._monitor_active(stats)

    conn.execute("UPDATE tasks SET status='running', tmux_target='' WHERE id = ?", (task_id,))
    conn.commit()
    scheduler._monitor_active(stats)

    # get_task returns None branch.
    monkeypatch.setattr(orchestrator.db, "get_task", lambda *_a, **_k: None)
    scheduler._monitor_active(stats)


def test_monitor_progress_and_policy_violations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo")
    task_id = _seed_task(conn, pid, status="running")
    conn.execute(
        "UPDATE tasks SET tmux_target='s:task.0', branch_name='expected', base_sha='sha', assigned_agent='codex' WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=2)

    # Progress marker path + interactive/timeout reason construction.
    progress = orchestrator._progress_marker(task_id)
    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: f"{progress}\nPassword:")
    monkeypatch.setattr(orchestrator, "_elapsed_minutes", lambda *_a, **_k: 5.0)
    monkeypatch.setattr(orchestrator, "send_keys", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda *_a, **_k: None)
    stats = orchestrator.SchedulerStats()
    task = db.get_task(scheduler.conn, task_id)
    assert task is not None
    scheduler._monitor_one(task, stuck_minutes=1, auto_reassign=True, stats=stats)

    # Branch-policy violation.
    conn.execute("UPDATE tasks SET status='running', tmux_target='s:task.0', branch_name='expected', base_sha='sha' WHERE id = ?", (task_id,))
    conn.commit()
    done = orchestrator._done_marker(task_id)
    monkeypatch.setattr(orchestrator, "capture_pane", lambda *_a, **_k: f"{done}\nSummary:\n- ok\nArtifacts:\n- a.txt")
    monkeypatch.setattr(
        orchestrator,
        "_run_git",
        lambda _r, *args: "wrong" if args == ("rev-parse", "--abbrev-ref", "HEAD") else "sha",
    )
    task2 = db.get_task(scheduler.conn, task_id)
    assert task2 is not None
    scheduler._monitor_one(task2, stuck_minutes=5, auto_reassign=False, stats=orchestrator.SchedulerStats())

    # Merge-policy violation.
    conn.execute("UPDATE tasks SET status='running', tmux_target='s:task.0', branch_name='expected', base_sha='sha' WHERE id = ?", (task_id,))
    conn.commit()
    monkeypatch.setattr(
        orchestrator,
        "_run_git",
        lambda _r, *args: "expected" if args == ("rev-parse", "--abbrev-ref", "HEAD") else ("merge" if args and args[0] == "rev-list" else "sha"),
    )
    task3 = db.get_task(scheduler.conn, task_id)
    assert task3 is not None
    scheduler._monitor_one(task3, stuck_minutes=5, auto_reassign=False, stats=orchestrator.SchedulerStats())


def test_dispatch_task_missing_full_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    _seed_project(conn, tmp_path / "repo")
    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01)
    monkeypatch.setattr(orchestrator.db, "get_task", lambda *_a, **_k: None)
    scheduler._dispatch_task({"id": 1})


def test_scheduler_parallel_same_project_uses_isolated_worktrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = _seed_project(conn, tmp_path / "repo", name="demo")
    t1 = _seed_task(conn, pid, status="queued")
    t2 = _seed_task(conn, pid, status="queued")

    branch_by_root: dict[str, str] = {}
    removed_worktrees: set[str] = set()

    def fake_git(root: str, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "sha"
        if args[:2] == ("worktree", "add"):
            branch = args[4]
            worktree = args[5]
            branch_by_root[worktree] = branch
            return ""
        if args[:2] == ("worktree", "remove"):
            removed_worktrees.add(args[3])
            return ""
        if args[:2] == ("worktree", "prune"):
            return ""
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return branch_by_root.get(root, "main")
        if args and args[0] == "rev-list":
            return ""
        return ""

    monkeypatch.setattr(orchestrator, "_run_git", fake_git)
    monkeypatch.setattr(orchestrator, "resolve_command", lambda *_a, **_k: ("codex", 0.0))
    monkeypatch.setattr(orchestrator, "ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "ensure_window", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "send_text", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "kill_session", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda *_a, **_k: None)

    def fake_capture(target: str, *_a, **_k) -> str:
        session = target.split(":", 1)[0]
        match = re.search(r"-t(\d+)-", session)
        assert match is not None
        task_id = int(match.group(1))
        done = orchestrator._done_marker(task_id)
        return f"{done}\nSummary:\n- ok\nArtifacts:\n- README.md\n"

    monkeypatch.setattr(orchestrator, "capture_pane", fake_capture)

    scheduler = orchestrator.GlobalScheduler(db_path=db_path, poll_seconds=0.01, max_attempts=2)
    stats = scheduler.tick()
    assert stats.dispatched == 2
    assert stats.completed == 2

    row1 = db.get_task(scheduler.conn, t1)
    row2 = db.get_task(scheduler.conn, t2)
    assert row1 is not None
    assert row2 is not None
    assert row1["status"] == "completed"
    assert row2["status"] == "completed"
    assert row1["worktree_path"] != row2["worktree_path"]
    assert str(row1["worktree_path"]) in removed_worktrees
    assert str(row2["worktree_path"]) in removed_worktrees
    alerts = db.list_alerts(scheduler.conn, only_open=False, limit=20)
    assert not any(str(a["kind"]) == "branch-policy" for a in alerts)
