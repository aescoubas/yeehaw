"""Roadmap Markdown parser and validator."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Task:
    """Task extracted from a roadmap phase."""

    number: str
    title: str
    description: str


@dataclass
class Phase:
    """Phase extracted from the roadmap."""

    number: int
    title: str
    verify_cmd: str | None
    tasks: list[Task] = field(default_factory=list)


@dataclass
class Roadmap:
    """Root parsed roadmap model."""

    project_name: str
    phases: list[Phase] = field(default_factory=list)


_RE_HEADER = re.compile(r"^#\s+Roadmap:\s+(.+)$")
_RE_PHASE = re.compile(r"^##\s+Phase\s+(\d+):\s+(.+)$")
_RE_VERIFY = re.compile(r"^\*\*Verify:\*\*\s+`(.+)`$")
_RE_TASK = re.compile(r"^###\s+Task\s+([\d.]+):\s+(.+)$")


def parse_roadmap(text: str) -> Roadmap:
    """Parse roadmap markdown into structured models."""
    lines = text.strip().splitlines()
    roadmap: Roadmap | None = None
    current_phase: Phase | None = None
    current_task: Task | None = None
    task_lines: list[str] = []

    def flush_task() -> None:
        nonlocal current_task, task_lines
        if current_task is not None:
            current_task.description = "\n".join(task_lines).strip()
            task_lines = []

    for line in lines:
        header_match = _RE_HEADER.match(line)
        if header_match:
            roadmap = Roadmap(project_name=header_match.group(1).strip())
            continue

        phase_match = _RE_PHASE.match(line)
        if phase_match:
            flush_task()
            current_task = None
            current_phase = Phase(
                number=int(phase_match.group(1)),
                title=phase_match.group(2).strip(),
                verify_cmd=None,
            )
            if roadmap is not None:
                roadmap.phases.append(current_phase)
            continue

        verify_match = _RE_VERIFY.match(line)
        if verify_match and current_phase is not None and not current_phase.tasks:
            current_phase.verify_cmd = verify_match.group(1)
            continue

        task_match = _RE_TASK.match(line)
        if task_match:
            flush_task()
            current_task = Task(
                number=task_match.group(1),
                title=task_match.group(2).strip(),
                description="",
            )
            task_lines = []
            if current_phase is not None:
                current_phase.tasks.append(current_task)
            continue

        if current_task is not None:
            task_lines.append(line)

    flush_task()

    if roadmap is None:
        raise ValueError("Missing roadmap header: '# Roadmap: <name>'")

    return roadmap


def validate_roadmap(roadmap: Roadmap) -> list[str]:
    """Validate roadmap structural sequencing and completeness."""
    errors: list[str] = []

    if not roadmap.phases:
        errors.append("Roadmap must have at least one phase")
        return errors

    for i, phase in enumerate(roadmap.phases):
        expected_num = i + 1
        if phase.number != expected_num:
            errors.append(
                f"Phase {phase.number} out of sequence (expected {expected_num})"
            )
        if not phase.tasks:
            errors.append(f"Phase {phase.number} has no tasks")

        for j, task in enumerate(phase.tasks):
            expected_task = f"{phase.number}.{j + 1}"
            if task.number != expected_task:
                errors.append(
                    f"Task {task.number} out of sequence (expected {expected_task})"
                )

    return errors
