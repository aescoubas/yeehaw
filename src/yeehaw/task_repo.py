"""Shared task repository-root resolution helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def resolve_task_repo_root(task: Mapping[str, Any], *, fallback: Path) -> Path:
    """Resolve repo root for a task, defaulting to fallback when unavailable."""
    candidate = task.get("project_repo_root")
    if isinstance(candidate, str) and candidate:
        return Path(candidate)
    return fallback
