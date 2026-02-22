"""Worker runtime configuration and default launch hardening."""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkerLaunchConfig:
    """Resolved launch configuration for a worker agent."""

    disable_default_mcp: bool = True
    extra_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


def resolve_worker_launch_config(repo_root: Path, agent_name: str) -> WorkerLaunchConfig:
    """Resolve worker launch config from `.yeehaw/workers.json` with defaults."""
    config_path = repo_root / ".yeehaw" / "workers.json"
    if not config_path.exists():
        return WorkerLaunchConfig()

    try:
        raw = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid worker config in {config_path}: root must be an object")

    global_disable = _read_bool(raw, "disable_default_mcp", default=True)
    global_args = tuple(_read_str_list(raw, "extra_args"))
    global_env = _read_env_map(raw, "env")

    agents_obj = raw.get("agents", {})
    if agents_obj is None:
        agents_obj = {}
    if not isinstance(agents_obj, dict):
        raise ValueError(f"Invalid worker config in {config_path}: 'agents' must be an object")

    agent_raw = agents_obj.get(agent_name, {})
    if agent_raw is None:
        agent_raw = {}
    if not isinstance(agent_raw, dict):
        raise ValueError(
            f"Invalid worker config in {config_path}: agents.{agent_name} must be an object"
        )

    agent_disable = _read_bool(agent_raw, "disable_default_mcp", default=global_disable)
    agent_args = tuple(_read_str_list(agent_raw, "extra_args"))
    agent_env = _read_env_map(agent_raw, "env")

    env = dict(global_env)
    env.update(agent_env)
    return WorkerLaunchConfig(
        disable_default_mcp=agent_disable,
        extra_args=global_args + agent_args,
        env=env,
    )


def default_no_mcp_args(agent_name: str) -> list[str]:
    """Return CLI args that disable default MCP servers for known agents."""
    if agent_name == "claude":
        return ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']

    if agent_name == "gemini":
        # Restrict to a sentinel name that is not expected to exist.
        return ["--allowed-mcp-server-names", "__yeehaw_no_mcp__"]

    if agent_name == "codex":
        names = _codex_mcp_server_names()
        args: list[str] = []
        for name in names:
            args.extend(["-c", f"mcp_servers.{name}.enabled=false"])
        return args

    return []


def _codex_mcp_server_names() -> list[str]:
    """Discover configured Codex MCP server names."""
    discovered = _codex_mcp_names_via_cli()
    if discovered:
        return discovered
    return _codex_mcp_names_via_toml()


def _codex_mcp_names_via_cli() -> list[str]:
    proc = subprocess.run(
        ["codex", "mcp", "list", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _codex_mcp_names_via_toml() -> list[str]:
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return []
    try:
        payload = tomllib.loads(config_path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return []

    servers = payload.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    return [name for name in servers if isinstance(name, str) and name]


def _read_bool(mapping: dict[str, Any], key: str, default: bool) -> bool:
    value = mapping.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"'{key}' must be a boolean")


def _read_str_list(mapping: dict[str, Any], key: str) -> list[str]:
    value = mapping.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return value


def _read_env_map(mapping: dict[str, Any], key: str) -> dict[str, str]:
    value = mapping.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"'{key}' must be an object of string values")
    env: dict[str, str] = {}
    for env_key, env_value in value.items():
        if not isinstance(env_key, str) or not isinstance(env_value, str):
            raise ValueError(f"'{key}' must be an object of string values")
        env[env_key] = env_value
    return env
