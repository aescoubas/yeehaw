"""Tests for worker runtime configuration resolution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import yeehaw.agent.runtime_config as runtime_config
from yeehaw.agent.runtime_config import default_no_mcp_args, resolve_worker_launch_config


def test_resolve_worker_launch_config_defaults(tmp_path: Path) -> None:
    resolved = resolve_worker_launch_config(tmp_path, "codex")
    assert resolved.disable_default_mcp is True
    assert resolved.extra_args == ()
    assert resolved.env == {}


def test_resolve_worker_launch_config_merges_global_and_agent(tmp_path: Path) -> None:
    cfg_dir = tmp_path
    cfg_dir.mkdir(parents=True, exist_ok=True)
    config_path = cfg_dir / "workers.json"
    config_path.write_text(
        json.dumps(
            {
                "disable_default_mcp": True,
                "extra_args": ["--global-flag"],
                "env": {"GLOBAL_ENV": "1"},
                "agents": {
                    "codex": {
                        "disable_default_mcp": False,
                        "extra_args": ["--agent-flag"],
                        "env": {"AGENT_ENV": "2"},
                    }
                },
            }
        )
    )

    resolved = resolve_worker_launch_config(tmp_path, "codex")
    assert resolved.disable_default_mcp is False
    assert resolved.extra_args == ("--global-flag", "--agent-flag")
    assert resolved.env == {"GLOBAL_ENV": "1", "AGENT_ENV": "2"}


def test_resolve_worker_launch_config_invalid_json(tmp_path: Path) -> None:
    cfg_dir = tmp_path
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "workers.json").write_text("{not-json")

    with pytest.raises(ValueError, match="Invalid JSON"):
        resolve_worker_launch_config(tmp_path, "claude")


def test_default_no_mcp_args_static_agents() -> None:
    assert "--strict-mcp-config" in default_no_mcp_args("claude")
    assert default_no_mcp_args("gemini") == [
        "--allowed-mcp-server-names",
        "__yeehaw_no_mcp__",
    ]


def test_default_no_mcp_args_codex_from_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        [
            {"name": "server-a"},
            {"name": "server-b"},
        ]
    )

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["codex"], 0, payload, "")

    monkeypatch.setattr(runtime_config.subprocess, "run", fake_run)

    args = default_no_mcp_args("codex")
    assert args == [
        "-c",
        "mcp_servers.server-a.enabled=false",
        "-c",
        "mcp_servers.server-b.enabled=false",
    ]


def test_default_no_mcp_args_codex_falls_back_to_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        """
[mcp_servers.alpha]
command = "x"

[mcp_servers.beta]
command = "y"
""".strip()
    )

    monkeypatch.setattr(runtime_config.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["codex"], 1, "", "failed")

    monkeypatch.setattr(runtime_config.subprocess, "run", fake_run)

    args = default_no_mcp_args("codex")
    assert args == [
        "-c",
        "mcp_servers.alpha.enabled=false",
        "-c",
        "mcp_servers.beta.enabled=false",
    ]


def test_default_no_mcp_args_codex_logs_toml_parse_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text("[mcp_servers.alpha\nbroken = true")

    monkeypatch.setattr(runtime_config.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["codex"], 1, "", "failed")

    monkeypatch.setattr(runtime_config.subprocess, "run", fake_run)
    caplog.set_level("WARNING", logger="yeehaw.agent.runtime_config")

    assert default_no_mcp_args("codex") == []
    assert "Failed to parse Codex MCP config" in caplog.text
