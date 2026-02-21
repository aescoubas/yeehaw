"""Pytest fixtures for yeehaw Python implementation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw.store.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Create a fresh store bound to a temp DB."""
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    instance = Store(db_path)
    try:
        yield instance
    finally:
        instance.close()
