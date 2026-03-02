"""Typed models and validation helpers for project memory packs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MEMORY_PACK_MAX_BYTES = 12_000
MEMORY_PACK_MAX_LINES = 240
MEMORY_PACK_MAX_HEADINGS = 24
MEMORY_PACK_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Conventions",
    "Architecture Constraints",
    "Coding Standards",
)

_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")


@dataclass(frozen=True)
class ProjectMemoryPack:
    """Validated project-scoped memory pack content."""

    project_name: str
    markdown: str = ""
    source_path: Path | None = None

    @property
    def is_empty(self) -> bool:
        """Return True when no memory pack content is loaded."""
        return not bool(self.markdown)


def parse_project_memory_pack(
    markdown: Any,
    *,
    project_name: str,
    source: Path | str = "<memory-pack>",
) -> ProjectMemoryPack:
    """Parse and validate project memory pack markdown."""
    normalized_project_name = project_name.strip()
    if not normalized_project_name:
        raise ValueError("project_name must be a non-empty string")

    normalized_markdown = validate_memory_pack_markdown(markdown, source=source)
    return ProjectMemoryPack(
        project_name=normalized_project_name,
        markdown=normalized_markdown,
        source_path=source if isinstance(source, Path) else None,
    )


def validate_memory_pack_markdown(markdown: Any, *, source: Path | str = "<memory-pack>") -> str:
    """Validate bounded memory pack markdown and normalize line endings."""
    if not isinstance(markdown, str):
        raise _memory_pack_error(
            source,
            f"markdown must be a string (got {_json_type_name(markdown)})",
        )

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise _memory_pack_error(source, "markdown must be non-empty")

    payload_bytes = len(normalized.encode("utf-8"))
    if payload_bytes > MEMORY_PACK_MAX_BYTES:
        raise _memory_pack_error(
            source,
            f"markdown exceeds max size ({payload_bytes} > {MEMORY_PACK_MAX_BYTES} bytes)",
        )

    lines = normalized.splitlines()
    if len(lines) > MEMORY_PACK_MAX_LINES:
        raise _memory_pack_error(
            source,
            f"markdown exceeds max line count ({len(lines)} > {MEMORY_PACK_MAX_LINES})",
        )

    _validate_control_characters(normalized, source=source)

    headings = [match.group(1).strip() for match in _HEADING_PATTERN.finditer(normalized)]
    if len(headings) > MEMORY_PACK_MAX_HEADINGS:
        raise _memory_pack_error(
            source,
            f"markdown exceeds max heading count ({len(headings)} > {MEMORY_PACK_MAX_HEADINGS})",
        )

    _validate_required_sections(headings, source=source)
    return normalized


def _validate_required_sections(headings: list[str], *, source: Path | str) -> None:
    heading_set = {heading.casefold() for heading in headings if heading}
    missing_sections = [
        section
        for section in MEMORY_PACK_REQUIRED_SECTIONS
        if section.casefold() not in heading_set
    ]
    if missing_sections:
        missing = ", ".join(missing_sections)
        raise _memory_pack_error(source, f"missing required sections: {missing}")


def _validate_control_characters(markdown: str, *, source: Path | str) -> None:
    for character in markdown:
        codepoint = ord(character)
        if codepoint < 32 and character not in ("\n", "\t"):
            raise _memory_pack_error(
                source,
                "markdown contains unsupported control characters",
            )


def _memory_pack_error(source: Path | str, message: str) -> ValueError:
    return ValueError(f"Invalid memory pack in {source}: {message}")


def _json_type_name(value: Any) -> str:
    """Return a human-readable JSON-ish type label."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
