from __future__ import annotations

from pathlib import Path

from yeehaw.roadmap import RoadmapValidationError, load_roadmap

from .db import connect as connect_db
from .editor_bridge import open_in_editor
from .store import add_roadmap_revision, get_project, upsert_roadmap


def edit_roadmap_for_project(
    db_path: str | Path,
    project_name: str,
    roadmap_path: str | Path,
    roadmap_name: str = "roadmap",
    editor: str | None = None,
) -> tuple[int, int]:
    conn = connect_db(db_path)
    project = get_project(conn, project_name)
    if project is None:
        raise ValueError(f"project not found: {project_name}")

    project_root = Path(str(project["root_path"])).resolve()
    path = Path(roadmap_path).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("## 2. Execution Phases\n\n", encoding="utf-8")

    open_in_editor(path, editor=editor)
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError(f"roadmap file is empty: {path}")

    roadmap_id = upsert_roadmap(
        conn,
        project_id=int(project["id"]),
        name=roadmap_name,
        path=path,
        status="edited",
    )
    revision_id = add_roadmap_revision(conn, roadmap_id=roadmap_id, source="editor", raw_text=raw)
    return roadmap_id, revision_id


def validate_roadmap(path: str | Path, default_agent: str = "codex") -> tuple[bool, str]:
    try:
        roadmap = load_roadmap(path, default_agent=default_agent)
    except RoadmapValidationError as exc:
        return False, str(exc)
    stage_count = sum(len(track.stages) for track in roadmap.tracks)
    return True, f"valid roadmap '{roadmap.name}' with {len(roadmap.tracks)} tracks and {stage_count} stages"
