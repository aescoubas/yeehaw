from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RuntimeKind(str, Enum):
    TMUX = "tmux"
    LOCAL_PTY = "local_pty"


class SessionStatus(str, Enum):
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    CRASHED = "crashed"


@dataclass(frozen=True, slots=True)
class SessionSpec:
    project_id: int
    task_id: int | None
    project_root: Path
    title: str
    command: str
    runtime_kind: RuntimeKind = RuntimeKind.TMUX
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionHandle:
    runtime_kind: RuntimeKind
    session_id: str
    target: str
    pid: int | None = None
