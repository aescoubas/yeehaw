"""Tests for agent profiles and launcher helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw.agent.launcher import (
    build_launch_command,
    build_task_prompt,
    write_launcher,
)
from yeehaw.agent.profiles import resolve_profile


def test_resolve_profile_default_and_named() -> None:
    default_profile = resolve_profile()
    codex_profile = resolve_profile("codex")

    assert default_profile.name == "claude"
    assert codex_profile.command.startswith("codex exec")
    assert codex_profile.prompt_flag == ""


def test_resolve_profile_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        resolve_profile("unknown")


def test_build_task_prompt_includes_signal_contract() -> None:
    prompt = build_task_prompt(
        {
            "id": 42,
            "task_number": "1.2",
            "title": "Implement parser",
            "description": "Add parser logic.",
        },
        signal_dir="/tmp/signal-dir",
        previous_failure="timeout",
    )

    assert "# Task 1.2: Implement parser" in prompt
    assert "`/tmp/signal-dir/signal.json`" in prompt
    assert '"task_id": 42' in prompt
    assert '"status": "done"' in prompt
    assert "## Previous Attempt Failed" in prompt
    assert "timeout" in prompt


def test_build_launch_command_quotes_prompt() -> None:
    profile = resolve_profile("codex")
    command = build_launch_command(profile, "line one\nline two with 'quotes'")

    assert command.startswith("codex exec ")
    assert "--prompt" not in command
    assert "line two" in command


def test_write_launcher_creates_executable_script(tmp_path: Path) -> None:
    script_path = tmp_path / "launch.sh"
    profile = resolve_profile("gemini")

    write_launcher(script_path, profile, "hello")

    content = script_path.read_text()
    assert content.startswith("#!/bin/bash")
    assert "YEEHAW_PROMPT_EOF" in content
    assert "exec gemini -p" in content
    assert script_path.stat().st_mode & 0o111
