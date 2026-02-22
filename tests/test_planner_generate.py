"""Tests for natural-language roadmap generation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import yeehaw.planner.generate as planner_generate
from yeehaw.planner.generate import AgentRunResult, generate_roadmap_from_prompt
from yeehaw.store.store import Store


def _seed_project(db_path: Path, name: str = "proj-a") -> int:
    store = Store(db_path)
    try:
        project_id = store.create_project(name, "/tmp/repo-a")
        return project_id
    finally:
        store.close()


def test_generate_roadmap_from_prompt_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    _seed_project(db_path)

    def fake_run_agent(agent: str, db_path_arg: Path, prompt: str, timeout_sec: int) -> AgentRunResult:
        assert agent == "codex"
        assert db_path_arg == db_path
        assert "create_roadmap" in prompt
        assert timeout_sec == 300

        store = Store(db_path_arg)
        try:
            project = store.get_project("proj-a")
            assert project is not None
            roadmap_id = store.create_roadmap(
                project["id"],
                "# Roadmap: proj-a\n## Phase 1: Setup\n### Task 1.1: Build\nDo work\n",
            )
            phase_id = store.create_phase(roadmap_id, 1, "Setup", None)
            store.create_task(roadmap_id, phase_id, "1.1", "Build", "Do work")
        finally:
            store.close()

        return AgentRunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("yeehaw.planner.generate._run_agent_prompt", fake_run_agent)

    result = generate_roadmap_from_prompt(
        db_path=db_path,
        project_name="proj-a",
        prompt_text="Build an API service with tests.",
        agent="codex",
    )

    assert result.success is True
    assert result.roadmap_id is not None
    assert result.phases == 1
    assert result.tasks == 1


def test_generate_roadmap_from_prompt_no_new_roadmap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    project_id = _seed_project(db_path)

    store = Store(db_path)
    try:
        existing = store.create_roadmap(project_id, "# Roadmap: proj-a")
        phase_id = store.create_phase(existing, 1, "Old", None)
        store.create_task(existing, phase_id, "1.1", "Old task", "Old")
    finally:
        store.close()

    monkeypatch.setattr(
        "yeehaw.planner.generate._run_agent_prompt",
        lambda *_args, **_kwargs: AgentRunResult(returncode=0, stdout="", stderr=""),
    )

    result = generate_roadmap_from_prompt(
        db_path=db_path,
        project_name="proj-a",
        prompt_text="Replace old roadmap",
        agent="claude",
    )

    assert result.success is False
    assert "did not create a new roadmap" in result.message


def test_generate_roadmap_from_prompt_agent_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    _seed_project(db_path)

    monkeypatch.setattr(
        "yeehaw.planner.generate._run_agent_prompt",
        lambda *_args, **_kwargs: AgentRunResult(returncode=1, stdout="no", stderr="boom"),
    )

    result = generate_roadmap_from_prompt(
        db_path=db_path,
        project_name="proj-a",
        prompt_text="Create roadmap",
        agent="gemini",
    )

    assert result.success is False
    assert result.message == "Agent exited with code 1"
    assert result.stderr == "boom"


def test_generate_roadmap_from_prompt_missing_project(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    result = generate_roadmap_from_prompt(
        db_path=db_path,
        project_name="missing",
        prompt_text="Build x",
        agent="codex",
    )
    assert result.success is False
    assert result.message == "Project 'missing' not found"


def test_run_codex_prompt_builds_mcp_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], timeout_sec: int, cwd: Path | None = None) -> AgentRunResult:
        captured["cmd"] = cmd
        captured["timeout"] = timeout_sec
        captured["cwd"] = cwd
        return AgentRunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(planner_generate, "_run_command", fake_run_command)

    result = planner_generate._run_codex_prompt(db_path, "Build a roadmap", 90)
    assert result.returncode == 0

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert any(
        isinstance(item, str) and item.startswith("mcp_servers.yeehaw.command=")
        for item in cmd
    )
    assert any(
        isinstance(item, str) and item.startswith("mcp_servers.yeehaw.args=")
        for item in cmd
    )
    assert captured["timeout"] == 90
    assert captured["cwd"] is None


def test_run_gemini_prompt_registers_server_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    calls: list[tuple[list[str], int, Path | None]] = []

    class FakeUUID:
        hex = "abcdef1234567890"

    monkeypatch.setattr(planner_generate.uuid, "uuid4", lambda: FakeUUID())

    def fake_run_command(cmd: list[str], timeout_sec: int, cwd: Path | None = None) -> AgentRunResult:
        calls.append((cmd, timeout_sec, cwd))
        if cmd[:3] == ["gemini", "mcp", "add"]:
            return AgentRunResult(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["gemini", "mcp", "remove"]:
            return AgentRunResult(returncode=0, stdout="", stderr="")
        return AgentRunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(planner_generate, "_run_command", fake_run_command)

    result = planner_generate._run_gemini_prompt(db_path, "Build roadmap", 120)
    assert result.returncode == 0

    add_call, run_call, remove_call = calls
    add_cmd = add_call[0]
    run_cmd = run_call[0]
    remove_cmd = remove_call[0]

    assert add_cmd[:3] == ["gemini", "mcp", "add"]
    assert "yeehaw-abcdef12" in add_cmd
    assert run_cmd[0] == "gemini"
    assert "--allowed-mcp-server-names" in run_cmd
    assert "yeehaw-abcdef12" in run_cmd
    assert remove_cmd == ["gemini", "mcp", "remove", "yeehaw-abcdef12"]
