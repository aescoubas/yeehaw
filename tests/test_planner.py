"""Tests for planner session launching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yeehaw.planner.session import start_planner_session


def test_start_planner_session_claude_uses_mcp_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    briefing = tmp_path / "briefing.md"
    briefing.write_text("Ship API v1")

    captured: dict[str, object] = {}

    def fake_execvp(program: str, args: list[str]) -> None:
        captured["program"] = program
        captured["args"] = args
        raise RuntimeError("stop")

    def fake_unlink(path: str) -> None:
        captured["config_path"] = path

    monkeypatch.setattr("os.execvp", fake_execvp)
    monkeypatch.setattr("os.unlink", fake_unlink)

    with pytest.raises(RuntimeError, match="stop"):
        start_planner_session(db_path, briefing_file=briefing, agent="claude")

    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "claude"
    assert "--mcp-config" in args
    prompt = args[-1]
    assert isinstance(prompt, str)
    assert "## Briefing" in prompt
    assert "Ship API v1" in prompt

    config_path = captured["config_path"]
    assert isinstance(config_path, str)
    config = json.loads(Path(config_path).read_text())
    assert config["mcpServers"]["yeehaw"]["args"][-1] == str(db_path)


def test_start_planner_session_gemini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    captured: dict[str, object] = {}

    def fake_execvp(program: str, args: list[str]) -> None:
        captured["program"] = program
        captured["args"] = args
        raise RuntimeError("stop")

    monkeypatch.setattr("os.execvp", fake_execvp)
    monkeypatch.setattr("os.unlink", lambda _path: None)

    with pytest.raises(RuntimeError, match="stop"):
        start_planner_session(db_path, briefing_file=None, agent="gemini")

    assert captured["program"] == "gemini"


def test_start_planner_session_unsupported_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    called = {"unlink": 0}
    monkeypatch.setattr("os.unlink", lambda _path: called.__setitem__("unlink", 1))

    with pytest.raises(ValueError, match="Unsupported planner agent"):
        start_planner_session(db_path, agent="codex")

    assert called["unlink"] == 1
