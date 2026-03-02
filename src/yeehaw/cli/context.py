"""Project memory pack context management commands."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from yeehaw.context import (
    CONTEXT_DIR_NAME,
    MEMORY_PACK_MAX_BYTES,
    PROJECT_MEMORY_DIR_NAME,
    load_project_memory_pack,
    validate_memory_pack_markdown,
)
from yeehaw.store.store import Store


_TEMPLATED_SECTION_LABELS: tuple[str, ...] = (
    "Conventions",
    "Architecture Constraints",
    "Coding Standards",
)


def handle_context(args: Any, db_path: Path) -> None:
    """Handle `yeehaw context` subcommands."""
    project_name = _normalize_project_name(args.project)
    if project_name is None:
        print("Error: project name must be a non-empty string.")
        return
    if "/" in project_name or "\\" in project_name:
        print("Error: project name must not contain path separators.")
        return

    store = Store(db_path)
    try:
        project = store.get_project(project_name)
    finally:
        store.close()

    if project is None:
        print(f"Error: Project '{project_name}' not found.")
        return

    runtime_root = db_path.parent
    if args.context_command == "show":
        _show_context(project_name=project_name, runtime_root=runtime_root)
    elif args.context_command == "set":
        _set_context(project_name=project_name, runtime_root=runtime_root, args=args)
    elif args.context_command == "edit":
        _edit_context(project_name=project_name, runtime_root=runtime_root)
    elif args.context_command == "validate":
        _validate_context(project_name=project_name, runtime_root=runtime_root)


def _show_context(*, project_name: str, runtime_root: Path) -> None:
    """Print the effective memory pack content for one project."""
    try:
        memory_pack = load_project_memory_pack(project_name, runtime_root=runtime_root)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    if memory_pack.is_empty:
        print(f"No memory pack configured for project '{project_name}'.")
        print(f"Default path: {_default_memory_pack_path(runtime_root, project_name)}")
        return

    source_path = memory_pack.source_path or _default_memory_pack_path(runtime_root, project_name)
    print(f"Memory pack for project '{project_name}':")
    print(f"Source: {source_path}")
    print(f"Size: {len(memory_pack.markdown.encode('utf-8'))}/{MEMORY_PACK_MAX_BYTES} bytes")
    print()
    print(memory_pack.markdown)


def _set_context(*, project_name: str, runtime_root: Path, args: Any) -> None:
    """Validate and persist a memory pack for one project."""
    source: Path | str = "<inline>"
    markdown: str

    if args.file is not None:
        source_path = Path(str(args.file))
        if not source_path.exists():
            print(f"Error: File '{args.file}' not found.")
            return
        try:
            markdown = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"Error: file must be UTF-8 text: {exc}")
            return
        except OSError as exc:
            print(f"Error: unable to read '{args.file}': {exc}")
            return
        source = source_path
    else:
        markdown = str(args.text)

    try:
        normalized_markdown = validate_memory_pack_markdown(markdown, source=source)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    destination = _default_memory_pack_path(runtime_root, project_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"{normalized_markdown}\n", encoding="utf-8")

    print(f"Saved memory pack for project '{project_name}' to {destination}")
    print(f"Size: {len(normalized_markdown.encode('utf-8'))} bytes")


def _edit_context(*, project_name: str, runtime_root: Path) -> None:
    """Open the project memory pack in the configured editor, then validate."""
    destination = _default_memory_pack_path(runtime_root, project_name)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_memory_pack_template(project_name), encoding="utf-8")
        print(f"Created memory pack template at {destination}")

    editor_cmd = _editor_command()
    if editor_cmd is None:
        print("Error: set VISUAL or EDITOR before using `yeehaw context edit`.")
        return

    try:
        result = subprocess.run(
            [*editor_cmd, str(destination)],
            check=False,
        )
    except OSError as exc:
        print(f"Error: unable to launch editor: {exc}")
        return

    if result.returncode != 0:
        print(f"Error: editor exited with code {result.returncode}.")
        return

    _validate_context(project_name=project_name, runtime_root=runtime_root)


def _validate_context(*, project_name: str, runtime_root: Path) -> None:
    """Validate one project's effective memory pack and print result."""
    try:
        memory_pack = load_project_memory_pack(project_name, runtime_root=runtime_root)
    except ValueError as exc:
        print(f"Context validation failed for project '{project_name}': {exc}")
        return

    if memory_pack.is_empty:
        print(f"No memory pack configured for project '{project_name}'.")
        return

    source_path = memory_pack.source_path or _default_memory_pack_path(runtime_root, project_name)
    print(f"Context pack is valid for project '{project_name}'.")
    print(f"Source: {source_path}")
    print(f"Size: {len(memory_pack.markdown.encode('utf-8'))} bytes")


def _default_memory_pack_path(runtime_root: Path, project_name: str) -> Path:
    """Return canonical write/edit location for a project's memory pack."""
    project_slug = _project_slug(project_name)
    return runtime_root / CONTEXT_DIR_NAME / PROJECT_MEMORY_DIR_NAME / f"{project_slug}.md"


def _project_slug(project_name: str) -> str:
    """Normalize project name into a filesystem-friendly memory pack slug."""
    if "/" in project_name or "\\" in project_name:
        raise ValueError("project name must not contain path separators")

    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    if slug:
        return slug

    fallback = project_name.strip().replace(" ", "-")
    if not fallback:
        raise ValueError("project name must be non-empty")
    return fallback


def _memory_pack_template(project_name: str) -> str:
    """Build starter markdown that includes required memory pack headings."""
    heading = f"# {project_name} Memory Pack"
    section_blocks = [f"## {label}\n- " for label in _TEMPLATED_SECTION_LABELS]
    return f"{heading}\n\n" + "\n\n".join(section_blocks) + "\n"


def _editor_command() -> list[str] | None:
    """Return editor argv parsed from VISUAL/EDITOR environment variables."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not isinstance(editor, str) or not editor.strip():
        return None
    return shlex.split(editor)


def _normalize_project_name(raw_project_name: Any) -> str | None:
    """Parse CLI project argument into a trimmed non-empty string."""
    if not isinstance(raw_project_name, str):
        return None
    normalized = raw_project_name.strip()
    if not normalized:
        return None
    return normalized
