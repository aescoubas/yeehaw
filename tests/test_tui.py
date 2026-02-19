from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw import db, tui
from yeehaw.git_repo import GitRepoInfo
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


class FakeWindow:
    def __init__(self, h: int = 30, w: int = 140, queue: list[int] | None = None) -> None:
        self.h = h
        self.w = w
        self.queue = queue if queue is not None else []
        self.raise_on_add = False
        self.raise_on_box = False
        self.raise_on_move = False

    def getmaxyx(self):
        return (self.h, self.w)

    def derwin(self, h: int, w: int, _y: int, _x: int):
        child = FakeWindow(h, w, self.queue)
        child.raise_on_add = self.raise_on_add
        child.raise_on_box = self.raise_on_box
        child.raise_on_move = self.raise_on_move
        return child

    def erase(self):
        return None

    def attron(self, _attr: int):
        return None

    def attroff(self, _attr: int):
        return None

    def box(self):
        if self.raise_on_box:
            raise tui.curses.error("box")
        return None

    def addnstr(self, *_args, **_kwargs):
        if self.raise_on_add:
            raise tui.curses.error("add")
        return None

    def refresh(self):
        return None

    def move(self, *_args, **_kwargs):
        if self.raise_on_move:
            raise tui.curses.error("move")
        return None

    def nodelay(self, _flag: bool):
        return None

    def keypad(self, _flag: bool):
        return None

    def getch(self) -> int:
        if self.queue:
            return self.queue.pop(0)
        return -1


class FakeProc:
    def __init__(self, pid: int = 123, rc: int | None = None) -> None:
        self.pid = pid
        self._rc = rc

    def poll(self):
        return self._rc


def _seed_db(
    db_path: Path,
    with_runs: bool = True,
    with_tracks: bool = True,
    run_count: int = 1,
    track_count: int = 1,
) -> None:
    conn = db.connect(db_path)
    p1 = db.create_project(conn, "p1", "/tmp/p1", "g")
    db.create_project(conn, "p2", "/tmp/p2", "g")

    if with_runs:
        stage = StageDef(id="s1", title="S1", goal="g", timeout_minutes=1)
        track = TrackDef(id="t1", topic="topic", agent="codex", command="codex", stages=[stage])
        rm = RoadmapDef(version=1, name="rm", guidelines=[], tracks=[track], raw_text="raw")
        rm_id = db.insert_roadmap(conn, p1, rm)
        for idx in range(run_count):
            run_id = db.create_run(conn, p1, rm_id, f"sess{idx}")
            if with_tracks:
                for t_idx in range(track_count):
                    t = TrackDef(id=f"t{t_idx}", topic=f"topic{t_idx}", agent="codex", command="codex", stages=[stage])
                    tr_id = db.create_track_run(conn, run_id, t, f"win{t_idx}")
                    if t_idx == 0:
                        db.set_track_run_state(conn, tr_id, "awaiting_input", current_stage_index=1, waiting_question="Q?")
                    else:
                        db.set_track_run_state(conn, tr_id, "running", current_stage_index=t_idx)
            if idx == 0:
                db.add_event(conn, run_id, "info", "m1")
                db.add_event(conn, run_id, "warn", "m2")
                db.add_event(conn, run_id, "error", "m3")

    conn.close()


def _patch_curses(monkeypatch: pytest.MonkeyPatch, keys: list[int], size: tuple[int, int], has_colors: bool = False) -> FakeWindow:
    stdscr = FakeWindow(size[0], size[1], keys)
    monkeypatch.setattr(tui.curses, "wrapper", lambda fn: fn(stdscr))
    monkeypatch.setattr(tui.curses, "has_colors", lambda: has_colors)
    monkeypatch.setattr(tui.curses, "start_color", lambda: None)
    monkeypatch.setattr(tui.curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(tui.curses, "init_pair", lambda *_a, **_k: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n * 10)
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    return stdscr


def test_trim_and_window_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tui._trim("abc", 0) == ""
    assert tui._trim("abc", 2) == "ab"
    assert tui._trim("abc", 3) == "abc"
    assert tui._trim("abcdef", 5) == "ab..."

    w = FakeWindow(5, 10)
    tui._safe_add(w, 0, 0, "hello")
    tui._safe_add(w, -1, 0, "x")
    tui._safe_add(w, 0, 10, "x")
    tui._safe_add(w, 0, 9, "x")
    w.raise_on_add = True
    tui._safe_add(w, 0, 0, "x")

    p = tui._new_panel(w, 0, 0, 2, 7, "t", 0)
    assert p is None
    w2 = FakeWindow(20, 20)
    panel = tui._new_panel(w2, 0, 0, 5, 10, "title", 0)
    assert panel is not None
    w3 = FakeWindow(20, 20)
    w3.raise_on_box = True
    panel2 = tui._new_panel(w3, 0, 0, 5, 10, "title", 0)
    assert panel2 is not None


def test_init_colors_and_status_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    p = tui._init_colors()
    assert p["header"] == tui.curses.A_BOLD

    monkeypatch.setattr(tui.curses, "has_colors", lambda: True)
    monkeypatch.setattr(tui.curses, "start_color", lambda: None)
    monkeypatch.setattr(tui.curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(tui.curses, "init_pair", lambda *_a, **_k: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n * 10)
    p2 = tui._init_colors()
    assert p2["border"] == 20

    assert tui._status_attr("running", p2) == p2["running"]
    assert tui._status_attr("awaiting_input", p2) == p2["awaiting_input"]
    assert tui._status_attr("completed", p2) == p2["completed"]
    assert tui._status_attr("failed", p2) == p2["failed"]
    assert tui._status_attr("other", p2) == p2["muted"]


def test_fetch_helpers(conn) -> None:
    pid = db.create_project(conn, "p", "/tmp/p", "g")
    stage = StageDef(id="s1", title="S1", goal="g")
    track = TrackDef(id="t1", topic="topic", agent="codex", stages=[stage])
    rm = RoadmapDef(version=1, name="rm", guidelines=[], tracks=[track], raw_text="raw")
    rm_id = db.insert_roadmap(conn, pid, rm)
    run_id = db.create_run(conn, pid, rm_id, "sess")
    db.set_run_status(conn, run_id, "running")
    run2 = db.create_run(conn, pid, rm_id, "sess2")
    db.set_run_status(conn, run2, "mystery")

    counts = tui._fetch_status_counts(conn)
    assert counts["running"] >= 1
    assert counts["other"] >= 1

    all_runs = tui._fetch_runs(conn, None)
    one_runs = tui._fetch_runs(conn, "p")
    assert len(all_runs) == 2
    assert len(one_runs) == 2

    per = tui._fetch_run_counts_per_project(conn)
    assert per["p"] == 2

    assert tui._window_start(1, 2, 10) == 0
    assert tui._window_start(50, 100, 10) <= 90


def test_default_roadmap_path(tmp_path: Path) -> None:
    assert tui._default_roadmap_path(str(tmp_path)) == "roadmap.md"
    (tmp_path / "roadmap.yaml").write_text("x", encoding="utf-8")
    assert tui._default_roadmap_path(str(tmp_path)) == "roadmap.yaml"


def test_resolve_roadmap_path(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    rel = tui._resolve_roadmap_path(str(project_root), "roadmap.md")
    assert rel == (project_root / "roadmap.md").resolve()
    abs_path = (project_root / "x.yaml").resolve()
    assert tui._resolve_roadmap_path(str(project_root), str(abs_path)) == abs_path


def test_prompt_new_run_modal_cancel_and_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {
        "border": 0,
        "info": 0,
        "muted": 0,
        "failed": 0,
        "selected": 0,
    }
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)

    s1 = FakeWindow(30, 140, [27])
    assert tui._prompt_new_run_modal(s1, palette, "p", "roadmap.md") is None

    s2 = FakeWindow(30, 140, [10])
    assert tui._prompt_new_run_modal(s2, palette, "p", "roadmap.md") == ("roadmap.md", "codex")

    keys = [21, 10, ord("r"), ord("."), ord("m"), 9, 21, 10, ord("c"), ord("o"), ord("d"), ord("e"), ord("x"), tui.curses.KEY_UP, tui.curses.KEY_DOWN, tui.curses.KEY_BACKSPACE, ord("x"), 10]
    s3 = FakeWindow(30, 140, keys)
    roadmap_value, agent_value = tui._prompt_new_run_modal(s3, palette, "p", "roadmap.md")
    assert roadmap_value == "r.m"
    assert agent_value.endswith("x")


def test_prompt_new_run_modal_enter_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: (_ for _ in ()).throw(tui.curses.error("x")))
    s = FakeWindow(30, 140, [tui.curses.KEY_ENTER])
    assert tui._prompt_new_run_modal(s, palette, "p", "roadmap.md") == ("roadmap.md", "codex")


def test_prompt_new_run_modal_panel_none(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    s = FakeWindow(30, 140, [10])
    assert tui._prompt_new_run_modal(s, palette, "p", "roadmap.md") is None


def test_prompt_new_run_modal_move_error(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    s = FakeWindow(30, 140, [10])
    s.raise_on_move = True
    assert tui._prompt_new_run_modal(s, palette, "p", "roadmap.md") == ("roadmap.md", "codex")


def test_prompt_workflow_modal_cancel_and_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {
        "border": 0,
        "info": 0,
        "muted": 0,
        "failed": 0,
        "selected": 0,
    }
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)

    s1 = FakeWindow(30, 140, [27])
    assert tui._prompt_workflow_modal(s1, palette, "p", "roadmap.md") is None

    s2 = FakeWindow(30, 140, [10])
    assert tui._prompt_workflow_modal(s2, palette, "p", "roadmap.md") == ("roadmap.md", "codex", "codex")

    keys = [
        21,
        10,
        ord("r"),
        ord("."),
        ord("m"),
        9,
        21,
        10,
        ord("c"),
        ord("o"),
        ord("d"),
        ord("e"),
        ord("x"),
        9,
        21,
        10,
        ord("g"),
        ord("e"),
        ord("m"),
        ord("i"),
        ord("n"),
        ord("i"),
        tui.curses.KEY_UP,
        tui.curses.KEY_DOWN,
        tui.curses.KEY_BACKSPACE,
        ord("i"),
        10,
    ]
    s3 = FakeWindow(30, 140, keys)
    roadmap_value, coach_agent, coding_agent = tui._prompt_workflow_modal(s3, palette, "p", "roadmap.md")
    assert roadmap_value == "r.m"
    assert coach_agent == "codex"
    assert coding_agent.endswith("i")


def test_prompt_workflow_modal_panel_none_and_move_error(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)

    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    s = FakeWindow(30, 140, [10])
    assert tui._prompt_workflow_modal(s, palette, "p", "roadmap.md") is None

    monkeypatch.undo()
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    s2 = FakeWindow(30, 140, [10])
    s2.raise_on_move = True
    assert tui._prompt_workflow_modal(s2, palette, "p", "roadmap.md") == ("roadmap.md", "codex", "codex")


def test_prompt_workflow_modal_curs_set_error(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0}
    monkeypatch.setattr(
        tui.curses,
        "curs_set",
        lambda *_a, **_k: (_ for _ in ()).throw(tui.curses.error("x")),
    )
    s = FakeWindow(30, 140, [tui.curses.KEY_ENTER])
    assert tui._prompt_workflow_modal(s, palette, "p", "roadmap.md") == ("roadmap.md", "codex", "codex")


def test_prompt_confirm_modal_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0}
    s1 = FakeWindow(30, 140, [27])
    assert tui._prompt_confirm_modal(s1, palette, "T", ["L"]) is False
    s2 = FakeWindow(30, 140, [ord("n")])
    assert tui._prompt_confirm_modal(s2, palette, "T", ["L"]) is False
    s3 = FakeWindow(30, 140, [ord("y")])
    assert tui._prompt_confirm_modal(s3, palette, "T", ["L"]) is True
    s4 = FakeWindow(30, 140, [10])
    assert tui._prompt_confirm_modal(s4, palette, "T", ["L"]) is True

    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    s5 = FakeWindow(30, 140, [10])
    assert tui._prompt_confirm_modal(s5, palette, "T", ["L"]) is False


def test_prompt_add_project_batch_and_reply_modals(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0, "warn": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)

    s1 = FakeWindow(30, 140, [27])
    assert tui._prompt_add_project_modal(s1, palette) is None

    keys = [10, ord("/"), ord("t"), ord("m"), ord("p"), 9, ord("p"), 9, ord("g"), 10]
    s2 = FakeWindow(30, 140, keys)
    root, name, g = tui._prompt_add_project_modal(s2, palette)
    assert root == "/tmp"
    assert name == "p"
    assert g == "g"

    s3 = FakeWindow(30, 140, [27])
    assert tui._prompt_batch_modal(s3, palette, "proj") is None
    s4 = FakeWindow(30, 140, [10, 27])
    assert tui._prompt_batch_modal(s4, palette, "proj") is None

    s5 = FakeWindow(30, 140, [ord("B"), 9, ord("c"), ord("o"), ord("d"), ord("e"), ord("x"), 9, ord("a"), ord(";"), ord("b"), 10])
    name2, agent2, tasks2 = tui._prompt_batch_modal(s5, palette, "proj")
    assert name2.startswith("Roadmap Batch")
    assert agent2.endswith("codex")
    assert tasks2 == "a\nb"

    s6 = FakeWindow(30, 140, [27])
    assert tui._prompt_task_reply_modal(s6, palette, 1, "Q?") is None
    s7 = FakeWindow(30, 140, [10, ord("x"), 10])
    assert tui._prompt_task_reply_modal(s7, palette, 1, "Q?") == "x"

    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    s8 = FakeWindow(30, 140, [10])
    assert tui._prompt_add_project_modal(s8, palette) is None


def test_new_modals_extra_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0, "warn": 0}
    monkeypatch.setattr(
        tui.curses,
        "curs_set",
        lambda *_a, **_k: (_ for _ in ()).throw(tui.curses.error("x")),
    )
    s1 = FakeWindow(30, 140, [tui.curses.KEY_DOWN, tui.curses.KEY_UP, tui.curses.KEY_BACKSPACE, 21, ord("/"), 10])
    s1.raise_on_move = True
    assert tui._prompt_add_project_modal(s1, palette)[0] == "/"

    s2 = FakeWindow(30, 140, [tui.curses.KEY_DOWN, tui.curses.KEY_UP, tui.curses.KEY_BACKSPACE, 21, ord("n"), 9, ord("a"), 9, ord("x"), 10])
    s2.raise_on_move = True
    assert tui._prompt_batch_modal(s2, palette, "p")[0].endswith("n")

    s3 = FakeWindow(30, 140, [tui.curses.KEY_BACKSPACE, 21, ord("a"), 10])
    s3.raise_on_move = True
    assert tui._prompt_task_reply_modal(s3, palette, 1, "Q") == "a"

    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    assert tui._prompt_batch_modal(FakeWindow(30, 140, [10]), palette, "p") is None
    assert tui._prompt_task_reply_modal(FakeWindow(30, 140, [10]), palette, 1, "Q") is None

    conn = db.connect(tmp_path / "db.sqlite")
    root = tmp_path / "repo"
    root.mkdir()
    g = tmp_path / "guidelines.md"
    g.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(tui, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(tui.GitRepoError("x")))
    ok, _msg = tui._add_project_from_root(conn, str(root), "n", str(g))
    assert ok is True


def test_fetch_task_helpers_and_add_project_from_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn) -> None:
    pid = db.create_project(conn, "proj", str(tmp_path / "repo"), "g")
    batch_id = db.create_task_batch(conn, project_id=pid, name="b", source_text="s", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T1", description="d1")
    db.set_task_state(conn, task_id, "awaiting_input", blocked_question="q?")
    db.set_task_state(conn, task_id, "mystery")
    db.add_task_event(conn, task_id, "warn", "w")
    db.create_alert(conn, level="warn", kind="blocked", message="m", task_id=task_id, project_id=pid)

    assert tui._fetch_tasks(conn, None)
    assert tui._fetch_tasks(conn, "proj")
    counts = tui._fetch_task_status_counts(conn)
    assert counts["other"] >= 1
    assert tui._fetch_open_alerts(conn)
    assert tui._fetch_task_events(conn, task_id)

    ok, msg = tui._add_project_from_root(conn, str(tmp_path / "missing"), "", "")
    assert ok is False and "does not exist" in msg

    root = tmp_path / "root"
    root.mkdir()
    ok2, msg2 = tui._add_project_from_root(conn, str(root), "", str(tmp_path / "no-guidelines.md"))
    assert ok2 is False and "Guidelines file not found" in msg2

    monkeypatch.setattr(tui, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(tui.GitRepoError("bad")))
    ok3, msg3 = tui._add_project_from_root(conn, str(root), "name", "")
    assert ok3 is True and "non-git" in msg3

    monkeypatch.setattr(
        tui,
        "detect_repo",
        lambda *_a, **_k: GitRepoInfo(root_path=str(root), remote_url="u", default_branch="main", head_sha="sha"),
    )
    ok4, msg4 = tui._add_project_from_root(conn, str(root), "", "")
    assert ok4 is True and "Project added" in msg4


def test_with_curses_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tui.curses, "def_prog_mode", lambda: calls.append("def"))
    monkeypatch.setattr(tui.curses, "endwin", lambda: calls.append("end"))
    monkeypatch.setattr(tui.curses, "reset_prog_mode", lambda: calls.append("reset"))
    w = FakeWindow()
    assert tui._with_curses_paused(w, lambda: "ok") == "ok"
    assert calls == ["def", "end", "reset"]

    monkeypatch.setattr(
        tui.curses, "def_prog_mode", lambda: (_ for _ in ()).throw(tui.curses.error("x"))
    )
    monkeypatch.setattr(
        tui.curses, "reset_prog_mode", lambda: (_ for _ in ()).throw(tui.curses.error("x"))
    )
    assert tui._with_curses_paused(w, lambda: "still-ok") == "still-ok"

    class _BadWindow(FakeWindow):
        def erase(self):
            raise tui.curses.error("erase")

    monkeypatch.setattr(tui.curses, "def_prog_mode", lambda: None)
    monkeypatch.setattr(tui.curses, "endwin", lambda: None)
    monkeypatch.setattr(tui.curses, "reset_prog_mode", lambda: None)
    bad = _BadWindow()
    assert tui._with_curses_paused(bad, lambda: "done") == "done"


def test_run_roadmap_coach_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {
        "border": 0,
        "info": 0,
        "muted": 0,
        "warn": 0,
        "focused": 0,
        "selected": 0,
    }
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)

    # Happy path: send a message, then exit.
    s1 = FakeWindow(30, 140, [ord("h"), ord("i"), 10, 27])
    monkeypatch.setattr(tui, "list_windows", lambda *_a, **_k: ["coach"])
    monkeypatch.setattr(tui, "capture_pane", lambda *_a, **_k: "agent line")
    sent: list[str] = []
    monkeypatch.setattr(tui, "send_text", lambda _t, text, press_enter=True: sent.append(text))
    ok, msg = tui._run_roadmap_coach_inline(s1, palette, "sess", "p")
    assert ok is True
    assert "ended" in msg.lower()
    assert sent == ["hi"]

    # Enter when session is gone exits cleanly.
    s2 = FakeWindow(30, 140, [10])
    monkeypatch.setattr(tui, "list_windows", lambda *_a, **_k: ["control"])
    ok2, msg2 = tui._run_roadmap_coach_inline(s2, palette, "sess", "p")
    assert ok2 is True
    assert "already ended" in msg2

    # Small terminal/panel creation failure.
    monkeypatch.setattr(tui, "_new_panel", lambda *_a, **_k: None)
    s3 = FakeWindow(30, 140, [27])
    ok3, msg3 = tui._run_roadmap_coach_inline(s3, palette, "sess", "p")
    assert ok3 is False
    assert "too small" in msg3.lower()


def test_run_roadmap_coach_inline_edge_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {
        "border": 0,
        "info": 0,
        "muted": 0,
        "warn": 0,
        "focused": 0,
        "selected": 0,
    }
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)

    # list_windows tmux error -> treated as ended.
    monkeypatch.setattr(tui, "list_windows", lambda *_a, **_k: (_ for _ in ()).throw(tui.TmuxError("x")))
    monkeypatch.setattr(tui, "capture_pane", lambda *_a, **_k: "ignored")
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: (_ for _ in ()).throw(tui.curses.error("x")))
    ok1, msg1 = tui._run_roadmap_coach_inline(FakeWindow(30, 140, [10]), palette, "sess", "p")
    assert ok1 is True
    assert "already ended" in msg1

    # capture_pane tmux error branch + move error branch.
    monkeypatch.setattr(tui, "list_windows", lambda *_a, **_k: ["coach"])
    monkeypatch.setattr(tui, "capture_pane", lambda *_a, **_k: (_ for _ in ()).throw(tui.TmuxError("cap")))
    w2 = FakeWindow(30, 140, [27])
    w2.raise_on_move = True
    ok2, msg2 = tui._run_roadmap_coach_inline(w2, palette, "sess", "p")
    assert ok2 is True
    assert "ended" in msg2.lower()

    # Backspace + Ctrl+U + idle(-1) branches.
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    monkeypatch.setattr(tui, "capture_pane", lambda *_a, **_k: "agent")
    monkeypatch.setattr(tui, "list_windows", lambda *_a, **_k: ["coach"])
    w3 = FakeWindow(30, 140, [ord("a"), tui.curses.KEY_BACKSPACE, ord("b"), 21, -1, 27])
    ok3, msg3 = tui._run_roadmap_coach_inline(w3, palette, "sess", "p")
    assert ok3 is True
    assert "ended" in msg3.lower()


def test_start_and_validate_workflow_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stdscr = FakeWindow()
    palette = {"border": 0, "info": 0, "muted": 0, "warn": 0, "focused": 0, "selected": 0}
    project_root = tmp_path / "proj"
    project_root.mkdir()
    db_path = tmp_path / "db.sqlite"

    monkeypatch.setattr(
        tui,
        "start_roadmap_coach",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(tui, "kill_session", lambda *_a, **_k: None)
    ok, msg, path = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "roadmap.md",
        "codex",
        "codex",
    )
    assert ok is False
    assert "Roadmap coach failed" in msg
    assert path.endswith("roadmap.md")

    monkeypatch.setattr(tui, "start_roadmap_coach", lambda *_a, **_k: "sess")
    monkeypatch.setattr(tui, "_run_roadmap_coach_inline", lambda *_a, **_k: (False, "inline-boom"))
    ok2, msg2, path2 = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "roadmap.md",
        "codex",
        "codex",
    )
    assert ok2 is False
    assert "Roadmap coach failed: inline-boom" in msg2
    assert path2.endswith("roadmap.md")

    monkeypatch.setattr(tui, "_run_roadmap_coach_inline", lambda *_a, **_k: (True, "ok"))
    ok_missing, msg_missing, _ = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "roadmap.md",
        "codex",
        "codex",
    )
    assert ok_missing is False
    assert "Roadmap not found after coach session sess" in msg_missing

    monkeypatch.setattr(
        tui,
        "kill_session",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("kill")),
    )
    ok_missing2, msg_missing2, _ = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "still-missing.md",
        "codex",
        "codex",
    )
    assert ok_missing2 is False
    assert "Roadmap not found after coach session sess" in msg_missing2

    monkeypatch.setattr(tui, "kill_session", lambda *_a, **_k: None)
    roadmap_path = project_root / "roadmap.md"
    roadmap_path.write_text("### bad", encoding="utf-8")
    monkeypatch.setattr(
        tui,
        "load_roadmap",
        lambda *_a, **_k: (_ for _ in ()).throw(tui.RoadmapValidationError("invalid")),
    )
    ok3, msg3, _ = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "roadmap.md",
        "codex",
        "codex",
    )
    assert ok3 is False
    assert "Roadmap validation failed: invalid" in msg3

    stage = StageDef(id="s1", title="S1", goal="g", timeout_minutes=1)
    track = TrackDef(id="t1", topic="topic", agent="codex", stages=[stage])
    rm = RoadmapDef(version=1, name="rm", guidelines=[], tracks=[track], raw_text="raw")
    monkeypatch.setattr(tui, "load_roadmap", lambda *_a, **_k: rm)
    ok4, msg4, resolved = tui._start_and_validate_workflow(
        stdscr,
        palette,
        db_path,
        "p",
        str(project_root),
        "roadmap.md",
        "codex",
        "codex",
    )
    assert ok4 is True
    assert "Validated roadmap 'rm' (1 tracks, 1 stages)." == msg4
    assert resolved.endswith("roadmap.md")


def test_launch_run_background_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    roadmap = project_root / "roadmap.md"
    roadmap.write_text("x", encoding="utf-8")

    ok, msg = tui._launch_run_in_background(tmp_path / "db.sqlite", "p", str(project_root), "missing.md", "codex")
    assert ok is False
    assert "Roadmap not found" in msg

    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(tui.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok2, msg2 = tui._launch_run_in_background(tmp_path / "db.sqlite", "p", str(project_root), "roadmap.md", "codex")
    assert ok2 is False
    assert "Failed to launch run" in msg2

    monkeypatch.setattr(tui.subprocess, "Popen", lambda *_a, **_k: FakeProc(rc=1))
    ok3, msg3 = tui._launch_run_in_background(tmp_path / "db.sqlite", "p", str(project_root), "roadmap.md", "codex")
    assert ok3 is False
    assert "Run launch failed" in msg3

    monkeypatch.setattr(tui.subprocess, "Popen", lambda *_a, **_k: FakeProc(pid=999, rc=None))
    ok4, msg4 = tui._launch_run_in_background(tmp_path / "db.sqlite", "p", str(project_root), "roadmap.md", "codex")
    assert ok4 is True
    assert "pid 999" in msg4

    ok5, msg5 = tui._launch_run_in_background(
        tmp_path / "db.sqlite",
        "p",
        str(project_root),
        str(roadmap.resolve()),
        "codex",
    )
    assert ok5 is True
    assert "pid 999" in msg5


def test_run_tui_small_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_db(db_path, with_runs=False)

    _patch_curses(monkeypatch, [ord("r"), ord("q")], size=(10, 50), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_no_projects_and_warn_new_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    db.connect(db_path).close()

    _patch_curses(monkeypatch, [ord("n"), ord("w"), 9, ord("r"), ord("q")], size=(30, 130), has_colors=True)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_with_runs_modal_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_db(db_path, with_runs=True, with_tracks=True, run_count=3, track_count=12)

    keys = [
        tui.curses.KEY_DOWN,
        10,
        9,
        tui.curses.KEY_DOWN,
        tui.curses.KEY_UP,
        tui.curses.KEY_NPAGE,
        tui.curses.KEY_PPAGE,
        ord("g"),
        ord("G"),
        ord("n"),
        ord("n"),
        ord("n"),
        9,
        tui.curses.KEY_DOWN,
        tui.curses.KEY_UP,
        tui.curses.KEY_NPAGE,
        tui.curses.KEY_PPAGE,
        ord("g"),
        ord("G"),
        ord("q"),
    ]
    _patch_curses(monkeypatch, keys, size=(32, 140), has_colors=False)

    modal_calls = {"n": 0}

    def fake_modal(**_k):
        modal_calls["n"] += 1
        if modal_calls["n"] == 1:
            return ("roadmap.md", "codex")
        if modal_calls["n"] == 2:
            return ("roadmap.md", "codex")
        return None

    launch_calls = {"n": 0}

    def fake_launch(**_k):
        launch_calls["n"] += 1
        if launch_calls["n"] == 1:
            return True, "ok"
        return False, "bad"

    monkeypatch.setattr(tui, "_prompt_new_run_modal", fake_modal)
    monkeypatch.setattr(tui, "_launch_run_in_background", fake_launch)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_with_runs_but_no_tracks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_db(db_path, with_runs=True, with_tracks=False)

    _patch_curses(monkeypatch, [tui.curses.KEY_DOWN, ord("q")], size=(30, 130), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_workflow_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_db(db_path, with_runs=True, with_tracks=False, run_count=1)

    keys = [tui.curses.KEY_DOWN, ord("w"), ord("w"), ord("w"), ord("w"), ord("q")]
    _patch_curses(monkeypatch, keys, size=(32, 140), has_colors=False)

    modal_calls = {"n": 0}

    def fake_workflow_modal(**_k):
        modal_calls["n"] += 1
        if modal_calls["n"] == 1:
            return None
        return ("roadmap.md", "codex", "codex")

    validate_calls = {"n": 0}

    def fake_validate(**_k):
        validate_calls["n"] += 1
        if validate_calls["n"] == 1:
            return False, "bad roadmap", "/tmp/roadmap.md"
        return True, "ok roadmap", "/tmp/roadmap.md"

    confirm_calls = {"n": 0}

    def fake_confirm(**_k):
        confirm_calls["n"] += 1
        return confirm_calls["n"] > 1

    monkeypatch.setattr(tui, "_prompt_workflow_modal", fake_workflow_modal)
    monkeypatch.setattr(tui, "_start_and_validate_workflow", fake_validate)
    monkeypatch.setattr(tui, "_prompt_confirm_modal", fake_confirm)
    monkeypatch.setattr(tui, "_launch_run_in_background", lambda **_k: (True, "launched"))
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_run_focus_navigation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_db(db_path, with_runs=True, with_tracks=False, run_count=12)

    keys = [
        10,
        tui.curses.KEY_DOWN,
        tui.curses.KEY_UP,
        tui.curses.KEY_NPAGE,
        tui.curses.KEY_PPAGE,
        ord("g"),
        ord("G"),
        ord("q"),
    ]
    _patch_curses(monkeypatch, keys, size=(32, 140), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_new_scheduler_and_task_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = db.create_project(conn, "p1", "/tmp/p1", "g")
    batch_id = db.create_task_batch(conn, project_id=pid, name="b", source_text="s", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T", description="d")
    conn.execute(
        "UPDATE tasks SET status='awaiting_input', blocked_question='q?', tmux_target='sess:task.0', assigned_agent='codex' WHERE id = ?",
        (task_id,),
    )
    conn.commit()

    keys = [
        ord("a"),
        tui.curses.KEY_DOWN,
        ord("b"),
        ord("s"),
        ord("z"),
        ord("v"),
        ord("y"),
        ord("q"),
    ]
    _patch_curses(monkeypatch, keys, size=(34, 150), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    monkeypatch.setattr(tui, "_prompt_add_project_modal", lambda *_a, **_k: ("/tmp/p2", "", ""))
    monkeypatch.setattr(tui, "_add_project_from_root", lambda *_a, **_k: (True, "added"))
    monkeypatch.setattr(tui, "_prompt_batch_modal", lambda *_a, **_k: ("B1", "codex", "t1\nt2"))
    monkeypatch.setattr(tui, "create_batch_from_task_list", lambda **_k: 9)
    monkeypatch.setattr(tui, "_prompt_task_reply_modal", lambda *_a, **_k: "answer")

    class _Stats:
        dispatched = 1
        completed = 0
        awaiting_input = 1
        reassigned = 0
        failed = 0

    class _Scheduler:
        def __init__(self, **_k):
            self.replies: list[tuple[int, str]] = []

        def tick(self):
            return _Stats()

        def reply_to_task(self, task_id: int, answer: str) -> None:
            self.replies.append((task_id, answer))

    monkeypatch.setattr(tui, "GlobalScheduler", _Scheduler)
    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_new_error_paths_and_task_navigation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = db.create_project(conn, "p1", "/tmp/p1", "g")
    batch_id = db.create_task_batch(conn, project_id=pid, name="b", source_text="s", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T", description="d")
    conn.execute(
        "UPDATE tasks SET status='running', blocked_question='', tmux_target='sess:task.0', assigned_agent='codex' WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    db.create_alert(conn, level="warn", kind="stuck", message="stuck", task_id=task_id, project_id=pid)

    keys = [
        ord("z"),  # enable auto scheduler
        ord("s"),  # manual tick failure path
        ord("a"),  # add cancelled
        ord("b"),  # batch warn when project filter not selected
        ord("v"),  # tasks mode
        ord("y"),  # selected task not awaiting
        tui.curses.KEY_DOWN,
        tui.curses.KEY_UP,
        tui.curses.KEY_NPAGE,
        tui.curses.KEY_PPAGE,
        ord("g"),
        ord("G"),
        ord("q"),
    ]
    _patch_curses(monkeypatch, keys, size=(34, 150), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])
    monkeypatch.setattr(tui, "_prompt_add_project_modal", lambda *_a, **_k: None)

    class _SchedErr:
        def __init__(self, **_k):
            return

        def tick(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(tui, "GlobalScheduler", _SchedErr)
    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_run_tui_batch_cancel_fail_and_reply_cancel_or_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = db.create_project(conn, "p1", "/tmp/p1", "g")
    batch_id = db.create_task_batch(conn, project_id=pid, name="b", source_text="s", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T", description="d")
    conn.execute(
        "UPDATE tasks SET status='awaiting_input', blocked_question='q?', tmux_target='sess:task.0', assigned_agent='codex' WHERE id = ?",
        (task_id,),
    )
    conn.commit()

    keys = [
        tui.curses.KEY_DOWN,
        ord("b"),  # cancel batch modal
        ord("b"),  # batch failure
        ord("v"),
        ord("y"),  # reply cancelled
        ord("y"),  # reply send failure
        ord("q"),
    ]
    _patch_curses(monkeypatch, keys, size=(34, 150), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])

    calls = {"n": 0}

    def fake_batch_modal(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("B", "codex", "tasks")

    monkeypatch.setattr(tui, "_prompt_batch_modal", fake_batch_modal)
    monkeypatch.setattr(
        tui,
        "create_batch_from_task_list",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("bad batch")),
    )

    reply_calls = {"n": 0}

    def fake_reply_modal(*_a, **_k):
        reply_calls["n"] += 1
        if reply_calls["n"] == 1:
            return None
        return "answer"

    monkeypatch.setattr(tui, "_prompt_task_reply_modal", fake_reply_modal)

    class _Sched:
        def __init__(self, **_k):
            return

        def tick(self):
            class _Stats:
                dispatched = completed = awaiting_input = reassigned = failed = 0

            return _Stats()

        def reply_to_task(self, *_a, **_k):
            raise RuntimeError("send fail")

    monkeypatch.setattr(tui, "GlobalScheduler", _Sched)
    tui.run_tui(db_path=db_path, refresh_seconds=0.01)


def test_prompt_batch_modal_empty_name_and_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    palette = {"border": 0, "info": 0, "muted": 0, "failed": 0, "selected": 0, "warn": 0}
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_a, **_k: None)
    keys = [
        21,   # clear name
        10,   # error: empty name
        ord("n"),
        9,    # agent field
        21,   # clear agent
        10,   # error: empty agent
        27,
    ]
    assert tui._prompt_batch_modal(FakeWindow(30, 140, keys), palette, "p") is None


def test_run_tui_tasks_view_no_tasks_and_reply_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = db.connect(db_path)
    pid = db.create_project(conn, "p1", "/tmp/p1", "g")
    # Ensure there is at least one awaiting task for reply success path.
    batch_id = db.create_task_batch(conn, project_id=pid, name="b", source_text="s", status="queued")
    task_id = db.create_task(conn, batch_id=batch_id, project_id=pid, title="T", description="d")
    conn.execute(
        "UPDATE tasks SET status='awaiting_input', blocked_question='q?', tmux_target='sess:task.0', assigned_agent='codex' WHERE id = ?",
        (task_id,),
    )
    conn.commit()

    # First run: tasks footer + reply success.
    keys1 = [tui.curses.KEY_DOWN, ord("v"), ord("y"), ord("q")]
    _patch_curses(monkeypatch, keys1, size=(34, 150), has_colors=False)
    monkeypatch.setattr(tui.time, "sleep", lambda *_a, **_k: None)
    ctr = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr.__setitem__("v", ctr["v"] + 1.0) or ctr["v"])
    monkeypatch.setattr(tui, "_prompt_task_reply_modal", lambda *_a, **_k: "ok")

    class _SchedOk:
        def __init__(self, **_k):
            return

        def tick(self):
            class _Stats:
                dispatched = completed = awaiting_input = reassigned = failed = 0

            return _Stats()

        def reply_to_task(self, *_a, **_k):
            return None

    monkeypatch.setattr(tui, "GlobalScheduler", _SchedOk)
    tui.run_tui(db_path=db_path, refresh_seconds=0.01)

    # Second run: no tasks in tasks view branch.
    conn.execute("DELETE FROM tasks")
    conn.commit()
    keys2 = [tui.curses.KEY_DOWN, ord("v"), ord("q")]
    _patch_curses(monkeypatch, keys2, size=(34, 150), has_colors=False)
    ctr2 = {"v": 0.0}
    monkeypatch.setattr(tui.time, "monotonic", lambda: ctr2.__setitem__("v", ctr2["v"] + 1.0) or ctr2["v"])
    tui.run_tui(db_path=db_path, refresh_seconds=0.01)
