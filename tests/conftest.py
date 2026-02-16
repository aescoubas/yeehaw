from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from yeehaw import db
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "yeehaw.db"


@pytest.fixture
def conn(temp_db_path: Path) -> sqlite3.Connection:
    c = db.connect(temp_db_path)
    yield c
    c.close()


@pytest.fixture
def sample_roadmap() -> RoadmapDef:
    stage = StageDef(
        id="s1",
        title="Stage 1",
        goal="Do work",
        instructions="Be precise",
        deliverables=["README.md"],
        timeout_minutes=5,
    )
    track = TrackDef(id="t1", topic="topic", agent="codex", command="codex", stages=[stage])
    return RoadmapDef(version=1, name="rm", guidelines=["g1"], tracks=[track], raw_text="raw")
