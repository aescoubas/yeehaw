from __future__ import annotations

import os
import sqlite3
import json
from pathlib import Path
from typing import Iterable

from .roadmap import RoadmapDef, StageDef, TrackDef


def utc_now() -> str:
    # SQLite stores UTC timestamp strings; this format is stable and sortable.
    return "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def default_db_path() -> Path:
    override = os.getenv("YEEHAW_DB")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / ".yeehaw" / "yeehaw.db").resolve()


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            root_path TEXT NOT NULL,
            guidelines TEXT NOT NULL DEFAULT '',
            git_remote_url TEXT,
            default_branch TEXT,
            head_sha TEXT,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS roadmaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS roadmap_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
            track_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            agent TEXT NOT NULL,
            command TEXT,
            position INTEGER NOT NULL,
            UNIQUE(roadmap_id, track_id)
        );

        CREATE TABLE IF NOT EXISTS roadmap_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roadmap_track_id INTEGER NOT NULL REFERENCES roadmap_tracks(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            instructions TEXT NOT NULL,
            timeout_minutes INTEGER NOT NULL,
            deliverables_json TEXT NOT NULL,
            position INTEGER NOT NULL,
            UNIQUE(roadmap_track_id, stage_id)
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            tmux_session TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()}),
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS track_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            track_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            agent TEXT NOT NULL,
            window_name TEXT NOT NULL,
            status TEXT NOT NULL,
            current_stage_index INTEGER NOT NULL DEFAULT 0,
            waiting_question TEXT,
            last_pane TEXT,
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()}),
            UNIQUE(run_id, track_id)
        );

        CREATE TABLE IF NOT EXISTS stage_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_run_id INTEGER NOT NULL REFERENCES track_runs(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            token TEXT NOT NULL,
            baseline_done_count INTEGER NOT NULL DEFAULT 0,
            baseline_input_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            artifacts TEXT,
            pane_snapshot TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            track_run_id INTEGER REFERENCES track_runs(id) ON DELETE SET NULL,
            stage_run_id INTEGER REFERENCES stage_runs(id) ON DELETE SET NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );
        """
    )
    _migrate_projects_table(conn)
    conn.commit()


def _migrate_projects_table(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "git_remote_url" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN git_remote_url TEXT")
    if "default_branch" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN default_branch TEXT")
    if "head_sha" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN head_sha TEXT")


def create_project(
    conn: sqlite3.Connection,
    name: str,
    root_path: str,
    guidelines: str,
    git_remote_url: str | None = None,
    default_branch: str | None = None,
    head_sha: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO projects(name, root_path, guidelines, git_remote_url, default_branch, head_sha)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            root_path = excluded.root_path,
            guidelines = excluded.guidelines,
            git_remote_url = excluded.git_remote_url,
            default_branch = excluded.default_branch,
            head_sha = excluded.head_sha
        """,
        (name, root_path, guidelines, git_remote_url, default_branch, head_sha),
    )
    conn.commit()

    row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError("failed to upsert project")
    return int(row["id"])


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, root_path, git_remote_url, default_branch, head_sha, created_at FROM projects ORDER BY name"
    ).fetchall()


def get_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name, root_path, guidelines, git_remote_url, default_branch, head_sha, created_at
        FROM projects WHERE name = ?
        """,
        (name,),
    ).fetchone()


def insert_roadmap(conn: sqlite3.Connection, project_id: int, roadmap: RoadmapDef) -> int:
    cur = conn.execute(
        "INSERT INTO roadmaps(project_id, name, version, raw_text) VALUES (?, ?, ?, ?)",
        (project_id, roadmap.name, roadmap.version, roadmap.raw_text),
    )
    roadmap_id = int(cur.lastrowid)

    for track_pos, track in enumerate(roadmap.tracks):
        track_cur = conn.execute(
            """
            INSERT INTO roadmap_tracks(roadmap_id, track_id, topic, agent, command, position)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (roadmap_id, track.id, track.topic, track.agent, track.command, track_pos),
        )
        roadmap_track_id = int(track_cur.lastrowid)

        for stage_pos, stage in enumerate(track.stages):
            conn.execute(
                """
                INSERT INTO roadmap_stages(
                    roadmap_track_id, stage_id, title, goal, instructions,
                    timeout_minutes, deliverables_json, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    roadmap_track_id,
                    stage.id,
                    stage.title,
                    stage.goal,
                    stage.instructions,
                    stage.timeout_minutes,
                    _to_json_array(stage.deliverables),
                    stage_pos,
                ),
            )

    conn.commit()
    return roadmap_id


def _to_json_array(items: Iterable[str]) -> str:
    return json.dumps(list(items))


def create_run(conn: sqlite3.Connection, project_id: int, roadmap_id: int, tmux_session: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(project_id, roadmap_id, status, tmux_session) VALUES (?, ?, 'running', ?)",
        (project_id, roadmap_id, tmux_session),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_run_status(conn: sqlite3.Connection, run_id: int, status: str, finished: bool = False) -> None:
    if finished:
        conn.execute(
            f"UPDATE runs SET status = ?, updated_at = ({utc_now()}), finished_at = ({utc_now()}) WHERE id = ?",
            (status, run_id),
        )
    else:
        conn.execute(
            f"UPDATE runs SET status = ?, updated_at = ({utc_now()}) WHERE id = ?",
            (status, run_id),
        )
    conn.commit()


def create_track_run(
    conn: sqlite3.Connection,
    run_id: int,
    track: TrackDef,
    window_name: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO track_runs(run_id, track_id, topic, agent, window_name, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (run_id, track.id, track.topic, track.agent, window_name),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_track_run_state(
    conn: sqlite3.Connection,
    track_run_id: int,
    status: str,
    current_stage_index: int | None = None,
    waiting_question: str | None = None,
    last_pane: str | None = None,
) -> None:
    assigns = ["status = ?", f"updated_at = ({utc_now()})"]
    params: list[object] = [status]

    if current_stage_index is not None:
        assigns.append("current_stage_index = ?")
        params.append(current_stage_index)

    if waiting_question is not None:
        assigns.append("waiting_question = ?")
        params.append(waiting_question)

    if last_pane is not None:
        assigns.append("last_pane = ?")
        params.append(last_pane)

    params.append(track_run_id)
    sql = f"UPDATE track_runs SET {', '.join(assigns)} WHERE id = ?"
    conn.execute(sql, params)
    conn.commit()


def create_stage_run(
    conn: sqlite3.Connection,
    track_run_id: int,
    stage: StageDef,
    token: str,
    baseline_done_count: int,
    baseline_input_count: int,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO stage_runs(
            track_run_id, stage_id, title, status, token,
            baseline_done_count, baseline_input_count, started_at, updated_at
        ) VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ({utc_now()}), ({utc_now()}))
        """,
        (track_run_id, stage.id, stage.title, token, baseline_done_count, baseline_input_count),
    )
    conn.commit()
    return int(cur.lastrowid)


def complete_stage_run(
    conn: sqlite3.Connection,
    stage_run_id: int,
    status: str,
    summary: str,
    artifacts: str,
    pane_snapshot: str,
) -> None:
    conn.execute(
        f"""
        UPDATE stage_runs
        SET status = ?, summary = ?, artifacts = ?, pane_snapshot = ?,
            updated_at = ({utc_now()}), finished_at = ({utc_now()})
        WHERE id = ?
        """,
        (status, summary, artifacts, pane_snapshot, stage_run_id),
    )
    conn.commit()


def set_stage_run_awaiting_input(
    conn: sqlite3.Connection,
    stage_run_id: int,
    pane_snapshot: str,
) -> None:
    conn.execute(
        f"""
        UPDATE stage_runs
        SET status = 'awaiting_input', pane_snapshot = ?, updated_at = ({utc_now()})
        WHERE id = ?
        """,
        (pane_snapshot, stage_run_id),
    )
    conn.commit()


def get_stage_summaries(conn: sqlite3.Connection, track_run_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT summary FROM stage_runs
        WHERE track_run_id = ? AND status = 'completed' AND summary IS NOT NULL
        ORDER BY id
        """,
        (track_run_id,),
    ).fetchall()
    return [str(r["summary"]).strip() for r in rows if str(r["summary"]).strip()]


def add_event(
    conn: sqlite3.Connection,
    run_id: int,
    level: str,
    message: str,
    track_run_id: int | None = None,
    stage_run_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO events(run_id, track_run_id, stage_run_id, level, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, track_run_id, stage_run_id, level, message),
    )
    conn.commit()


def latest_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.id, p.name AS project_name, rm.name AS roadmap_name,
               r.status, r.tmux_session, r.created_at, r.updated_at, r.finished_at
        FROM runs r
        JOIN projects p ON p.id = r.project_id
        JOIN roadmaps rm ON rm.id = r.roadmap_id
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def run_tracks(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, track_id, topic, agent, window_name, status,
               current_stage_index, waiting_question, updated_at
        FROM track_runs
        WHERE run_id = ?
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()


def run_events(conn: sqlite3.Connection, run_id: int, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT created_at, level, message
        FROM events
        WHERE run_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.*, p.name AS project_name, p.root_path AS project_root, p.guidelines
        FROM runs r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = ?
        """,
        (run_id,),
    ).fetchone()
