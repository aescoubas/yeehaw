from __future__ import annotations

import sqlite3
from pathlib import Path


def utc_now() -> str:
    return "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL,
    guidelines TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ({utc_now()}),
    updated_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS roadmaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT ({utc_now()}),
    updated_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS roadmap_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    source TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS task_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    roadmap_id INTEGER REFERENCES roadmaps(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES task_batches(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 50,
    runtime_kind TEXT NOT NULL DEFAULT 'tmux',
    preferred_agent TEXT,
    assigned_agent TEXT,
    branch_name TEXT,
    worktree_path TEXT,
    blocked_question TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ({utc_now()}),
    updated_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    runtime_kind TEXT NOT NULL,
    session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'starting',
    started_at TEXT NOT NULL DEFAULT ({utc_now()}),
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    runtime_kind TEXT NOT NULL,
    transport_session_id TEXT NOT NULL,
    transport_target TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting',
    started_at TEXT NOT NULL DEFAULT ({utc_now()}),
    last_heartbeat_at TEXT,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS operator_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK(direction IN ('to_agent', 'from_agent')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS session_watch_state (
    session_id INTEGER PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
    last_fingerprint TEXT NOT NULL DEFAULT '',
    repeat_count INTEGER NOT NULL DEFAULT 0,
    last_output_at TEXT,
    last_meaningful_at TEXT,
    updated_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS dispatcher_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER REFERENCES task_batches(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    proposal_json TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    confidence REAL,
    applied INTEGER NOT NULL DEFAULT 0,
    overridden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS usage_ingestion_state (
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT ({utc_now()}),
    PRIMARY KEY (session_id, provider, model)
);

CREATE TABLE IF NOT EXISTS scheduler_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    max_global_sessions INTEGER NOT NULL DEFAULT 20,
    max_project_sessions INTEGER NOT NULL DEFAULT 10,
    stuck_minutes INTEGER NOT NULL DEFAULT 12,
    preemption_enabled INTEGER NOT NULL DEFAULT 1,
    auto_reassign INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT ({utc_now()}),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    decisions TEXT NOT NULL DEFAULT '',
    next_context TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ({utc_now()})
);

CREATE TABLE IF NOT EXISTS git_worktrees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT ({utc_now()}),
    cleaned_at TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    resolved = Path(db_path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        f"""
        INSERT INTO scheduler_config(id, max_global_sessions, max_project_sessions, stuck_minutes, preemption_enabled, auto_reassign, updated_at)
        VALUES (1, 20, 10, 12, 1, 1, ({utc_now()}))
        ON CONFLICT(id) DO NOTHING
        """
    )
    conn.commit()
