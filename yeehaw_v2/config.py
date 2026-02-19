from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SchedulerLimits:
    max_global_sessions: int = 20
    max_project_sessions: int = 10
    stuck_minutes: int = 12
    preemption_enabled: bool = True
    auto_reassign: bool = True


@dataclass(frozen=True, slots=True)
class ControlPlaneConfig:
    db_path: Path
    poll_seconds: float = 1.0
    default_runtime: str = "tmux"
    limits: SchedulerLimits = field(default_factory=SchedulerLimits)
