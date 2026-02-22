"""Tests for planner session launching."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import yeehaw.planner.session as planner_session
from yeehaw.planner.session import start_planner_session


def _completed(cmd: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode, "", "")


def test_start_planner_session_claude_uses_mcp_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    briefing = tmp_path / "briefing.md"
    briefing.write_text("Ship API v1")

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        config_idx = cmd.index("--mcp-config") + 1
        config_path = Path(cmd[config_idx])
        captured["config"] = json.loads(config_path.read_text())
        return _completed(cmd)

    monkeypatch.setattr(planner_session.subprocess, "run", fake_run)

    start_planner_session(
        db_path,
        briefing_file=briefing,
        agent="claude",
        project_name="demo",
    )

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "claude"
    assert "--mcp-config" in cmd
    prompt = cmd[-1]
    assert isinstance(prompt, str)
    assert "## Initial Briefing" in prompt
    assert "Ship API v1" in prompt
    assert "Target project: 'demo'." in prompt
    assert "Updated colorized roadmap preview" in prompt
    assert "never replace it with a summarized outline" in prompt

    config = captured["config"]
    assert isinstance(config, dict)
    assert config["mcpServers"]["yeehaw"]["args"][-1] == str(db_path)


def test_start_planner_session_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _completed(cmd)

    monkeypatch.setattr(planner_session.subprocess, "run", fake_run)

    start_planner_session(
        db_path,
        briefing_file=None,
        agent="codex",
        project_name="demo",
    )

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "codex"
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert any(
        isinstance(item, str) and item.startswith("mcp_servers.yeehaw.command=")
        for item in cmd
    )
    assert any(
        isinstance(item, str) and item.startswith("mcp_servers.yeehaw.args=")
        for item in cmd
    )
    prompt = cmd[-1]
    assert isinstance(prompt, str)
    assert "create_roadmap(project_name='demo'" in prompt
    assert "show the full returned preview verbatim" in prompt


def test_start_planner_session_gemini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    calls: list[tuple[list[str], Path | None]] = []

    class FakeUUID:
        hex = "abc12345deadbeef"

    monkeypatch.setattr(planner_session.uuid, "uuid4", lambda: FakeUUID())

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = kwargs.get("cwd")
        if isinstance(cwd, str):
            cwd = Path(cwd)
        calls.append((cmd, cwd if isinstance(cwd, Path) else None))
        return _completed(cmd)

    monkeypatch.setattr(planner_session.subprocess, "run", fake_run)

    start_planner_session(
        db_path,
        briefing_file=None,
        agent="gemini",
        project_name="demo",
    )

    assert len(calls) == 3
    add_cmd, add_cwd = calls[0]
    run_cmd, run_cwd = calls[1]
    remove_cmd, remove_cwd = calls[2]

    assert add_cmd[:3] == ["gemini", "mcp", "add"]
    assert add_cmd[3] == "yeehaw-abc12345"
    assert run_cmd[0] == "gemini"
    assert "--prompt-interactive" in run_cmd
    assert "--allowed-mcp-server-names" in run_cmd
    assert "yeehaw-abc12345" in run_cmd
    assert "--include-directories" in run_cmd
    assert remove_cmd == ["gemini", "mcp", "remove", "yeehaw-abc12345"]

    assert add_cwd is not None
    assert run_cwd == add_cwd
    assert remove_cwd == add_cwd


def test_start_planner_session_gemini_add_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gemini", "mcp", "add"]:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        return _completed(cmd)

    monkeypatch.setattr(planner_session.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Failed to configure Gemini MCP server"):
        start_planner_session(db_path, agent="gemini")


def test_start_planner_session_unsupported_agent(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    with pytest.raises(ValueError, match="Unsupported planner agent"):
        start_planner_session(db_path, agent="unknown-agent")


def test_start_planner_session_codex_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    monkeypatch.setattr(
        planner_session.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", ""),
    )

    with pytest.raises(RuntimeError, match="Codex planner session exited with code 1"):
        start_planner_session(db_path, agent="codex")
