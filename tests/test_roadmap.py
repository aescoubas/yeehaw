from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw import roadmap


def test_helpers() -> None:
    assert roadmap._slugify("A B/C") == "a-b-c"
    assert roadmap._slugify("!!!") == "phase"
    assert roadmap._timeout_from_budget("low") == 45
    assert roadmap._timeout_from_budget("HIGH") == 150
    assert roadmap._timeout_from_budget("medium") == 90
    assert roadmap._normalize_checklist_line("- [x] item") == "- item"
    assert roadmap._normalize_checklist_line("plain") == "plain"
    assert roadmap._join_paragraph_lines([" a ", "", "b "]) == "a b"


def test_extract_guidelines_and_agent() -> None:
    md = """## Roadmap Guidelines
- one
- two

## 2. Execution Phases
**Agent:** claude
"""
    assert roadmap._extract_guidelines_md(md) == ["one", "two"]
    assert roadmap._extract_global_agent_md(md) == "claude"
    assert roadmap._extract_guidelines_md("x") == []
    assert roadmap._extract_global_agent_md("x") is None


def test_parse_phase_block() -> None:
    block = """
**Status:** TODO
**Token Budget:** High
**Prerequisites:** Phase 1
**Agent:** codex

**Objective:**
build things

**Tasks:**
- [x] thing 1
- [ ] thing 2

**Verification:**
- [ ] verify
---
"""
    stage = roadmap._parse_phase_block(2, "Core", block)
    assert stage.id.startswith("phase-2-")
    assert stage.timeout_minutes == 150
    assert "Tasks:" in stage.instructions
    assert "Verification:" in stage.instructions


def test_parse_phase_block_inline_section_values() -> None:
    block = """
**Objective:** inline objective
**Tasks:** - [ ] inline task
**Verification:** - [ ] inline verify
"""
    stage = roadmap._parse_phase_block(1, "Inline", block)
    assert "inline objective" in stage.goal
    assert "inline task" in stage.instructions


def test_load_markdown_and_render(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.md"
    p.write_text(
        """## 2. Execution Phases

### Phase 1: Setup
**Status:** TODO
**Token Budget:** Medium
**Prerequisites:** None

**Objective:**
Do setup

**Tasks:**
- [ ] a

**Verification:**
- [ ] b
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p)
    assert rm.name == "roadmap"
    assert rm.tracks[0].agent == "codex"
    assert len(rm.tracks[0].stages) == 1

    prompt = roadmap.render_stage_prompt(
        project_name="p",
        project_root="/tmp/p",
        global_guidelines="",
        roadmap=rm,
        track=rm.tracks[0],
        stage=rm.tracks[0].stages[0],
        prior_summaries=["did x"],
        done_marker="DONE",
        input_marker="NEED",
    )
    assert "Project: p" in prompt
    assert "DONE" in prompt
    assert "NEED" in prompt


def test_load_markdown_with_default_agent_override(tmp_path: Path) -> None:
    p = tmp_path / "rm.markdown"
    p.write_text(
        """## 2. Execution Phases

### Phase 1: Setup
**Objective:**
Do setup
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p, default_agent="gemini")
    assert rm.tracks[0].agent == "gemini"

    rm2 = roadmap.load_roadmap(p, default_agent="   ")
    assert rm2.tracks[0].agent == "codex"


def test_load_yaml_valid(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.yaml"
    p.write_text(
        """version: 1
name: test
guidelines: [one]
tracks:
  - id: t1
    topic: topic
    agent: codex
    command: codex
    stages:
      - id: s1
        title: stage
        goal: do
        instructions: hi
        deliverables: [a]
        timeout_minutes: 3
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p)
    assert rm.name == "test"
    assert rm.tracks[0].command == "codex"


def test_load_yaml_with_default_agent(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.yml"
    p.write_text(
        """tracks:
  - id: t1
    topic: topic
    stages:
      - id: s1
        goal: do
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p, default_agent="claude")
    assert rm.tracks[0].agent == "claude"


def test_load_fallback_yaml_from_unknown_suffix(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.txt"
    p.write_text(
        """tracks:
  - id: t1
    topic: topic
    agent: codex
    stages:
      - id: s1
        goal: do
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p)
    assert rm.tracks[0].id == "t1"


def test_load_fallback_markdown_when_yaml_invalid(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.txt"
    p.write_text(
        """not: [yaml

### Phase 1: Setup
**Objective:**
Do setup
""",
        encoding="utf-8",
    )
    rm = roadmap.load_roadmap(p)
    assert rm.tracks[0].stages[0].title == "Phase 1: Setup"


def test_load_markdown_no_phase(tmp_path: Path) -> None:
    p = tmp_path / "roadmap.md"
    p.write_text("## nope", encoding="utf-8")
    with pytest.raises(roadmap.RoadmapValidationError, match="No phases found"):
        roadmap.load_roadmap(p)


@pytest.mark.parametrize(
    "content, msg",
    [
        ("[]", "Roadmap root must be a mapping"),
        ("version: x\ntracks: []", "version must be an integer"),
        ("name: ''\ntracks: []", "name must be a non-empty string"),
        ("name: n\nguidelines: x\ntracks: []", "guidelines must be a list of strings"),
        ("name: n\ntracks: []", "tracks must be a non-empty list"),
        ("name: n\ntracks: [x]", "each track must be a mapping"),
    ],
)
def test_yaml_validation_errors_top_level(tmp_path: Path, content: str, msg: str) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(roadmap.RoadmapValidationError, match=msg):
        roadmap.load_roadmap(p)


def test_yaml_validation_errors_nested(tmp_path: Path) -> None:
    bad_cases = [
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    command: 1\n    stages:\n      - id: s\n        goal: g\n""",
            "command must be a non-empty string",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages: []\n""",
            "stages must be a non-empty list",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages: [x]\n""",
            "stages entries must be mappings",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages:\n      - id: ''\n        goal: g\n""",
            "must be a non-empty string",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages:\n      - id: s\n        goal: g\n        instructions: 1\n""",
            "instructions must be a string",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages:\n      - id: s\n        goal: g\n        deliverables: x\n""",
            "deliverables must be a list of strings",
        ),
        (
            """tracks:\n  - id: t\n    topic: t\n    agent: codex\n    stages:\n      - id: s\n        goal: g\n        timeout_minutes: 0\n""",
            "timeout_minutes must be a positive integer",
        ),
    ]
    for idx, (content, msg) in enumerate(bad_cases):
        p = tmp_path / f"bad_{idx}.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(roadmap.RoadmapValidationError, match=msg):
            roadmap.load_roadmap(p)


def test_duplicate_ids_errors(tmp_path: Path) -> None:
    p1 = tmp_path / "dup_track.yaml"
    p1.write_text(
        """tracks:
  - id: t
    topic: a
    agent: codex
    stages:
      - id: s1
        goal: g
  - id: t
    topic: b
    agent: codex
    stages:
      - id: s2
        goal: g
""",
        encoding="utf-8",
    )
    with pytest.raises(roadmap.RoadmapValidationError, match="duplicate track id"):
        roadmap.load_roadmap(p1)

    p2 = tmp_path / "dup_stage.yaml"
    p2.write_text(
        """tracks:
  - id: t
    topic: a
    agent: codex
    stages:
      - id: s1
        goal: g
      - id: s1
        goal: g
""",
        encoding="utf-8",
    )
    with pytest.raises(roadmap.RoadmapValidationError, match="duplicate stage id"):
        roadmap.load_roadmap(p2)
