"""Runtime loader for project memory packs."""

from __future__ import annotations

import re
from pathlib import Path

from yeehaw.context.models import (
    MEMORY_PACK_MAX_BYTES,
    ProjectMemoryPack,
    parse_project_memory_pack,
)
from yeehaw.runtime import runtime_root as resolve_runtime_root

CONTEXT_DIR_NAME = "context"
PROJECT_MEMORY_DIR_NAME = "projects"


def load_project_memory_pack(
    project_name: str,
    *,
    runtime_root: Path | None = None,
) -> ProjectMemoryPack:
    """Load a project memory pack from runtime context paths."""
    normalized_project_name = project_name.strip()
    if not normalized_project_name:
        raise ValueError("project_name must be a non-empty string")
    if "/" in normalized_project_name or "\\" in normalized_project_name:
        raise ValueError("project_name must not contain path separators")

    resolved_runtime_root = runtime_root or resolve_runtime_root()
    context_dir = resolved_runtime_root / CONTEXT_DIR_NAME
    memory_pack_path = _resolve_project_memory_pack_path(context_dir, normalized_project_name)
    if memory_pack_path is None:
        return ProjectMemoryPack(project_name=normalized_project_name)

    memory_pack_markdown = _read_memory_pack_markdown(memory_pack_path)
    return parse_project_memory_pack(
        memory_pack_markdown,
        project_name=normalized_project_name,
        source=memory_pack_path,
    )


def _resolve_project_memory_pack_path(context_dir: Path, project_name: str) -> Path | None:
    for path in _project_memory_pack_candidates(context_dir, project_name):
        if path.exists():
            return path
    return None


def _project_memory_pack_candidates(context_dir: Path, project_name: str) -> list[Path]:
    slug = _slugify(project_name)
    base_names = _dedupe_preserve_order([project_name, slug])

    candidates: list[Path] = []
    for base_name in base_names:
        for parent in (context_dir / PROJECT_MEMORY_DIR_NAME, context_dir):
            candidates.extend(
                [
                    parent / f"{base_name}.md",
                    parent / f"{base_name}.memory.md",
                    parent / base_name / "memory.md",
                ]
            )
    return candidates


def _read_memory_pack_markdown(memory_pack_path: Path) -> str:
    if not memory_pack_path.is_file():
        raise _memory_pack_error(memory_pack_path, "expected a file")

    try:
        stat_result = memory_pack_path.stat()
    except OSError as exc:
        raise _memory_pack_error(memory_pack_path, f"unable to stat file: {exc}") from exc

    if stat_result.st_size > MEMORY_PACK_MAX_BYTES:
        raise _memory_pack_error(
            memory_pack_path,
            f"file exceeds max size ({stat_result.st_size} > {MEMORY_PACK_MAX_BYTES} bytes)",
        )

    try:
        return memory_pack_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _memory_pack_error(memory_pack_path, f"file must be UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise _memory_pack_error(memory_pack_path, f"unable to read file: {exc}") from exc


def _slugify(project_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _memory_pack_error(source: Path, message: str) -> ValueError:
    return ValueError(f"Invalid memory pack in {source}: {message}")
