from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class RoadmapValidationError(ValueError):
    """Raised when a roadmap file is invalid."""


PHASE_HEADING_RE = re.compile(r"^###\s*Phase\s+(\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(
    r"^\*\*(Status|Token Budget|Prerequisites|Objective|Tasks|Verification|Agent):\*\*\s*(.*)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class StageDef:
    id: str
    title: str
    goal: str
    instructions: str = ""
    deliverables: list[str] = field(default_factory=list)
    timeout_minutes: int = 90


@dataclass(slots=True)
class TrackDef:
    id: str
    topic: str
    agent: str
    command: str | None = None
    stages: list[StageDef] = field(default_factory=list)


@dataclass(slots=True)
class RoadmapDef:
    version: int
    name: str
    guidelines: list[str]
    tracks: list[TrackDef]
    raw_text: str


@dataclass(slots=True)
class ProjectGuidelines:
    name: str
    root: str
    guidelines: str


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RoadmapValidationError("Roadmap root must be a mapping")
    return data


def _as_non_empty_str(obj: Any, field_name: str) -> str:
    if not isinstance(obj, str) or not obj.strip():
        raise RoadmapValidationError(f"{field_name} must be a non-empty string")
    return obj.strip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "phase"


def _timeout_from_budget(token_budget: str) -> int:
    key = token_budget.strip().lower()
    if "low" in key:
        return 45
    if "high" in key:
        return 150
    return 90


def _normalize_checklist_line(line: str) -> str:
    text = line.strip()
    if text.startswith("- ["):
        close_bracket = text.find("]")
        if close_bracket != -1 and close_bracket + 1 < len(text):
            text = "- " + text[close_bracket + 1 :].strip()
    return text


def _join_paragraph_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return " ".join(cleaned).strip()


def _extract_guidelines_md(raw_text: str) -> list[str]:
    header_match = re.search(r"^##\s+(Roadmap\s+)?Guidelines\s*$", raw_text, re.IGNORECASE | re.MULTILINE)
    if not header_match:
        return []

    tail = raw_text[header_match.end() :]
    next_header = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_header.start()] if next_header else tail

    guidelines: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            guidelines.append(stripped[2:].strip())
    return [g for g in guidelines if g]


def _extract_global_agent_md(raw_text: str) -> str | None:
    for line in raw_text.splitlines():
        match = FIELD_RE.match(line.strip())
        if not match:
            continue
        if match.group(1).strip().lower() == "agent":
            value = match.group(2).strip()
            if value:
                return value
    return None


def _parse_phase_block(phase_number: int, phase_title: str, block_text: str) -> StageDef:
    status = "TODO"
    token_budget = "Medium"
    prerequisites = "None"
    sections: dict[str, list[str]] = {
        "objective": [],
        "tasks": [],
        "verification": [],
    }
    current_section: str | None = None

    for raw_line in block_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        match = FIELD_RE.match(stripped)
        if match:
            field_name = match.group(1).strip().lower()
            value = match.group(2).strip()

            if field_name == "status":
                status = value or status
                current_section = None
            elif field_name == "token budget":
                token_budget = value or token_budget
                current_section = None
            elif field_name == "prerequisites":
                prerequisites = value or prerequisites
                current_section = None
            elif field_name in sections:
                current_section = field_name
                if value:
                    sections[current_section].append(value)
            else:
                current_section = None
            continue

        if stripped == "---":
            continue

        if current_section in sections:
            if stripped:
                sections[current_section].append(stripped)
            elif sections[current_section] and sections[current_section][-1] != "":
                sections[current_section].append("")

    objective = _join_paragraph_lines(sections["objective"]) or phase_title

    tasks = [_normalize_checklist_line(line) for line in sections["tasks"] if line.strip().startswith("-")]
    verification = [
        _normalize_checklist_line(line) for line in sections["verification"] if line.strip().startswith("-")
    ]

    instructions_parts = [
        "Legacy phase metadata:",
        f"- Status: {status}",
        f"- Token Budget: {token_budget}",
        f"- Prerequisites: {prerequisites}",
    ]

    if tasks:
        instructions_parts.extend(["", "Tasks:", *tasks])
    if verification:
        instructions_parts.extend(["", "Verification:", *verification])

    stage_id = f"phase-{phase_number}-{_slugify(phase_title)}"
    return StageDef(
        id=stage_id,
        title=f"Phase {phase_number}: {phase_title}",
        goal=objective,
        instructions="\n".join(instructions_parts).strip(),
        deliverables=[],
        timeout_minutes=_timeout_from_budget(token_budget),
    )


def _load_roadmap_from_markdown(path: Path, raw_text: str, default_agent: str | None) -> RoadmapDef:
    phase_matches = list(PHASE_HEADING_RE.finditer(raw_text))
    if not phase_matches:
        raise RoadmapValidationError(
            "No phases found. Expected markdown headings like '### Phase 1: <title>'."
        )

    stages: list[StageDef] = []
    for idx, phase_match in enumerate(phase_matches):
        phase_number = int(phase_match.group(1))
        phase_title = phase_match.group(2).strip()
        start = phase_match.end()
        end = phase_matches[idx + 1].start() if idx + 1 < len(phase_matches) else len(raw_text)
        block_text = raw_text[start:end]
        stages.append(_parse_phase_block(phase_number, phase_title, block_text))

    agent = (default_agent or _extract_global_agent_md(raw_text) or "codex").strip()
    if not agent:
        agent = "codex"

    track = TrackDef(
        id="main",
        topic="Execution Phases",
        agent=agent,
        stages=stages,
    )

    return RoadmapDef(
        version=1,
        name=path.stem,
        guidelines=_extract_guidelines_md(raw_text),
        tracks=[track],
        raw_text=raw_text,
    )


def _load_roadmap_from_yaml(path: Path, data: dict[str, Any], default_agent: str | None) -> RoadmapDef:
    version = data.get("version", 1)
    if not isinstance(version, int):
        raise RoadmapValidationError("version must be an integer")

    name = _as_non_empty_str(data.get("name", path.stem), "name")

    raw_guidelines = data.get("guidelines", [])
    if not isinstance(raw_guidelines, list) or not all(isinstance(x, str) for x in raw_guidelines):
        raise RoadmapValidationError("guidelines must be a list of strings")
    guidelines = [g.strip() for g in raw_guidelines if g.strip()]

    raw_tracks = data.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise RoadmapValidationError("tracks must be a non-empty list")

    tracks: list[TrackDef] = []
    track_ids: set[str] = set()

    for track_obj in raw_tracks:
        if not isinstance(track_obj, dict):
            raise RoadmapValidationError("each track must be a mapping")

        track_id = _as_non_empty_str(track_obj.get("id"), "track.id")
        if track_id in track_ids:
            raise RoadmapValidationError(f"duplicate track id: {track_id}")
        track_ids.add(track_id)

        topic = _as_non_empty_str(track_obj.get("topic", track_id), f"track[{track_id}].topic")
        raw_agent = track_obj.get("agent")
        if raw_agent is None:
            raw_agent = default_agent
        agent = _as_non_empty_str(raw_agent, f"track[{track_id}].agent")

        command = track_obj.get("command")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            raise RoadmapValidationError(f"track[{track_id}].command must be a non-empty string if provided")
        if isinstance(command, str):
            command = command.strip()

        raw_stages = track_obj.get("stages")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise RoadmapValidationError(f"track[{track_id}].stages must be a non-empty list")

        stage_ids: set[str] = set()
        stages: list[StageDef] = []
        for stage_obj in raw_stages:
            if not isinstance(stage_obj, dict):
                raise RoadmapValidationError(f"track[{track_id}] stages entries must be mappings")

            stage_id = _as_non_empty_str(stage_obj.get("id"), f"track[{track_id}].stage.id")
            if stage_id in stage_ids:
                raise RoadmapValidationError(f"duplicate stage id in track {track_id}: {stage_id}")
            stage_ids.add(stage_id)

            goal = _as_non_empty_str(stage_obj.get("goal"), f"track[{track_id}].stage[{stage_id}].goal")
            title = _as_non_empty_str(stage_obj.get("title", stage_id), f"track[{track_id}].stage[{stage_id}].title")

            instructions = stage_obj.get("instructions", "")
            if not isinstance(instructions, str):
                raise RoadmapValidationError(
                    f"track[{track_id}].stage[{stage_id}].instructions must be a string"
                )
            instructions = instructions.strip()

            raw_deliverables = stage_obj.get("deliverables", [])
            if not isinstance(raw_deliverables, list) or not all(isinstance(x, str) for x in raw_deliverables):
                raise RoadmapValidationError(
                    f"track[{track_id}].stage[{stage_id}].deliverables must be a list of strings"
                )
            deliverables = [d.strip() for d in raw_deliverables if d.strip()]

            timeout_minutes = stage_obj.get("timeout_minutes", 90)
            if not isinstance(timeout_minutes, int) or timeout_minutes <= 0:
                raise RoadmapValidationError(
                    f"track[{track_id}].stage[{stage_id}].timeout_minutes must be a positive integer"
                )

            stages.append(
                StageDef(
                    id=stage_id,
                    title=title,
                    goal=goal,
                    instructions=instructions,
                    deliverables=deliverables,
                    timeout_minutes=timeout_minutes,
                )
            )

        tracks.append(
            TrackDef(
                id=track_id,
                topic=topic,
                agent=agent,
                command=command,
                stages=stages,
            )
        )

    raw_text = path.read_text(encoding="utf-8")
    return RoadmapDef(version=version, name=name, guidelines=guidelines, tracks=tracks, raw_text=raw_text)


def load_roadmap(path: str | Path, default_agent: str | None = None) -> RoadmapDef:
    roadmap_path = Path(path)
    suffix = roadmap_path.suffix.lower()

    raw_text = roadmap_path.read_text(encoding="utf-8")
    if suffix in {".md", ".markdown"}:
        return _load_roadmap_from_markdown(roadmap_path, raw_text, default_agent=default_agent)

    if suffix in {".yaml", ".yml"}:
        data = _read_yaml(roadmap_path)
        return _load_roadmap_from_yaml(roadmap_path, data, default_agent=default_agent)

    try:
        data = yaml.safe_load(raw_text)
        if isinstance(data, dict) and "tracks" in data:
            return _load_roadmap_from_yaml(roadmap_path, data, default_agent=default_agent)
    except yaml.YAMLError:
        pass

    return _load_roadmap_from_markdown(roadmap_path, raw_text, default_agent=default_agent)


def render_stage_prompt(
    project_name: str,
    project_root: str,
    global_guidelines: str,
    roadmap: RoadmapDef,
    track: TrackDef,
    stage: StageDef,
    prior_summaries: list[str],
    done_marker: str,
    input_marker: str,
) -> str:
    summary_block = "\n".join(f"- {line}" for line in prior_summaries) if prior_summaries else "- None yet"
    deliverables_block = "\n".join(f"- {d}" for d in stage.deliverables) if stage.deliverables else "- No strict deliverables"

    parts = [
        f"Project: {project_name}",
        f"Project root: {project_root}",
        f"Roadmap: {roadmap.name}",
        f"Track: {track.id} ({track.topic})",
        f"Stage: {stage.id} ({stage.title})",
        "",
        "Global guidelines:",
        global_guidelines.strip() or "(none)",
        "",
        "Roadmap-level guidelines:",
        "\n".join(f"- {g}" for g in roadmap.guidelines) or "- None",
        "",
        "Prior stage summaries:",
        summary_block,
        "",
        "Stage goal:",
        stage.goal,
        "",
        "Deliverables:",
        deliverables_block,
    ]

    if stage.instructions:
        parts.extend(["", "Additional stage instructions:", stage.instructions])

    parts.extend(
        [
            "",
            "Execution policy:",
            "- Work autonomously and do not ask for confirmation unless absolutely blocked.",
            "- Prefer concrete file edits and runnable outcomes over high-level advice.",
            "- Keep changes scoped to this stage's goal.",
            "",
            "If you are blocked and truly need human input, print exactly:",
            input_marker,
            "Then print one line starting with: Question:",
            "",
            "When the stage is complete, print exactly:",
            done_marker,
            "Then print:",
            "Summary:",
            "- up to 5 bullets",
            "Artifacts:",
            "- relative/path.ext",
        ]
    )

    return "\n".join(parts).strip() + "\n"
