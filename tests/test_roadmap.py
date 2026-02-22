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


def test_parse_roadmap_verbose_phase_zero_and_prefixed_tasks() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: chamicore-lib
## Phase 0: Foundation (chamicore-lib)
### P0.1: httputil — envelope types and response helpers [x]

**Depends on:** none
**Repo:** chamicore-lib

**Files:**
- `httputil/envelope.go` — envelope model types

**Description:**
Implement envelope types and response helpers.

**Done when:**
- [ ] Envelope serializes correctly
- [ ] Problem details are RFC 9457 compliant
""".strip()
    )

    assert roadmap.project_name == "chamicore-lib"
    assert len(roadmap.phases) == 1
    assert roadmap.phases[0].number == 0
    assert roadmap.phases[0].title == "Foundation (chamicore-lib)"
    assert len(roadmap.phases[0].tasks) == 1
    task = roadmap.phases[0].tasks[0]
    assert task.number == "0.1"
    assert task.title == "httputil — envelope types and response helpers"
    assert "**Depends on:** none" in task.description
    assert "- [ ] Envelope serializes correctly" in task.description
    assert validate_roadmap(roadmap) == []


def test_validate_roadmap_rejects_unknown_dependency() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
**Depends on:** none
### Task 1.2: Build
**Depends on:** 1.9
""".strip()
    )

    errors = validate_roadmap(roadmap)
    assert "Task 1.2 depends on unknown task 1.9" in errors


def test_validate_roadmap_rejects_dependency_cycle() -> None:
    roadmap = parse_roadmap(
        """
# Roadmap: proj-a
## Phase 1: Foundation
### Task 1.1: Setup
**Depends on:** 1.2
### Task 1.2: Build
**Depends on:** 1.1
""".strip()
    )

    errors = validate_roadmap(roadmap)
    assert any(error.startswith("Task dependency cycle detected:") for error in errors)
