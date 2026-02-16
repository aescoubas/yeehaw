from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yeehaw import runner
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


@dataclass
class _UUID:
    hex: str


def _mk_roadmap(timeout_minutes: int = 1) -> RoadmapDef:
    st = StageDef(id="s1", title="S1", goal="g", instructions="", deliverables=[], timeout_minutes=timeout_minutes)
    tr = TrackDef(id="t1", topic="topic", agent="codex", command="cmd", stages=[st])
    return RoadmapDef(version=1, name="rm", guidelines=[], tracks=[tr], raw_text="raw")


def test_helpers() -> None:
    assert runner._safe_session_name("p", "x y").startswith("p-x-y-")
    assert runner._state_terminal("completed") is True
    assert runner._state_terminal("ready") is False

    txt = "x\n[[YEEHAW_DONE tok]]\nSummary:\n- a\nArtifacts:\n- f\n"
    s, a = runner._parse_summary_and_artifacts(txt, "[[YEEHAW_DONE tok]]")
    assert "a" in s and "f" in a
    s2, a2 = runner._parse_summary_and_artifacts("none", "marker")
    assert s2 == "" and a2 == ""
    long_tail = "\n".join(["[[YEEHAW_DONE t]]", "Summary:"] + [f"- s{i}" for i in range(20)] + ["Artifacts:"] + [f"- a{i}" for i in range(30)])
    s3, a3 = runner._parse_summary_and_artifacts(long_tail, "[[YEEHAW_DONE t]]")
    assert len(s3.splitlines()) == 8
    assert len(a3.splitlines()) == 20

    q = runner._extract_question("[[YEEHAW_NEEDS_INPUT x]]\nQuestion: what now?", "[[YEEHAW_NEEDS_INPUT x]]")
    assert q == "what now?"
    q2 = runner._extract_question("[[YEEHAW_NEEDS_INPUT x]]\nno question", "[[YEEHAW_NEEDS_INPUT x]]")
    assert "requested input" in q2
    assert runner._extract_question("none", "x") == ""


def test_run_roadmap_project_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner.db, "connect", lambda *_a, **_k: object())
    monkeypatch.setattr(runner.db, "get_project", lambda *_a, **_k: None)
    with pytest.raises(ValueError, match="not found"):
        runner.run_roadmap("p", tmp_path / "r.yaml")


def _patch_common(monkeypatch: pytest.MonkeyPatch, mode: str, include_repo_meta: bool = True) -> dict:
    state: dict = {
        "events": [],
        "run_status": [],
        "track_state": [],
        "stage_completed": [],
        "set_await": [],
        "markers": {},
        "captures": 0,
    }
    conn = object()
    project = {
        "id": 1,
        "name": "proj",
        "root_path": "/tmp/proj",
        "guidelines": "g",
        "git_remote_url": "r" if include_repo_meta else None,
        "default_branch": "main" if include_repo_meta else None,
        "head_sha": "sha" if include_repo_meta else None,
    }

    monkeypatch.setattr(runner.db, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(runner.db, "get_project", lambda *_a, **_k: project)
    monkeypatch.setattr(runner.db, "insert_roadmap", lambda *_a, **_k: 11)
    monkeypatch.setattr(runner.db, "create_run", lambda *_a, **_k: 22)
    monkeypatch.setattr(runner.db, "create_track_run", lambda *_a, **_k: 33)
    monkeypatch.setattr(runner.db, "get_stage_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr(runner.db, "create_stage_run", lambda *_a, **_k: 44)
    monkeypatch.setattr(runner.db, "add_event", lambda *_a, **k: state["events"].append(k.get("message") or _a[3]))
    monkeypatch.setattr(
        runner.db,
        "set_track_run_state",
        lambda *_a, **k: state["track_state"].append(k),
    )
    monkeypatch.setattr(
        runner.db,
        "complete_stage_run",
        lambda *_a, **k: state["stage_completed"].append(k),
    )
    monkeypatch.setattr(
        runner.db,
        "set_stage_run_awaiting_input",
        lambda *_a, **k: state["set_await"].append(k),
    )
    monkeypatch.setattr(
        runner.db,
        "set_run_status",
        lambda *_a, **k: state["run_status"].append((_a[2], k.get("finished", False))),
    )

    monkeypatch.setattr(runner, "ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "ensure_window", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(runner, "load_roadmap", lambda *_a, **_k: _mk_roadmap(timeout_minutes=1))
    monkeypatch.setattr(runner.time, "sleep", lambda *_a, **_k: None)

    def fake_send(_target: str, text: str, press_enter: bool = True) -> None:
        for line in text.splitlines():
            if line.startswith("[[YEEHAW_DONE"):
                state["markers"]["done"] = line.strip()
            if line.startswith("[[YEEHAW_NEEDS_INPUT"):
                state["markers"]["input"] = line.strip()

    monkeypatch.setattr(runner, "send_text", fake_send)

    monotonic_values = [0.0, 0.1, 0.2, 0.3, 9999.0, 10000.0]

    def fake_monotonic() -> float:
        return monotonic_values.pop(0) if monotonic_values else 10000.0

    monkeypatch.setattr(runner.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(runner.uuid, "uuid4", lambda: _UUID(hex="0123456789abcdef"))

    def fake_capture(_target: str) -> str:
        state["captures"] += 1
        if state["captures"] == 1:
            return "boot"
        done = state["markers"]["done"]
        need = state["markers"]["input"]
        if mode == "done":
            return f"x\n{done}\nSummary:\n- ok\nArtifacts:\n- a.txt\n"
        if mode == "await":
            return f"x\n{need}\nQuestion: please decide\n"
        return "still running"

    monkeypatch.setattr(runner, "capture_pane", fake_capture)
    return state


def test_run_roadmap_done(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = _patch_common(monkeypatch, mode="done", include_repo_meta=True)
    run_id = runner.run_roadmap("proj", tmp_path / "r.md", poll_seconds=0.01)
    assert run_id == 22
    assert ("completed", True) in state["run_status"]
    assert state["stage_completed"]


def test_run_roadmap_awaiting_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = _patch_common(monkeypatch, mode="await", include_repo_meta=False)
    run_id = runner.run_roadmap("proj", tmp_path / "r.md", poll_seconds=0.01)
    assert run_id == 22
    assert ("awaiting_input", False) in state["run_status"]
    assert state["set_await"]


def test_run_roadmap_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = _patch_common(monkeypatch, mode="timeout", include_repo_meta=False)
    run_id = runner.run_roadmap("proj", tmp_path / "r.md", poll_seconds=0.01)
    assert run_id == 22
    assert ("failed", True) in state["run_status"]


def test_run_roadmap_tmux_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = object()
    project = {"id": 1, "name": "p", "root_path": "/tmp", "guidelines": ""}
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(runner.db, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(runner.db, "get_project", lambda *_a, **_k: project)
    monkeypatch.setattr(runner.db, "insert_roadmap", lambda *_a, **_k: 1)
    monkeypatch.setattr(runner.db, "create_run", lambda *_a, **_k: 2)
    monkeypatch.setattr(runner.db, "add_event", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.db, "set_run_status", lambda *_a, **k: calls.append((_a[2], k.get("finished", False))))
    monkeypatch.setattr(runner, "load_roadmap", lambda *_a, **_k: _mk_roadmap())

    def boom(*_a, **_k):
        raise runner.TmuxError("boom")

    monkeypatch.setattr(runner, "ensure_session", boom)

    with pytest.raises(runner.TmuxError):
        runner.run_roadmap("p", tmp_path / "r.md")

    assert ("failed", True) in calls


def test_run_roadmap_skips_terminal_runtime_in_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = object()
    project = {"id": 1, "name": "p", "root_path": "/tmp/p", "guidelines": ""}
    state = {"run_status": [], "markers": {}, "captures": {}}

    st = StageDef(id="s1", title="S1", goal="g", timeout_minutes=1)
    t1 = TrackDef(id="a", topic="A", agent="codex", command="cmd", stages=[st])
    t2 = TrackDef(id="b", topic="B", agent="codex", command="cmd", stages=[st])
    rm = RoadmapDef(version=1, name="rm", guidelines=[], tracks=[t1, t2], raw_text="raw")

    monkeypatch.setattr(runner.db, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(runner.db, "get_project", lambda *_a, **_k: project)
    monkeypatch.setattr(runner.db, "insert_roadmap", lambda *_a, **_k: 1)
    monkeypatch.setattr(runner.db, "create_run", lambda *_a, **_k: 2)
    monkeypatch.setattr(runner.db, "create_track_run", lambda *_a, **_k: 3 if _a[2].id == "a" else 4)
    monkeypatch.setattr(runner.db, "get_stage_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr(runner.db, "create_stage_run", lambda *_a, **_k: 5)
    monkeypatch.setattr(runner.db, "set_stage_run_awaiting_input", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.db, "complete_stage_run", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.db, "set_track_run_state", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.db, "add_event", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.db, "set_run_status", lambda *_a, **k: state["run_status"].append((_a[2], k.get("finished", False))))

    monkeypatch.setattr(runner, "load_roadmap", lambda *_a, **_k: rm)
    monkeypatch.setattr(runner, "ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "ensure_window", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(runner.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(runner.uuid, "uuid4", lambda: _UUID(hex="aaaaaaaaaaaa"))

    def fake_send(target: str, text: str, press_enter: bool = True) -> None:
        for line in text.splitlines():
            if line.startswith("[[YEEHAW_DONE"):
                state["markers"].setdefault(target, {})["done"] = line.strip()
            if line.startswith("[[YEEHAW_NEEDS_INPUT"):
                state["markers"].setdefault(target, {})["input"] = line.strip()

    monkeypatch.setattr(runner, "send_text", fake_send)

    def fake_capture(target: str) -> str:
        c = state["captures"].get(target, 0) + 1
        state["captures"][target] = c
        if c == 1:
            return "boot"
        markers = state["markers"][target]
        if target.endswith(":a.0"):
            return f"{markers['input']}\nQuestion: q"
        return f"{markers['done']}\nSummary:\n- ok\nArtifacts:\n- out"
    monkeypatch.setattr(runner, "capture_pane", fake_capture)

    run_id = runner.run_roadmap("p", tmp_path / "x.md", poll_seconds=0.01)
    assert run_id == 2
    assert ("awaiting_input", False) in state["run_status"]
