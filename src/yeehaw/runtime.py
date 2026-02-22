"""Runtime directory/path helpers for Yeehaw metadata."""

from __future__ import annotations

import os
from pathlib import Path


def runtime_root() -> Path:
    """Return Yeehaw runtime root, defaulting to ~/.yeehaw."""
    override = os.environ.get("YEEHAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".yeehaw"


def default_db_path() -> Path:
    """Return default SQLite database path under runtime root."""
    return runtime_root() / "yeehaw.db"
