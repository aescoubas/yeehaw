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

        CREATE TABLE IF NOT EXISTS scheduler_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_global_sessions INTEGER NOT NULL DEFAULT 20,
            max_project_sessions INTEGER NOT NULL DEFAULT 10,
            default_stuck_minutes INTEGER NOT NULL DEFAULT 12,
            auto_reassign INTEGER NOT NULL DEFAULT 1,
            preemption_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS task_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            source_text TEXT NOT NULL,
            roadmap_path TEXT,
            roadmap_text TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            priority TEXT NOT NULL DEFAULT 'medium',
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES task_batches(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            track_id TEXT,
            stage_id TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'queued',
            preferred_agent TEXT,
            assigned_agent TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            branch_name TEXT,
            worktree_path TEXT,
            base_sha TEXT,
            tmux_session TEXT,
            tmux_target TEXT,
            run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
            track_run_id INTEGER REFERENCES track_runs(id) ON DELETE SET NULL,
            stage_run_id INTEGER REFERENCES stage_runs(id) ON DELETE SET NULL,
            blocked_question TEXT,
            last_output_hash TEXT,
            loop_count INTEGER NOT NULL DEFAULT 0,
            last_progress_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            level TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()}),
            updated_at TEXT NOT NULL DEFAULT ({utc_now()}),
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            agent TEXT NOT NULL,
            status TEXT NOT NULL,
            tmux_session TEXT NOT NULL,
            tmux_target TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT ({utc_now()}),
            last_heartbeat_at TEXT,
            last_progress_at TEXT,
            ended_at TEXT
        );

        CREATE TABLE IF NOT EXISTS operator_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS roadmap_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            batch_id INTEGER REFERENCES task_batches(id) ON DELETE SET NULL,
            path TEXT NOT NULL,
            version INTEGER NOT NULL,
            source TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );

        CREATE TABLE IF NOT EXISTS phase_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            decisions TEXT NOT NULL DEFAULT '',
            next_context TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ({utc_now()})
        );
        """
    )
    _migrate_projects_table(conn)
    _migrate_tasks_table(conn)
    _migrate_scheduler_config(conn)
    conn.commit()


def _migrate_projects_table(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "git_remote_url" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN git_remote_url TEXT")
    if "default_branch" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN default_branch TEXT")
    if "head_sha" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN head_sha TEXT")


def _migrate_tasks_table(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "worktree_path" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN worktree_path TEXT")


def _migrate_scheduler_config(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO scheduler_config(id, max_global_sessions, max_project_sessions, default_stuck_minutes, auto_reassign, preemption_enabled)
        VALUES (1, 20, 10, 12, 1, 1)
        ON CONFLICT(id) DO NOTHING
        """
    )


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


def scheduler_config(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT max_global_sessions, max_project_sessions, default_stuck_minutes,
               auto_reassign, preemption_enabled
        FROM scheduler_config
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        _migrate_scheduler_config(conn)
        conn.commit()
        row = conn.execute(
            """
            SELECT max_global_sessions, max_project_sessions, default_stuck_minutes,
                   auto_reassign, preemption_enabled
            FROM scheduler_config
            WHERE id = 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("scheduler config row missing")
    return row


def update_scheduler_config(
    conn: sqlite3.Connection,
    max_global_sessions: int | None = None,
    max_project_sessions: int | None = None,
    default_stuck_minutes: int | None = None,
    auto_reassign: bool | None = None,
    preemption_enabled: bool | None = None,
) -> None:
    assigns: list[str] = [f"updated_at = ({utc_now()})"]
    params: list[object] = []
    if max_global_sessions is not None:
        assigns.append("max_global_sessions = ?")
        params.append(max_global_sessions)
    if max_project_sessions is not None:
        assigns.append("max_project_sessions = ?")
        params.append(max_project_sessions)
    if default_stuck_minutes is not None:
        assigns.append("default_stuck_minutes = ?")
        params.append(default_stuck_minutes)
    if auto_reassign is not None:
        assigns.append("auto_reassign = ?")
        params.append(1 if auto_reassign else 0)
    if preemption_enabled is not None:
        assigns.append("preemption_enabled = ?")
        params.append(1 if preemption_enabled else 0)
    params.append(1)
    conn.execute(f"UPDATE scheduler_config SET {', '.join(assigns)} WHERE id = ?", params)
    conn.commit()


def create_task_batch(
    conn: sqlite3.Connection,
    project_id: int,
    name: str,
    source_text: str,
    priority: str = "medium",
    roadmap_path: str | None = None,
    roadmap_text: str | None = None,
    status: str = "draft",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO task_batches(project_id, name, source_text, roadmap_path, roadmap_text, status, priority)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, name, source_text, roadmap_path, roadmap_text, status, priority),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_task_batch_status(conn: sqlite3.Connection, batch_id: int, status: str) -> None:
    conn.execute(
        f"UPDATE task_batches SET status = ?, updated_at = ({utc_now()}) WHERE id = ?",
        (status, batch_id),
    )
    conn.commit()


def update_task_batch_roadmap(
    conn: sqlite3.Connection,
    batch_id: int,
    roadmap_path: str,
    roadmap_text: str,
) -> None:
    conn.execute(
        f"""
        UPDATE task_batches
        SET roadmap_path = ?, roadmap_text = ?, updated_at = ({utc_now()})
        WHERE id = ?
        """,
        (roadmap_path, roadmap_text, batch_id),
    )
    conn.commit()


def list_task_batches(conn: sqlite3.Connection, project_id: int | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if project_id is None:
        return conn.execute(
            """
            SELECT b.*, p.name AS project_name
            FROM task_batches b
            JOIN projects p ON p.id = b.project_id
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT b.*, p.name AS project_name
        FROM task_batches b
        JOIN projects p ON p.id = b.project_id
        WHERE b.project_id = ?
        ORDER BY b.id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()


def get_task_batch(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT b.*, p.name AS project_name, p.root_path AS project_root
        FROM task_batches b
        JOIN projects p ON p.id = b.project_id
        WHERE b.id = ?
        """,
        (batch_id,),
    ).fetchone()


def create_task(
    conn: sqlite3.Connection,
    batch_id: int,
    project_id: int,
    title: str,
    description: str = "",
    priority: str = "medium",
    preferred_agent: str | None = None,
    track_id: str | None = None,
    stage_id: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO tasks(batch_id, project_id, title, description, priority, preferred_agent, track_id, stage_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (batch_id, project_id, title, description, priority, preferred_agent, track_id, stage_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_tasks(
    conn: sqlite3.Connection,
    status: str | None = None,
    project_id: int | None = None,
    batch_id: int | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("t.status = ?")
        params.append(status)
    if project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(project_id)
    if batch_id is not None:
        clauses.append("t.batch_id = ?")
        params.append(batch_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    return conn.execute(
        f"""
        SELECT t.*, p.name AS project_name
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        {where_sql}
        ORDER BY t.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT t.*, p.name AS project_name, p.root_path AS project_root, p.default_branch, p.guidelines
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()


def count_active_tasks(conn: sqlite3.Connection, project_id: int | None = None) -> int:
    active_statuses = ("dispatching", "running", "awaiting_input", "stuck", "preempting")
    if project_id is None:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM tasks
            WHERE status IN ({','.join('?' for _ in active_statuses)})
            """,
            active_statuses,
        ).fetchone()
    else:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM tasks
            WHERE project_id = ? AND status IN ({','.join('?' for _ in active_statuses)})
            """,
            (project_id, *active_statuses),
        ).fetchone()
    return int(row["c"]) if row is not None else 0


def next_queued_tasks(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    priority_rank = "CASE t.priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END"
    return conn.execute(
        f"""
        SELECT t.*, p.name AS project_name
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE t.status = 'queued'
        ORDER BY {priority_rank} DESC, t.id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def mark_task_dispatching(
    conn: sqlite3.Connection,
    task_id: int,
    assigned_agent: str,
    branch_name: str,
    worktree_path: str,
    base_sha: str,
    tmux_session: str,
    tmux_target: str,
) -> None:
    conn.execute(
        f"""
        UPDATE tasks
        SET status = 'dispatching',
            assigned_agent = ?,
            branch_name = ?,
            worktree_path = ?,
            base_sha = ?,
            tmux_session = ?,
            tmux_target = ?,
            attempt_count = attempt_count + 1,
            started_at = COALESCE(started_at, ({utc_now()})),
            last_progress_at = ({utc_now()}),
            updated_at = ({utc_now()})
        WHERE id = ?
        """,
        (assigned_agent, branch_name, worktree_path, base_sha, tmux_session, tmux_target, task_id),
    )
    conn.commit()


def set_task_state(
    conn: sqlite3.Connection,
    task_id: int,
    status: str,
    blocked_question: str | None = None,
    last_output_hash: str | None = None,
    loop_count: int | None = None,
    finished: bool = False,
) -> None:
    assigns = ["status = ?", f"updated_at = ({utc_now()})"]
    params: list[object] = [status]
    if blocked_question is not None:
        assigns.append("blocked_question = ?")
        params.append(blocked_question)
    if last_output_hash is not None:
        assigns.append("last_output_hash = ?")
        params.append(last_output_hash)
    if loop_count is not None:
        assigns.append("loop_count = ?")
        params.append(loop_count)
    if status in {"running", "dispatching", "awaiting_input"}:
        assigns.append(f"last_progress_at = ({utc_now()})")
    if finished:
        assigns.append(f"finished_at = ({utc_now()})")
    params.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(assigns)} WHERE id = ?", params)
    conn.commit()


def set_task_resume_ready(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        f"""
        UPDATE tasks
        SET status = 'running',
            blocked_question = NULL,
            last_progress_at = ({utc_now()}),
            updated_at = ({utc_now()})
        WHERE id = ?
        """,
        (task_id,),
    )
    conn.commit()


def touch_task_progress(conn: sqlite3.Connection, task_id: int, last_output_hash: str | None = None) -> None:
    if last_output_hash is None:
        conn.execute(
            f"UPDATE tasks SET last_progress_at = ({utc_now()}), updated_at = ({utc_now()}) WHERE id = ?",
            (task_id,),
        )
    else:
        conn.execute(
            f"""
            UPDATE tasks
            SET last_progress_at = ({utc_now()}),
                last_output_hash = ?,
                updated_at = ({utc_now()})
            WHERE id = ?
            """,
            (last_output_hash, task_id),
        )
    conn.commit()


def add_task_event(conn: sqlite3.Connection, task_id: int, level: str, message: str) -> None:
    conn.execute(
        "INSERT INTO task_events(task_id, level, message) VALUES (?, ?, ?)",
        (task_id, level, message),
    )
    conn.commit()


def task_events(conn: sqlite3.Connection, task_id: int, limit: int = 80) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT created_at, level, message
        FROM task_events
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (task_id, limit),
    ).fetchall()


def create_alert(
    conn: sqlite3.Connection,
    level: str,
    kind: str,
    message: str,
    task_id: int | None = None,
    project_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO alerts(task_id, project_id, level, kind, status, message)
        VALUES (?, ?, ?, ?, 'open', ?)
        """,
        (task_id, project_id, level, kind, message),
    )
    conn.commit()
    return int(cur.lastrowid)


def resolve_alert(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        f"""
        UPDATE alerts
        SET status = 'resolved', updated_at = ({utc_now()}), resolved_at = ({utc_now()})
        WHERE id = ?
        """,
        (alert_id,),
    )
    conn.commit()


def list_alerts(conn: sqlite3.Connection, only_open: bool = True, limit: int = 200) -> list[sqlite3.Row]:
    if only_open:
        return conn.execute(
            """
            SELECT a.*, p.name AS project_name
            FROM alerts a
            LEFT JOIN projects p ON p.id = a.project_id
            WHERE a.status = 'open'
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT a.*, p.name AS project_name
        FROM alerts a
        LEFT JOIN projects p ON p.id = a.project_id
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def create_agent_session(
    conn: sqlite3.Connection,
    task_id: int,
    project_id: int,
    agent: str,
    status: str,
    tmux_session: str,
    tmux_target: str,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO agent_sessions(task_id, project_id, agent, status, tmux_session, tmux_target, last_heartbeat_at, last_progress_at)
        VALUES (?, ?, ?, ?, ?, ?, ({utc_now()}), ({utc_now()}))
        """,
        (task_id, project_id, agent, status, tmux_session, tmux_target),
    )
    conn.commit()
    return int(cur.lastrowid)


def heartbeat_agent_session(conn: sqlite3.Connection, session_id: int, progress: bool = False) -> None:
    if progress:
        conn.execute(
            f"""
            UPDATE agent_sessions
            SET last_heartbeat_at = ({utc_now()}),
                last_progress_at = ({utc_now()})
            WHERE id = ?
            """,
            (session_id,),
        )
    else:
        conn.execute(
            f"UPDATE agent_sessions SET last_heartbeat_at = ({utc_now()}) WHERE id = ?",
            (session_id,),
        )
    conn.commit()


def set_agent_session_status(conn: sqlite3.Connection, session_id: int, status: str, ended: bool = False) -> None:
    if ended:
        conn.execute(
            f"UPDATE agent_sessions SET status = ?, ended_at = ({utc_now()}) WHERE id = ?",
            (status, session_id),
        )
    else:
        conn.execute(
            "UPDATE agent_sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
    conn.commit()


def active_agent_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM agent_sessions
        WHERE status IN ('dispatching', 'running', 'awaiting_input', 'stuck')
        ORDER BY id DESC
        """
    ).fetchall()


def save_operator_reply(conn: sqlite3.Connection, task_id: int, question: str, answer: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO operator_replies(task_id, question, answer)
        VALUES (?, ?, ?)
        """,
        (task_id, question, answer),
    )
    conn.commit()
    return int(cur.lastrowid)


def roadmap_revision_count(conn: sqlite3.Connection, project_id: int, batch_id: int | None = None) -> int:
    if batch_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM roadmap_revisions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM roadmap_revisions WHERE project_id = ? AND batch_id = ?",
            (project_id, batch_id),
        ).fetchone()
    return int(row["c"]) if row is not None else 0


def add_roadmap_revision(
    conn: sqlite3.Connection,
    project_id: int,
    path: str,
    source: str,
    raw_text: str,
    batch_id: int | None = None,
) -> int:
    version = roadmap_revision_count(conn, project_id, batch_id=batch_id) + 1
    cur = conn.execute(
        """
        INSERT INTO roadmap_revisions(project_id, batch_id, path, version, source, raw_text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, batch_id, path, version, source, raw_text),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_phase_checkpoint(
    conn: sqlite3.Connection,
    task_id: int,
    summary: str,
    decisions: str = "",
    next_context: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO phase_checkpoints(task_id, summary, decisions, next_context)
        VALUES (?, ?, ?, ?)
        """,
        (task_id, summary, decisions, next_context),
    )
    conn.commit()
    return int(cur.lastrowid)
