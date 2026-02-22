"""Dependency parsing helpers for roadmap task metadata."""

from __future__ import annotations

import re


_RE_DEPENDS_LINE = re.compile(r"^\*\*Depends on:\*\*\s*(.*)$", re.IGNORECASE)
_RE_TASK_REF = re.compile(r"[Pp]?\d+\.\d+")
_RE_TASK_COMPONENTS = re.compile(r"^[Pp]?(\d+)\.(\d+)$")
_NONE_VALUES = {"", "none", "n/a", "na", "-"}


def parse_task_dependencies(description: str) -> list[str]:
    """Extract normalized task references from a task description metadata block."""
    for raw_line in description.splitlines():
        match = _RE_DEPENDS_LINE.match(raw_line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if value.lower() in _NONE_VALUES:
            return []

        refs: list[str] = []
        seen: set[str] = set()
        for token in _RE_TASK_REF.findall(value):
            normalized = normalize_task_ref(token)
            if normalized is None or normalized in seen:
                continue
            refs.append(normalized)
            seen.add(normalized)
        return refs
    return []


def normalize_task_ref(raw_ref: str) -> str | None:
    """Normalize task ref (e.g. P0.1 -> 0.1), returning None when invalid."""
    match = _RE_TASK_COMPONENTS.match(raw_ref.strip())
    if not match:
        return None
    return f"{int(match.group(1))}.{int(match.group(2))}"
