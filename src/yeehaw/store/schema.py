"""SQLite schema definition and initialization."""

from __future__ import annotations

from datetime import datetime, timezone
import shutil
import sqlite3
from pathlib import Path

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    repo_root   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roadmaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    raw_md      TEXT    NOT NULL,
    integration_branch TEXT,
    status      TEXT    NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','approved','executing','completed','invalid')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roadmap_phases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    roadmap_id    INTEGER NOT NULL REFERENCES roadmaps(id),
    phase_number  INTEGER NOT NULL,
    title         TEXT    NOT NULL,
    verify_cmd    TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','executing','completed','failed')),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    roadmap_id      INTEGER NOT NULL REFERENCES roadmaps(id),
    phase_id        INTEGER NOT NULL REFERENCES roadmap_phases(id),
    task_number     TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','queued','paused','in-progress','done','failed','blocked')),
    assigned_agent  TEXT,
    branch_name     TEXT,
    worktree_path   TEXT,
    signal_dir      TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 4,
    last_failure    TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    blocked_task_id INTEGER NOT NULL REFERENCES tasks(id),
    blocker_task_id INTEGER NOT NULL REFERENCES tasks(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (blocked_task_id, blocker_task_id)
);

CREATE TABLE IF NOT EXISTS git_worktrees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    branch      TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','merged','cleaned')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    kind        TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    severity    TEXT    NOT NULL CHECK (severity IN ('info','warn','error')),
    message     TEXT    NOT NULL,
    acked       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hook_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER,
    roadmap_id  INTEGER,
    phase_id    INTEGER,
    task_id     INTEGER,
    event_name  TEXT    NOT NULL,
    event_id    TEXT    NOT NULL,
    hook_name   TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    summary     TEXT,
    error       TEXT,
    returncode  INTEGER,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hook_runs_event ON hook_runs(event_name, id DESC);
CREATE INDEX IF NOT EXISTS idx_hook_runs_task ON hook_runs(task_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hook_runs_event_hook ON hook_runs(event_id, hook_name);

CREATE TABLE IF NOT EXISTS scheduler_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    max_global_tasks    INTEGER NOT NULL DEFAULT 5,
    max_per_project     INTEGER NOT NULL DEFAULT 5,
    tick_interval_sec   INTEGER NOT NULL DEFAULT 5,
    task_timeout_min    INTEGER NOT NULL DEFAULT 60
);

INSERT OR IGNORE INTO scheduler_config (id) VALUES (1);
"""


LEGACY_TABLES = (
    "projects",
    "roadmaps",
    "roadmap_phases",
    "tasks",
    "git_worktrees",
    "events",
    "alerts",
    "scheduler_config",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _tasks_support_paused_status(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'",
    ).fetchone()
    if row is None:
        return True
    sql = str(row[0] or "")
    return "'paused'" in sql


def _roadmaps_support_integration_branch(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "roadmaps"):
        return False
    return "integration_branch" in _column_names(conn, "roadmaps")


def _migrate_roadmaps_add_integration_branch(conn: sqlite3.Connection) -> None:
    """Add integration_branch column to roadmaps when missing."""
    if not _table_exists(conn, "roadmaps"):
        return
    if _roadmaps_support_integration_branch(conn):
        return
    conn.execute("ALTER TABLE roadmaps ADD COLUMN integration_branch TEXT")
    conn.commit()


def _migrate_tasks_add_paused_status(conn: sqlite3.Connection) -> None:
    """Rebuild tasks table to include paused status in CHECK constraint."""
    if not _table_exists(conn, "tasks"):
        return
    if _tasks_support_paused_status(conn):
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("ALTER TABLE tasks RENAME TO tasks__prepaused")
        conn.execute(
            """
            CREATE TABLE tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                roadmap_id      INTEGER NOT NULL REFERENCES roadmaps(id),
                phase_id        INTEGER NOT NULL REFERENCES roadmap_phases(id),
                task_number     TEXT    NOT NULL,
                title           TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','queued','paused','in-progress','done','failed','blocked')),
                assigned_agent  TEXT,
                branch_name     TEXT,
                worktree_path   TEXT,
                signal_dir      TEXT,
                attempts        INTEGER NOT NULL DEFAULT 0,
                max_attempts    INTEGER NOT NULL DEFAULT 4,
                last_failure    TEXT,
                started_at      TEXT,
                completed_at    TEXT,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            INSERT INTO tasks (
                id, roadmap_id, phase_id, task_number, title, description, status,
                assigned_agent, branch_name, worktree_path, signal_dir,
                attempts, max_attempts, last_failure, started_at, completed_at, created_at, updated_at
            )
            SELECT id,
                   roadmap_id,
                   phase_id,
                   task_number,
                   title,
                   description,
                   CASE
                     WHEN status IN ('pending','queued','paused','in-progress','done','failed','blocked')
                     THEN status
                     ELSE 'pending'
                   END,
                   assigned_agent,
                   branch_name,
                   worktree_path,
                   signal_dir,
                   attempts,
                   max_attempts,
                   last_failure,
                   started_at,
                   completed_at,
                   created_at,
                   updated_at
            FROM tasks__prepaused
            """
        )
        conn.execute("DROP TABLE tasks__prepaused")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_scheduler_default_max_per_project(conn: sqlite3.Connection) -> None:
    """Bump untouched legacy scheduler default from 3 to 5 per-project tasks."""
    if not _table_exists(conn, "scheduler_config"):
        return
    row = conn.execute(
        "SELECT max_global_tasks, max_per_project, tick_interval_sec, task_timeout_min "
        "FROM scheduler_config WHERE id = 1",
    ).fetchone()
    if row is None:
        return

    max_global, max_per_project, tick_interval_sec, task_timeout_min = (
        int(row[0]),
        int(row[1]),
        int(row[2]),
        int(row[3]),
    )
    if (
        max_global == 5
        and max_per_project == 3
        and tick_interval_sec == 5
        and task_timeout_min == 60
    ):
        conn.execute(
            "UPDATE scheduler_config SET max_per_project = 5 WHERE id = 1",
        )
        conn.commit()


def _is_legacy_schema(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "projects"):
        return False
    project_cols = _column_names(conn, "projects")
    return "root_path" in project_cols and "repo_root" not in project_cols


def _backup_db_file(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.legacy-backup-{timestamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _migrate_legacy_schema(conn: sqlite3.Connection, db_path: Path) -> None:
    _backup_db_file(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in LEGACY_TABLES:
            if _table_exists(conn, table):
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}__legacy")

        conn.executescript(SCHEMA_DDL)

        if _table_exists(conn, "projects__legacy"):
            conn.execute(
                """
                INSERT INTO projects (id, name, repo_root, created_at, updated_at)
                SELECT id,
                       name,
                       root_path,
                       created_at,
                       updated_at
                FROM projects__legacy
                """
            )

        if _table_exists(conn, "roadmaps__legacy"):
            conn.execute(
                """
                INSERT INTO roadmaps (
                    id, project_id, raw_md, integration_branch, status, created_at, updated_at
                )
                SELECT id,
                       project_id,
                       raw_text,
                       NULL,
                       CASE
                         WHEN status IN ('draft','approved','executing','completed','invalid')
                         THEN status
                         ELSE 'draft'
                       END,
                       created_at,
                       updated_at
                FROM roadmaps__legacy
                """
            )

        if _table_exists(conn, "roadmap_phases__legacy"):
            conn.execute(
                """
                INSERT INTO roadmap_phases (id, roadmap_id, phase_number, title, verify_cmd, status, created_at)
                SELECT id,
                       roadmap_id,
                       number,
                       title,
                       NULLIF(verification_text, ''),
                       CASE
                         WHEN status = 'running' THEN 'executing'
                         WHEN status = 'passed' THEN 'completed'
                         WHEN status = 'failed' THEN 'failed'
                         ELSE 'pending'
                       END,
                       created_at
                FROM roadmap_phases__legacy
                """
            )

        if _table_exists(conn, "tasks__legacy"):
            conn.execute(
                """
                INSERT INTO tasks (
                    id, roadmap_id, phase_id, task_number, title, description, status,
                    assigned_agent, branch_name, worktree_path, signal_dir,
                    attempts, max_attempts, started_at, completed_at, created_at, updated_at
                )
                SELECT t.id,
                       p.roadmap_id,
                       t.phase_id,
                       t.number,
                       t.title,
                       t.description,
                       CASE
                         WHEN t.status = 'queued' THEN 'queued'
                         WHEN t.status IN ('dispatched', 'running') THEN 'in-progress'
                         WHEN t.status = 'done' THEN 'done'
                         WHEN t.status IN ('failed', 'timeout') THEN 'failed'
                         WHEN t.status = 'skipped' THEN 'blocked'
                         ELSE 'pending'
                       END,
                       NULLIF(t.agent, ''),
                       NULLIF(t.branch, ''),
                       NULLIF(t.worktree_path, ''),
                       NULLIF(t.signal_dir, ''),
                       COALESCE(t.attempt_count, 0),
                       COALESCE(t.max_attempts, 4),
                       t.started_at,
                       t.finished_at,
                       t.created_at,
                       t.updated_at
                FROM tasks__legacy t
                JOIN roadmap_phases__legacy p ON p.id = t.phase_id
                """
            )

        if _table_exists(conn, "git_worktrees__legacy"):
            conn.execute(
                """
                INSERT INTO git_worktrees (id, task_id, branch, path, status, created_at)
                SELECT id,
                       task_id,
                       branch,
                       path,
                       CASE
                         WHEN state = 'merged' THEN 'merged'
                         WHEN state = 'removed' THEN 'cleaned'
                         ELSE 'active'
                       END,
                       created_at
                FROM git_worktrees__legacy
                """
            )

        if _table_exists(conn, "events__legacy"):
            conn.execute(
                """
                INSERT INTO events (id, project_id, task_id, kind, message, created_at)
                SELECT id, project_id, task_id, kind, message, created_at
                FROM events__legacy
                """
            )

        if _table_exists(conn, "alerts__legacy"):
            conn.execute(
                """
                INSERT INTO alerts (id, project_id, task_id, severity, message, acked, created_at)
                SELECT id,
                       project_id,
                       task_id,
                       severity,
                       message,
                       CASE WHEN status = 'resolved' THEN 1 ELSE 0 END,
                       created_at
                FROM alerts__legacy
                """
            )

        if _table_exists(conn, "scheduler_config__legacy"):
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduler_config
                    (id, max_global_tasks, max_per_project, tick_interval_sec, task_timeout_min)
                SELECT id,
                       max_global,
                       max_per_project,
                       5,
                       timeout_minutes
                FROM scheduler_config__legacy
                WHERE id = 1
                """
            )

        for table in LEGACY_TABLES:
            legacy_table = f"{table}__legacy"
            if _table_exists(conn, legacy_table):
                conn.execute(f"DROP TABLE {legacy_table}")

        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema and return connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    if _is_legacy_schema(conn):
        _migrate_legacy_schema(conn, db_path)
    conn.executescript(SCHEMA_DDL)
    _migrate_roadmaps_add_integration_branch(conn)
    _migrate_tasks_add_paused_status(conn)
    _migrate_scheduler_default_max_per_project(conn)
    return conn
