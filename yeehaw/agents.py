from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProfile:
    name: str
    command: str
    warmup_seconds: float = 2.5


DEFAULT_PROFILES: dict[str, AgentProfile] = {
    "codex": AgentProfile(name="codex", command="codex", warmup_seconds=3.0),
    "claude": AgentProfile(name="claude", command="claude", warmup_seconds=3.0),
    "gemini": AgentProfile(name="gemini", command="gemini", warmup_seconds=3.0),
}


def resolve_command(agent_name: str, override_command: str | None = None) -> tuple[str, float]:
    if override_command and override_command.strip():
        return shlex.join(shlex.split(override_command)), 2.0

    key = agent_name.strip().lower()
    profile = DEFAULT_PROFILES.get(key)
    if profile is None:
        # Fall back to agent name as command for custom CLIs.
        return shlex.join(shlex.split(agent_name)), 2.0
    return profile.command, profile.warmup_seconds
