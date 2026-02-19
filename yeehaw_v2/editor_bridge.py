from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class EditorError(RuntimeError):
    pass


def resolve_editor(explicit_editor: str | None = None) -> str:
    if explicit_editor and explicit_editor.strip():
        return explicit_editor.strip()
    env_editor = os.environ.get("EDITOR", "").strip()
    if env_editor:
        return env_editor
    return "vi"


def open_in_editor(path: str | Path, editor: str | None = None) -> None:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not resolved.exists():
        resolved.write_text("", encoding="utf-8")

    command = resolve_editor(editor)
    parts = shlex.split(command) + [str(resolved)]
    proc = subprocess.run(parts, check=False)
    if proc.returncode != 0:
        raise EditorError(f"editor command failed ({proc.returncode}): {' '.join(parts)}")
