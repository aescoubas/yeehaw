"""Agent profile definitions and registry."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
import shutil


@dataclass(frozen=True)
class AgentProfile:
    """CLI launch profile for a worker agent."""

    name: str
    command: str
    prompt_flag: str
    timeout_minutes: int = 60

    def executable(self) -> str:
        """Return executable name used to launch the profile."""
        try:
            parts = shlex.split(self.command)
        except ValueError:
            return ""
        if not parts:
            return ""
        return parts[0]

    def is_available(self) -> bool:
        """Return True when the profile executable is available in PATH."""
        executable = self.executable()
        if not executable:
            return False
        return shutil.which(executable) is not None


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
        command="codex exec --json --dangerously-bypass-approvals-and-sandbox",
        prompt_flag="",
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
