"""Roadmap Markdown parser and validator."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from yeehaw.roadmap.dependencies import parse_task_dependencies


@dataclass
class Task:
    """Task extracted from a roadmap phase."""

    number: str
    title: str
    description: str
    file_targets: list[str] = field(default_factory=list)


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
_RE_TASK = re.compile(r"^###\s+(?:Task\s+)?([Pp]?\d+\.\d+):\s+(.+)$")
_RE_TASK_STATUS_SUFFIX = re.compile(r"\s+\[(?:x|X| )\]\s*$")
_RE_TASK_COMPONENTS = re.compile(r"^[Pp]?(\d+)\.(\d+)$")
_RE_FILES_HEADER = re.compile(r"^\*\*Files:\*\*\s*$", re.IGNORECASE)
_RE_METADATA_HEADER = re.compile(r"^\*\*[^*]+:\*\*\s*")
_RE_BULLET_LINE = re.compile(r"^[-*]\s+(.*)$")
_RE_CODE_SPAN = re.compile(r"`([^`]+)`")


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
            current_task.file_targets = parse_task_file_targets(current_task.description)
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
            raw_task_num = task_match.group(1).strip()
            normalized_task_num = _normalize_task_number(raw_task_num)
            raw_title = task_match.group(2).strip()
            title = _RE_TASK_STATUS_SUFFIX.sub("", raw_title).strip()
            current_task = Task(
                number=normalized_task_num,
                title=title,
                description="",
                file_targets=[],
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

    phase_start = 0 if roadmap.phases[0].number == 0 else 1

    for i, phase in enumerate(roadmap.phases):
        expected_num = phase_start + i
        if phase.number != expected_num:
            errors.append(
                f"Phase {phase.number} out of sequence (expected {expected_num})"
            )
        if not phase.tasks:
            errors.append(f"Phase {phase.number} has no tasks")

        for j, task in enumerate(phase.tasks):
            expected_task = f"{phase.number}.{j + 1}"
            components = _parse_task_components(task.number)
            if components is None:
                errors.append(
                    f"Task {task.number} has invalid number format (expected {expected_task})"
                )
                continue
            if components != (phase.number, j + 1):
                errors.append(
                    f"Task {task.number} out of sequence (expected {expected_task})"
                )

    dependency_graph: dict[str, list[str]] = {}
    task_numbers = {
        task.number
        for phase in roadmap.phases
        for task in phase.tasks
    }

    for phase in roadmap.phases:
        for task in phase.tasks:
            deps = parse_task_dependencies(task.description)
            dependency_graph[task.number] = deps
            for dep in deps:
                if dep not in task_numbers:
                    errors.append(f"Task {task.number} depends on unknown task {dep}")
                if dep == task.number:
                    errors.append(f"Task {task.number} cannot depend on itself")

    errors.extend(_validate_dependency_cycles(dependency_graph))

    return errors


def parse_task_file_targets(description: str) -> list[str]:
    """Extract normalized `**Files:**` metadata entries from task description text."""
    targets: list[str] = []
    seen: set[str] = set()
    in_files_block = False

    for raw_line in description.splitlines():
        stripped = raw_line.strip()
        if _RE_FILES_HEADER.match(stripped):
            in_files_block = True
            continue

        if not in_files_block:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            break

        if _RE_METADATA_HEADER.match(stripped):
            in_files_block = _RE_FILES_HEADER.match(stripped) is not None
            continue

        bullet = _RE_BULLET_LINE.match(stripped)
        if bullet is None:
            continue

        target = _normalize_file_target(_extract_file_target_candidate(bullet.group(1)))
        if target is None or target in seen:
            continue
        targets.append(target)
        seen.add(target)

    return targets


def _validate_dependency_cycles(graph: dict[str, list[str]]) -> list[str]:
    """Detect dependency cycles and return validation errors."""
    temp_mark: set[str] = set()
    perm_mark: set[str] = set()
    stack: list[str] = []
    errors: list[str] = []

    def visit(node: str) -> None:
        if node in perm_mark:
            return
        if node in temp_mark:
            if errors:
                return
            cycle_start = stack.index(node) if node in stack else 0
            cycle_path = stack[cycle_start:] + [node]
            errors.append(
                "Task dependency cycle detected: " + " -> ".join(cycle_path)
            )
            return

        temp_mark.add(node)
        stack.append(node)
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep)
        stack.pop()
        temp_mark.remove(node)
        perm_mark.add(node)

    for candidate in graph:
        if errors:
            break
        visit(candidate)
    return errors


def _normalize_task_number(raw_number: str) -> str:
    """Normalize task numbers (e.g. P0.1 -> 0.1) for storage and validation."""
    components = _parse_task_components(raw_number)
    if components is None:
        return raw_number
    return f"{components[0]}.{components[1]}"


def _parse_task_components(task_number: str) -> tuple[int, int] | None:
    """Parse task number into (phase, index), allowing optional leading P prefix."""
    match = _RE_TASK_COMPONENTS.match(task_number.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _extract_file_target_candidate(raw_item: str) -> str:
    """Extract path-like token from one Files-list bullet item."""
    code_match = _RE_CODE_SPAN.search(raw_item)
    if code_match:
        return code_match.group(1).strip()

    candidate = raw_item.strip()
    for separator in (" — ", " – ", " - "):
        if separator in candidate:
            return candidate.split(separator, 1)[0].strip()
    return candidate


def _normalize_file_target(raw_target: str) -> str | None:
    """Normalize file target text into stable slash-delimited path tokens."""
    value = raw_target.strip().strip("`").strip().strip("\"'")
    if not value:
        return None

    value = value.replace("\\", "/")
    parts = [part for part in value.split("/") if part and part != "."]
    if not parts:
        return None

    normalized = PurePosixPath(*parts).as_posix().strip()
    if not normalized or normalized == ".":
        return None
    return normalized
