"""Hook protocol and discovery data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOOK_RESPONSE_STATUSES: tuple[str, ...] = ("ok", "ignored", "error")


@dataclass(frozen=True)
class HookDefinition:
    """Validated hook metadata resolved to an executable entrypoint."""

    name: str
    entrypoint: Path
    events: tuple[str, ...]
    source: str
    metadata_path: Path
    timeout_ms: int = 2000
    description: str | None = None


@dataclass(frozen=True)
class HookRequest:
    """Lifecycle event payload passed to hook executables."""

    schema_version: int
    event_name: str
    event_id: str
    emitted_at: str
    source: dict[str, Any]
    context: dict[str, Any]
    project: dict[str, Any] | None = None
    roadmap: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None


@dataclass(frozen=True)
class HookAction:
    """Action emitted by a hook response."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResponse:
    """Structured response emitted by a hook executable."""

    schema_version: int
    event_id: str
    extension: str
    status: str
    summary: str | None = None
    actions: tuple[HookAction, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
