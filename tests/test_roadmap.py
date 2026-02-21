"""Tests for roadmap markdown parsing and validation."""

from __future__ import annotations

import pytest

from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap


def test_parse_roadmap_happy_path() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: API Project
## Phase 1: Foundation
**Verify:** `pytest -q`
### Task 1.1: Setup package
Create package structure.

### Task 1.2: Add schema
Write SQL schema.
## Phase 2: Integration
### Task 2.1: Wire engine
Connect orchestrator to store.
""".strip()
    )

    assert roadmap.project_name == "API Project"
    assert len(roadmap.phases) == 2
    assert roadmap.phases[0].verify_cmd == "pytest -q"
    assert roadmap.phases[0].tasks[0].number == "1.1"
    assert roadmap.phases[0].tasks[0].description == "Create package structure."
    assert roadmap.phases[1].tasks[0].title == "Wire engine"


def test_parse_roadmap_requires_header() -> None:
    with pytest.raises(ValueError, match="Missing roadmap header"):
        parse_roadmap("## Phase 1: Foundation")


def test_validate_roadmap_detects_sequence_issues() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: API Project
## Phase 2: Wrong Order
### Task 2.2: Bad Number
body
""".strip()
    )

    errors = validate_roadmap(roadmap)
    assert "Phase 2 out of sequence (expected 1)" in errors
    assert "Task 2.2 out of sequence (expected 2.1)" in errors


def test_validate_roadmap_phase_without_tasks() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: API Project
## Phase 1: Empty
""".strip()
    )

    errors = validate_roadmap(roadmap)
    assert errors == ["Phase 1 has no tasks"]
