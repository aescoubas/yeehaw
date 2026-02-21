# 02 — SQLite Store

## Design Principles

- **Single-file database** at `.yeehaw/yeehaw.db` (gitignored)
- **stdlib `sqlite3`** — no external ORM, no driver dependency
- **WAL mode** for concurrent reads from CLI while orchestrator writes
- **Single-writer** via connection reuse within the orchestrator process
- **ISO 8601 timestamps** stored as TEXT
- **Schema versioning** via idempotent DDL (`CREATE TABLE IF NOT EXISTS`)

## Schema

### `projects`

```sql
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    repo_root   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

### `roadmaps`

```sql
CREATE TABLE IF NOT EXISTS roadmaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    raw_md      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','approved','executing','completed','invalid')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

### `roadmap_phases`

```sql
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
```

### `tasks`

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    roadmap_id      INTEGER NOT NULL REFERENCES roadmaps(id),
    phase_id        INTEGER NOT NULL REFERENCES roadmap_phases(id),
    task_number     TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','queued','in-progress','done','failed','blocked')),
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
```

### `git_worktrees`

```sql
CREATE TABLE IF NOT EXISTS git_worktrees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    branch      TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','merged','cleaned')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

### `events`

```sql
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    kind        TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

### `alerts`

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    severity    TEXT    NOT NULL CHECK (severity IN ('info','warn','error')),
    message     TEXT    NOT NULL,
    acked       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

### `scheduler_config`

```sql
CREATE TABLE IF NOT EXISTS scheduler_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    max_global_tasks    INTEGER NOT NULL DEFAULT 5,
    max_per_project     INTEGER NOT NULL DEFAULT 3,
    tick_interval_sec   INTEGER NOT NULL DEFAULT 5,
    task_timeout_min    INTEGER NOT NULL DEFAULT 60
);

INSERT OR IGNORE INTO scheduler_config (id) VALUES (1);
```

## Store API

```python
class Store:
    def __init__(self, db_path: Path) -> None: ...
    def close(self) -> None: ...

    # Projects
    def create_project(self, name: str, repo_root: str) -> int: ...
    def get_project(self, name: str) -> dict | None: ...
    def list_projects(self) -> list[dict]: ...
    def delete_project(self, name: str) -> bool: ...

    # Roadmaps
    def create_roadmap(self, project_id: int, raw_md: str) -> int: ...
    def get_roadmap(self, roadmap_id: int) -> dict | None: ...
    def get_active_roadmap(self, project_id: int) -> dict | None: ...
    def update_roadmap_status(self, roadmap_id: int, status: str) -> None: ...

    # Phases
    def create_phase(self, roadmap_id: int, number: int, title: str, verify_cmd: str | None) -> int: ...
    def get_phase(self, phase_id: int) -> dict | None: ...
    def list_phases(self, roadmap_id: int) -> list[dict]: ...
    def list_tasks_by_phase(self, phase_id: int) -> list[dict]: ...
    def update_phase_status(self, phase_id: int, status: str) -> None: ...

    # Tasks
    def create_task(self, roadmap_id: int, phase_id: int, number: str, title: str, description: str) -> int: ...
    def get_task(self, task_id: int) -> dict | None: ...
    def list_tasks(self, project_id: int | None = None, status: str | None = None) -> list[dict]: ...
    def assign_task(self, task_id: int, agent: str, branch: str, worktree: str, signal_dir: str) -> None: ...
    def complete_task(self, task_id: int, status: str) -> None: ...
    def fail_task(self, task_id: int, failure_msg: str) -> None: ...
    def queue_task(self, task_id: int) -> None: ...
    def count_active_tasks(self, project_id: int | None = None) -> int: ...

    # Events
    def log_event(self, kind: str, message: str, project_id: int | None = None, task_id: int | None = None) -> None: ...
    def list_events(self, limit: int = 50) -> list[dict]: ...

    # Alerts
    def create_alert(self, severity: str, message: str, project_id: int | None = None, task_id: int | None = None) -> None: ...
    def list_alerts(self, acked: bool = False) -> list[dict]: ...
    def ack_alert(self, alert_id: int) -> None: ...

    # Scheduler
    def get_scheduler_config(self) -> dict: ...
    def update_scheduler_config(self, **kwargs) -> None: ...
```

## Connection Management

```python
import sqlite3
from pathlib import Path

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```
