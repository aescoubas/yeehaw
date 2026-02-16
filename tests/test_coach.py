from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw import coach
from yeehaw.git_repo import GitRepoInfo


def test_safe_session_name() -> None:
    name = coach._safe_session_name("pre", "my proj")
    assert name.startswith("pre-my-proj-")
    assert len(name) <= 60


def test_prompt_builders() -> None:
    repo = GitRepoInfo(root_path="/r", remote_url=None, default_branch=None, head_sha=None)
    p = coach._project_coach_prompt(repo, None, "/out.md", allow_non_git=True)
    assert "PROJECT_REGISTERED" in p

    r = coach._roadmap_coach_prompt("n", "/r", "/r/roadmap.md", "")
    assert "Execution Phases" in r


def test_ensure_window_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: ["coach"])
    coach._ensure_window_alive("s", "coach", "codex")

    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: ["control"])
    with pytest.raises(RuntimeError, match="exited before initialization"):
        coach._ensure_window_alive("s", "coach", "codex")


def test_start_project_coach(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = GitRepoInfo(root_path=str(repo_root), remote_url="u", default_branch="main", head_sha="sha")

    sent: dict[str, str] = {}
    monkeypatch.setattr(coach, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(coach, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(coach, "ensure_window", lambda *a, **k: None)
    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: ["project-coach"])
    monkeypatch.setattr(coach, "attach_session", lambda *_a, **_k: sent.setdefault("attached", "1"))
    monkeypatch.setattr(coach, "send_text", lambda _t, text, press_enter=True: sent.setdefault("prompt", text))
    monkeypatch.setattr(coach.time, "sleep", lambda *_a, **_k: None)

    session = coach.start_project_coach(
        repo=repo,
        agent="codex",
        guidelines_output="guidelines.md",
        name_hint="hint",
        attach=True,
    )
    assert session.startswith("yeehaw-project-coach-")
    assert "guidelines.md" in sent["prompt"]
    assert sent["attached"] == "1"


def test_start_roadmap_coach(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: dict[str, str] = {}

    monkeypatch.setattr(coach.db, "connect", lambda *_a, **_k: object())
    monkeypatch.setattr(coach.db, "get_project", lambda *_a, **_k: None)
    with pytest.raises(ValueError, match="not found"):
        coach.start_roadmap_coach("p", "roadmap.md", "codex")

    project = {
        "name": "p",
        "root_path": str(tmp_path),
        "guidelines": "g",
    }
    monkeypatch.setattr(coach.db, "get_project", lambda *_a, **_k: project)
    monkeypatch.setattr(coach, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(coach, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(coach, "ensure_window", lambda *a, **k: None)
    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: ["coach"])
    monkeypatch.setattr(coach, "send_text", lambda _t, text, press_enter=True: sent.setdefault("prompt", text))
    monkeypatch.setattr(coach, "attach_session", lambda *_a, **_k: sent.setdefault("attached", "1"))
    monkeypatch.setattr(coach.time, "sleep", lambda *_a, **_k: None)

    session = coach.start_roadmap_coach("p", "roadmap.md", "codex", attach=False)
    assert session.startswith("yeehaw-coach-")
    assert "Write the roadmap to:" in sent["prompt"]
    assert "attached" not in sent

    session2 = coach.start_roadmap_coach("p", "roadmap.md", "codex", attach=True)
    assert session2.startswith("yeehaw-coach-")
    assert sent["attached"] == "1"


def test_start_coach_fails_if_window_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = GitRepoInfo(root_path=str(tmp_path), remote_url=None, default_branch=None, head_sha=None)
    monkeypatch.setattr(coach, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(coach, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(coach, "ensure_window", lambda *a, **k: None)
    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: ["control"])
    monkeypatch.setattr(coach.time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="exited before initialization"):
        coach.start_project_coach(
            repo=repo,
            agent="codex",
            guidelines_output="guidelines.md",
            attach=False,
        )

    monkeypatch.setattr(coach.db, "connect", lambda *_a, **_k: object())
    monkeypatch.setattr(
        coach.db,
        "get_project",
        lambda *_a, **_k: {"name": "p", "root_path": str(tmp_path), "guidelines": ""},
    )
    with pytest.raises(RuntimeError, match="exited before initialization"):
        coach.start_roadmap_coach("p", "roadmap.md", "codex", attach=False)


def test_start_roadmap_coach_fails_if_window_exits_after_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(coach.db, "connect", lambda *_a, **_k: object())
    monkeypatch.setattr(
        coach.db,
        "get_project",
        lambda *_a, **_k: {"name": "p", "root_path": str(tmp_path), "guidelines": ""},
    )
    monkeypatch.setattr(coach, "resolve_command", lambda *_a, **_k: ("cmd", 0.0))
    monkeypatch.setattr(coach, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(coach, "ensure_window", lambda *a, **k: None)
    monkeypatch.setattr(coach, "send_text", lambda *_a, **_k: None)
    monkeypatch.setattr(coach.time, "sleep", lambda *_a, **_k: None)

    states = iter([["coach"], ["control"]])
    monkeypatch.setattr(coach, "list_windows", lambda *_a, **_k: next(states))

    with pytest.raises(RuntimeError, match="exited before initialization"):
        coach.start_roadmap_coach("p", "roadmap.md", "codex", attach=False)
