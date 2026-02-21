"""Agent profile definitions and registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    """CLI launch profile for a worker agent."""

    name: str
    command: str
    prompt_flag: str
    timeout_minutes: int = 60


AGENT_REGISTRY: dict[str, AgentProfile] = {
    "claude": AgentProfile(
        name="claude",
        command="claude",
        prompt_flag="--dangerously-skip-permissions -p",
    ),
    "gemini": AgentProfile(
        name="gemini",
        command="gemini",
        prompt_flag="-p",
    ),
    "codex": AgentProfile(
        name="codex",
        command="codex",
        prompt_flag="--prompt",
    ),
}

DEFAULT_AGENT = "claude"


def resolve_profile(agent_name: str | None = None) -> AgentProfile:
    """Resolve agent profile by name, falling back to default."""
    name = agent_name or DEFAULT_AGENT
    if name not in AGENT_REGISTRY:
        names = ", ".join(AGENT_REGISTRY)
        raise ValueError(f"Unknown agent '{name}'. Available: {names}")
    return AGENT_REGISTRY[name]
