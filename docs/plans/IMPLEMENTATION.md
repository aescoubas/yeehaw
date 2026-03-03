# IMPLEMENTATION.md — Yeehaw Python Implementation Guide

> This document is the complete specification for implementing the **yeehaw**
> multi-agent coding orchestrator CLI in Python. It is designed to be
> self-contained — an AI coding agent (Codex, GPT-5.3, Claude, Gemini) should
> be able to implement the entire project from this document alone.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Decisions](#2-technology-decisions)
3. [Project Setup](#3-project-setup)
4. [Package Structure](#4-package-structure)
5. [Module Specifications](#5-module-specifications)
   - 5.1 [store/schema.py](#51-storeschemapy)
   - 5.2 [store/store.py](#52-storestorepy)
   - 5.3 [roadmap/parser.py](#53-roadmapparserpy)
   - 5.4 [signal/protocol.py](#54-signalprotocolpy)
   - 5.5 [git/worktree.py](#55-gitworktreepy)
   - 5.6 [tmux/session.py](#56-tmuxsessionpy)
   - 5.7 [agent/profiles.py](#57-agentprofilespy)
   - 5.8 [agent/launcher.py](#58-agentlauncherpy)
   - 5.9 [orchestrator/engine.py](#59-orchestratorenginepy)
   - 5.10 [mcp/server.py](#510-mcpserverpy)
   - 5.11 [planner/session.py](#511-plannersessionpy)
   - 5.12 [cli/main.py](#512-climainpy)
   - 5.13 [cli/ subcommands](#513-cli-subcommands)
6. [CLI Command Reference](#6-cli-command-reference)
7. [Testing Specification](#7-testing-specification)
8. [Implementation Order](#8-implementation-order)
9. [Edge Cases & Error Handling](#9-edge-cases--error-handling)
10. [Acceptance Criteria](#10-acceptance-criteria)

---

## 1. Project Overview

Yeehaw is a **Planner-Worker multi-agent swarm** CLI tool that:

1. Accepts a human "brain dump" (freeform text about strategic topics)
2. Uses an AI **Planner agent** (connected via MCP) to translate it into
   structured projects, roadmaps with phases, and individual tasks
3. Stores everything in a **SQLite database**
4. Dispatches tasks to **Worker agents** (Claude Code, Gemini CLI, Codex)
   running in isolated **git worktrees** inside **tmux sessions**
5. Monitors task completion via a **file-based signal protocol** (watchdog)
6. Provides a lightweight CLI for status, attachment, and control

The key insight: **no TUI**. Instead of a fragile terminal UI, we use tmux
as the multiplexer and the CLI prints static tables for status.

---

## 2. Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.12+ | Modern typing, f-string nesting, stdlib richness |
| CLI | `argparse` (stdlib) | Zero deps, full control, subcommand support |
| Database | `sqlite3` (stdlib) | Zero deps, single-file, WAL for concurrent reads |
| MCP Server | `fastmcp` | Pythonic MCP server with decorator API |
| FS Monitoring | `watchdog` | Cross-platform filesystem event monitoring |
| Multiplexer | `tmux` | Battle-tested, universally available |
| Packaging | `uv` | Fast, modern, lockfile + pyproject.toml |
| Testing | `pytest` + `pytest-cov` | Standard, fixtures, 80%+ coverage target |
| Signal Protocol | `.yeehaw/` directory | Compatible with existing conventions |

### External Dependencies (minimal)

```
fastmcp>=2.0
watchdog>=4.0
```

### Dev Dependencies

```
pytest>=8.0
pytest-cov>=5.0
```

---

## 3. Project Setup

### 3.1 Directory Structure

Create these files and directories in the repository root:

```
pyproject.toml
src/yeehaw/__init__.py
src/yeehaw/__main__.py
src/yeehaw/cli/__init__.py
src/yeehaw/cli/main.py
src/yeehaw/cli/project.py
src/yeehaw/cli/roadmap.py
src/yeehaw/cli/run.py
src/yeehaw/cli/status.py
src/yeehaw/cli/scheduler.py
src/yeehaw/cli/plan.py
src/yeehaw/cli/attach.py
src/yeehaw/cli/stop.py
src/yeehaw/store/__init__.py
src/yeehaw/store/schema.py
src/yeehaw/store/store.py
src/yeehaw/roadmap/__init__.py
src/yeehaw/roadmap/parser.py
src/yeehaw/signal/__init__.py
src/yeehaw/signal/protocol.py
src/yeehaw/git/__init__.py
src/yeehaw/git/worktree.py
src/yeehaw/tmux/__init__.py
src/yeehaw/tmux/session.py
src/yeehaw/agent/__init__.py
src/yeehaw/agent/profiles.py
src/yeehaw/agent/launcher.py
src/yeehaw/orchestrator/__init__.py
src/yeehaw/orchestrator/engine.py
src/yeehaw/mcp/__init__.py
src/yeehaw/mcp/server.py
src/yeehaw/planner/__init__.py
src/yeehaw/planner/session.py
tests/conftest.py
tests/test_store.py
tests/test_roadmap.py
tests/test_signal.py
tests/test_git.py
tests/test_agent.py
tests/test_orchestrator.py
tests/test_mcp.py
tests/test_cli.py
```

### 3.2 pyproject.toml

```toml
[project]
name = "yeehaw"
version = "0.1.0"
description = "Multi-agent coding orchestrator CLI"
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=2.0",
    "watchdog>=4.0",
]

[project.scripts]
yeehaw = "yeehaw.cli.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/yeehaw"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: tests requiring git or tmux (deselect with '-m not integration')",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]
```

### 3.3 `__init__.py`

```python
# src/yeehaw/__init__.py
"""Yeehaw — Multi-agent coding orchestrator."""
```

### 3.4 `__main__.py`

```python
# src/yeehaw/__main__.py
"""Allow running as: python -m yeehaw"""
from yeehaw.cli.main import main
main()
```

---

## 4. Package Structure

```
src/yeehaw/
├── __init__.py              # Package marker
├── __main__.py              # python -m yeehaw entry point
├── cli/
│   ├── __init__.py
│   ├── main.py              # Root argparse parser, subcommand routing
│   ├── project.py           # yeehaw project {add,list,remove}
│   ├── roadmap.py           # yeehaw roadmap {create,show,approve,edit}
│   ├── run.py               # yeehaw run [--project]
│   ├── status.py            # yeehaw status [--project] [--json]
│   ├── scheduler.py         # yeehaw scheduler {config,show}
│   ├── plan.py              # yeehaw plan <briefing-file> [--agent claude|gemini]
│   ├── attach.py            # yeehaw attach <task-id>
│   └── stop.py              # yeehaw stop {<task-id>|--all}
├── store/
│   ├── __init__.py
│   ├── schema.py            # DDL strings, init_db() function
│   └── store.py             # Store class wrapping sqlite3
├── roadmap/
│   ├── __init__.py
│   └── parser.py            # parse_roadmap(), validate_roadmap()
├── signal/
│   ├── __init__.py
│   └── protocol.py          # SignalWatcher, read_signal()
├── git/
│   ├── __init__.py
│   └── worktree.py          # branch_name(), prepare_worktree(), cleanup_worktree()
├── tmux/
│   ├── __init__.py
│   └── session.py           # ensure_session(), send_text(), has_session(), etc.
├── agent/
│   ├── __init__.py
│   ├── profiles.py          # AgentProfile dataclass, AGENT_REGISTRY
│   └── launcher.py          # build_task_prompt(), build_launch_command(), write_launcher()
├── orchestrator/
│   ├── __init__.py
│   └── engine.py            # Orchestrator class with tick loop
├── mcp/
│   ├── __init__.py
│   └── server.py            # FastMCP server with tools
└── planner/
    ├── __init__.py
    └── session.py           # start_planner_session()
```

---

## 5. Module Specifications

### 5.1 `store/schema.py`

This module defines the database schema as SQL strings and provides an
initialization function.

```python
"""SQLite schema definition and initialization."""
from pathlib import Path
import sqlite3

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

CREATE TABLE IF NOT EXISTS scheduler_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    max_global_tasks    INTEGER NOT NULL DEFAULT 5,
    max_per_project     INTEGER NOT NULL DEFAULT 3,
    tick_interval_sec   INTEGER NOT NULL DEFAULT 5,
    task_timeout_min    INTEGER NOT NULL DEFAULT 60
);

INSERT OR IGNORE INTO scheduler_config (id) VALUES (1);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema. Returns connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_DDL)
    return conn
```

---

### 5.2 `store/store.py`

The `Store` class wraps all database operations. Every method that modifies
data commits immediately. Read methods return `dict` (from `sqlite3.Row`).

**Required methods** (implement ALL of these):

```python
"""SQLite store for yeehaw state management."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from yeehaw.store.schema import init_db


class Store:
    """Single-writer SQLite store."""

    def __init__(self, db_path: Path) -> None:
        self._conn = init_db(db_path)

    def close(self) -> None:
        self._conn.close()

    # --- Helper ---

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Projects ---

    def create_project(self, name: str, repo_root: str) -> int:
        """Insert a project. Returns its ID."""
        cur = self._conn.execute(
            "INSERT INTO projects (name, repo_root) VALUES (?, ?)",
            (name, repo_root),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_project(self, name: str) -> dict | None:
        """Get project by name."""
        row = self._conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_projects(self) -> list[dict]:
        """List all projects."""
        rows = self._conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_project(self, name: str) -> bool:
        """Delete a project by name. Returns True if deleted."""
        cur = self._conn.execute("DELETE FROM projects WHERE name = ?", (name,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- Roadmaps ---

    def create_roadmap(self, project_id: int, raw_md: str) -> int:
        """Insert a roadmap. Returns its ID."""
        cur = self._conn.execute(
            "INSERT INTO roadmaps (project_id, raw_md) VALUES (?, ?)",
            (project_id, raw_md),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_roadmap(self, roadmap_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM roadmaps WHERE id = ?", (roadmap_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_active_roadmap(self, project_id: int) -> dict | None:
        """Get the most recent non-invalid roadmap for a project."""
        row = self._conn.execute(
            "SELECT * FROM roadmaps WHERE project_id = ? AND status != 'invalid' "
            "ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def update_roadmap_status(self, roadmap_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE roadmaps SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), roadmap_id),
        )
        self._conn.commit()

    # --- Phases ---

    def create_phase(self, roadmap_id: int, number: int, title: str,
                     verify_cmd: str | None) -> int:
        cur = self._conn.execute(
            "INSERT INTO roadmap_phases (roadmap_id, phase_number, title, verify_cmd) "
            "VALUES (?, ?, ?, ?)",
            (roadmap_id, number, title, verify_cmd),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_phase(self, phase_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM roadmap_phases WHERE id = ?", (phase_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_phases(self, roadmap_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM roadmap_phases WHERE roadmap_id = ? ORDER BY phase_number",
            (roadmap_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_tasks_by_phase(self, phase_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE phase_id = ? ORDER BY task_number",
            (phase_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_phase_status(self, phase_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE roadmap_phases SET status = ? WHERE id = ?",
            (status, phase_id),
        )
        self._conn.commit()

    # --- Tasks ---

    def create_task(self, roadmap_id: int, phase_id: int, number: str,
                    title: str, description: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO tasks (roadmap_id, phase_id, task_number, title, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (roadmap_id, phase_id, number, title, description),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_task(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT t.*, p.name as project_name, p.id as project_id "
            "FROM tasks t "
            "JOIN roadmaps r ON t.roadmap_id = r.id "
            "JOIN projects p ON r.project_id = p.id "
            "WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_tasks(self, project_id: int | None = None,
                   status: str | None = None) -> list[dict]:
        query = (
            "SELECT t.*, p.name as project_name, p.id as project_id "
            "FROM tasks t "
            "JOIN roadmaps r ON t.roadmap_id = r.id "
            "JOIN projects p ON r.project_id = p.id "
            "WHERE 1=1"
        )
        params: list = []
        if project_id is not None:
            query += " AND p.id = ?"
            params.append(project_id)
        if status is not None:
            query += " AND t.status = ?"
            params.append(status)
        query += " ORDER BY t.task_number"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def assign_task(self, task_id: int, agent: str, branch: str,
                    worktree: str, signal_dir: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = 'in-progress', assigned_agent = ?, "
            "branch_name = ?, worktree_path = ?, signal_dir = ?, "
            "attempts = attempts + 1, started_at = ?, updated_at = ? WHERE id = ?",
            (agent, branch, worktree, signal_dir, self._now(), self._now(), task_id),
        )
        self._conn.commit()

    def complete_task(self, task_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), self._now(), task_id),
        )
        self._conn.commit()

    def fail_task(self, task_id: int, failure_msg: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = 'failed', last_failure = ?, updated_at = ? WHERE id = ?",
            (failure_msg, self._now(), task_id),
        )
        self._conn.commit()

    def queue_task(self, task_id: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self._conn.commit()

    def count_active_tasks(self, project_id: int | None = None) -> int:
        if project_id is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks t JOIN roadmaps r ON t.roadmap_id = r.id "
                "WHERE r.project_id = ? AND t.status = 'in-progress'",
                (project_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'in-progress'"
            ).fetchone()
        return row[0]

    # --- Events ---

    def log_event(self, kind: str, message: str, project_id: int | None = None,
                  task_id: int | None = None) -> None:
        self._conn.execute(
            "INSERT INTO events (project_id, task_id, kind, message) VALUES (?, ?, ?, ?)",
            (project_id, task_id, kind, message),
        )
        self._conn.commit()

    def list_events(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Alerts ---

    def create_alert(self, severity: str, message: str,
                     project_id: int | None = None,
                     task_id: int | None = None) -> None:
        self._conn.execute(
            "INSERT INTO alerts (project_id, task_id, severity, message) VALUES (?, ?, ?, ?)",
            (project_id, task_id, severity, message),
        )
        self._conn.commit()

    def list_alerts(self, acked: bool = False) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM alerts WHERE acked = ? ORDER BY id DESC",
            (int(acked),),
        ).fetchall()
        return [dict(r) for r in rows]

    def ack_alert(self, alert_id: int) -> None:
        self._conn.execute(
            "UPDATE alerts SET acked = 1 WHERE id = ?", (alert_id,)
        )
        self._conn.commit()

    # --- Scheduler Config ---

    def get_scheduler_config(self) -> dict:
        row = self._conn.execute(
            "SELECT * FROM scheduler_config WHERE id = 1"
        ).fetchone()
        return dict(row)

    def update_scheduler_config(self, **kwargs) -> None:
        valid_keys = {"max_global_tasks", "max_per_project",
                      "tick_interval_sec", "task_timeout_min"}
        for key in kwargs:
            if key not in valid_keys:
                raise ValueError(f"Invalid config key: {key}")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values())
        self._conn.execute(
            f"UPDATE scheduler_config SET {sets} WHERE id = 1", vals
        )
        self._conn.commit()
```

---

### 5.3 `roadmap/parser.py`

Single-pass line-by-line state machine that parses structured Markdown into
dataclasses.

```python
"""Roadmap Markdown parser and validator."""
from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class Task:
    number: str        # "1.1", "2.3"
    title: str
    description: str


@dataclass
class Phase:
    number: int
    title: str
    verify_cmd: str | None
    tasks: list[Task] = field(default_factory=list)


@dataclass
class Roadmap:
    project_name: str
    phases: list[Phase] = field(default_factory=list)


# Regex patterns
_RE_HEADER = re.compile(r"^#\s+Roadmap:\s+(.+)$")
_RE_PHASE = re.compile(r"^##\s+Phase\s+(\d+):\s+(.+)$")
_RE_VERIFY = re.compile(r"^\*\*Verify:\*\*\s+`(.+)`$")
_RE_TASK = re.compile(r"^###\s+Task\s+([\d.]+):\s+(.+)$")


def parse_roadmap(text: str) -> Roadmap:
    """Parse roadmap markdown into structured data.

    Raises ValueError if the header is missing.
    """
    lines = text.strip().splitlines()
    roadmap = None
    current_phase = None
    current_task = None
    task_lines: list[str] = []

    def _flush_task():
        nonlocal current_task, task_lines
        if current_task is not None:
            current_task.description = "\n".join(task_lines).strip()
            task_lines = []

    for line in lines:
        # H1: Roadmap header
        m = _RE_HEADER.match(line)
        if m:
            roadmap = Roadmap(project_name=m.group(1).strip())
            continue

        # H2: Phase
        m = _RE_PHASE.match(line)
        if m:
            _flush_task()
            current_task = None
            current_phase = Phase(
                number=int(m.group(1)),
                title=m.group(2).strip(),
                verify_cmd=None,
            )
            if roadmap is not None:
                roadmap.phases.append(current_phase)
            continue

        # Verify line (must follow a phase header)
        m = _RE_VERIFY.match(line)
        if m and current_phase is not None and not current_phase.tasks:
            current_phase.verify_cmd = m.group(1)
            continue

        # H3: Task
        m = _RE_TASK.match(line)
        if m:
            _flush_task()
            current_task = Task(
                number=m.group(1),
                title=m.group(2).strip(),
                description="",
            )
            task_lines = []
            if current_phase is not None:
                current_phase.tasks.append(current_task)
            continue

        # Body text
        if current_task is not None:
            task_lines.append(line)

    _flush_task()

    if roadmap is None:
        raise ValueError("Missing roadmap header: '# Roadmap: <name>'")

    return roadmap


def validate_roadmap(roadmap: Roadmap) -> list[str]:
    """Validate roadmap structure. Returns list of error messages (empty = valid)."""
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
```

---

### 5.4 `signal/protocol.py`

Filesystem-based signal detection using `watchdog`.

```python
"""Sentinel file protocol for agent completion signaling."""
from __future__ import annotations
import json
import time
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent


def read_signal(signal_path: Path, retries: int = 3) -> dict | None:
    """Read and parse a signal.json file with retries for partial writes."""
    for attempt in range(retries):
        try:
            text = signal_path.read_text()
            data = json.loads(text)
            if "task_id" in data and "status" in data:
                return data
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        if attempt < retries - 1:
            time.sleep(0.2)
    return None


class SignalHandler(FileSystemEventHandler):
    """Watches for signal.json file creation/modification with debounce."""

    def __init__(self, debounce_sec: float = 0.5) -> None:
        self.debounce_sec = debounce_sec
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith("signal.json"):
            with self._lock:
                self._pending[event.src_path] = time.monotonic()

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith("signal.json"):
            with self._lock:
                self._pending[event.src_path] = time.monotonic()

    def get_ready_signals(self) -> list[Path]:
        """Return signal paths that have passed the debounce window."""
        now = time.monotonic()
        ready: list[Path] = []
        with self._lock:
            expired = [
                path for path, ts in self._pending.items()
                if now - ts >= self.debounce_sec
            ]
            for path in expired:
                del self._pending[path]
                ready.append(Path(path))
        return ready


class SignalWatcher:
    """Manages watchdog observer for signal directories."""

    def __init__(self, signals_root: Path) -> None:
        self.signals_root = signals_root
        self.handler = SignalHandler()
        self._observer: Observer | None = None

    def start(self) -> None:
        self.signals_root.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(self.handler, str(self.signals_root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def get_ready_signals(self) -> list[Path]:
        return self.handler.get_ready_signals()

    def poll_signals(self) -> list[Path]:
        """Fallback: scan for signal files on disk."""
        found: list[Path] = []
        if self.signals_root.exists():
            for signal_file in self.signals_root.rglob("signal.json"):
                found.append(signal_file)
        return found
```

---

### 5.5 `git/worktree.py`

Git worktree management via subprocess.

```python
"""Git worktree management for task isolation."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path


def branch_name(task_number: str, title: str) -> str:
    """Generate a sanitized git branch name for a task."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:50]
    return f"yeehaw/task-{task_number}-{slug}"


def prepare_worktree(repo_root: Path, branch: str) -> Path:
    """Create a git worktree for the given branch. Returns worktree path."""
    dir_name = branch.split("/")[-1]
    worktree_path = repo_root / ".yeehaw" / "worktrees" / dir_name

    # Clean stale worktree if it exists
    if worktree_path.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_root, capture_output=True,
        )

    # Create or force-update branch from HEAD
    subprocess.run(
        ["git", "branch", "-f", branch, "HEAD"],
        cwd=repo_root, check=True, capture_output=True,
    )

    # Create worktree
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=repo_root, check=True, capture_output=True,
    )

    return worktree_path


def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove a worktree and prune stale entries."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_root, capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root, capture_output=True,
    )
```

---

### 5.6 `tmux/session.py`

Tmux session management via subprocess.

```python
"""Tmux session management for worker agent isolation."""
from __future__ import annotations
import os
import subprocess


def ensure_session(session_name: str, working_dir: str) -> None:
    """Create a detached tmux session."""
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", working_dir],
        check=True, capture_output=True,
    )


def send_text(session_name: str, text: str) -> None:
    """Send text to a tmux session and press Enter."""
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, text, "Enter"],
        check=True, capture_output=True,
    )


def has_session(session_name: str) -> bool:
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def capture_pane(session_name: str) -> str:
    """Capture the full scrollback of a tmux pane."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"],
        capture_output=True, text=True,
    )
    return result.stdout


def kill_session(session_name: str) -> None:
    """Kill a tmux session (ignores errors if already dead)."""
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
    )


def attach_session(session_name: str) -> None:
    """Attach to a tmux session (replaces current process)."""
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def launch_agent(session_name: str, working_dir: str, command: str) -> None:
    """Create a tmux session and send the agent launch command."""
    ensure_session(session_name, working_dir)
    send_text(session_name, command)
```

---

### 5.7 `agent/profiles.py`

```python
"""Agent profile definitions and registry."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    name: str
    command: str
    prompt_flag: str
    timeout_minutes: int = 60


AGENT_REGISTRY: dict[str, AgentProfile] = {
    "claude": AgentProfile(
        name="claude",
        command="claude",
        prompt_flag="--dangerously-skip-permissions -p",
    ),
    "gemini": AgentProfile(
        name="gemini",
        command="gemini",
        prompt_flag="-p",
    ),
    "codex": AgentProfile(
        name="codex",
        command="codex",
        prompt_flag="--prompt",
    ),
}

DEFAULT_AGENT = "claude"


def resolve_profile(agent_name: str | None = None) -> AgentProfile:
    """Resolve an agent profile by name. Falls back to default."""
    name = agent_name or DEFAULT_AGENT
    if name not in AGENT_REGISTRY:
        raise ValueError(
            f"Unknown agent '{name}'. Available: {', '.join(AGENT_REGISTRY)}"
        )
    return AGENT_REGISTRY[name]
```

---

### 5.8 `agent/launcher.py`

```python
"""Task prompt construction and agent launch command building."""
from __future__ import annotations
import shlex
from pathlib import Path
from yeehaw.agent.profiles import AgentProfile


def build_task_prompt(
    task: dict,
    signal_dir: str,
    previous_failure: str | None = None,
) -> str:
    """Build the full prompt that will be sent to the worker agent."""
    parts = [
        f"# Task {task['task_number']}: {task['title']}",
        "",
        task["description"],
        "",
        "## Signal Protocol",
        "",
        f"When you are finished, create the file `{signal_dir}/signal.json` with this format:",
        "",
        "```json",
        "{",
        f'  "task_id": {task["id"]},',
        '  "status": "done",',
        '  "summary": "Brief description of what you did",',
        '  "artifacts": ["list", "of", "key", "files", "changed"],',
        f'  "timestamp": "{_iso_now_placeholder()}"',
        "}",
        "```",
        "",
        'Set `status` to `"done"` on success, `"failed"` if you cannot complete the task,',
        'or `"blocked"` if you need external input.',
        "",
        "**This signal file is mandatory.** Without it, your task will time out.",
    ]

    if previous_failure:
        parts.extend([
            "",
            "## Previous Attempt Failed",
            "",
            f"The previous attempt failed with: {previous_failure}",
            "",
            "Please fix the issues and try again.",
        ])

    return "\n".join(parts)


def build_launch_command(profile: AgentProfile, prompt: str) -> str:
    """Build the shell command to launch an agent with the given prompt."""
    return f"{profile.command} {profile.prompt_flag} {shlex.quote(prompt)}"


def write_launcher(script_path: Path, profile: AgentProfile, prompt: str) -> None:
    """Write a launcher shell script for long prompts that exceed arg limits."""
    script_path.write_text(
        f"#!/bin/bash\n"
        f"exec {profile.command} {profile.prompt_flag} "
        f"\"$(cat <<'YEEHAW_PROMPT_EOF'\n{prompt}\nYEEHAW_PROMPT_EOF\n)\"\n"
    )
    script_path.chmod(0o755)


def _iso_now_placeholder() -> str:
    """Return a placeholder ISO timestamp for the prompt template."""
    return "ISO-8601-timestamp-here"
```

---

### 5.9 `orchestrator/engine.py`

The core dispatch/monitor loop. This is the most complex module.

**Key behaviors:**
- Single-threaded tick loop (default 5s)
- Each tick: monitor active → dispatch queued
- Signal detection via watchdog + polling fallback
- Concurrency limits (global + per-project)
- Phase advancement after all phase tasks complete
- PID file for single-instance enforcement
- Graceful shutdown on SIGINT/SIGTERM

```python
"""Orchestrator engine — dispatch/monitor tick loop."""
from __future__ import annotations
import os
import signal
import subprocess
import time
from pathlib import Path

from yeehaw.store.store import Store
from yeehaw.signal.protocol import SignalWatcher, read_signal
from yeehaw.agent.profiles import resolve_profile
from yeehaw.agent.launcher import build_task_prompt, build_launch_command, write_launcher
from yeehaw.git.worktree import branch_name, prepare_worktree, cleanup_worktree
from yeehaw.tmux.session import launch_agent, has_session, kill_session, capture_pane


class Orchestrator:
    def __init__(self, store: Store, repo_root: Path) -> None:
        self.store = store
        self.repo_root = repo_root
        self.config = store.get_scheduler_config()
        self.signal_watcher = SignalWatcher(repo_root / ".yeehaw" / "signals")
        self.running = False
        self._poll_counter = 0

    def run(self, project_id: int | None = None) -> None:
        """Start the orchestrator loop."""
        self._write_pid_file()
        self._install_signal_handlers()
        self.running = True
        self.signal_watcher.start()
        self.store.log_event("orchestrator_start", "Orchestrator started")

        try:
            while self.running:
                self._tick(project_id)
                time.sleep(self.config["tick_interval_sec"])
        finally:
            self.signal_watcher.stop()
            self._remove_pid_file()
            self.store.log_event("orchestrator_stop", "Orchestrator stopped")

    def stop(self) -> None:
        self.running = False

    # --- Tick ---

    def _tick(self, project_id: int | None) -> None:
        self._monitor_active(project_id)
        self._dispatch_queued(project_id)
        self._poll_counter += 1

    # --- Monitor ---

    def _monitor_active(self, project_id: int | None) -> None:
        # Check watchdog signals
        for signal_path in self.signal_watcher.get_ready_signals():
            self._process_signal_file(signal_path)

        # Periodic polling fallback (every ~30s = 6 ticks at 5s)
        if self._poll_counter % 6 == 0:
            for signal_path in self.signal_watcher.poll_signals():
                self._process_signal_file(signal_path)

        # Check active tasks for crashes/timeouts
        active = self.store.list_tasks(project_id=project_id, status="in-progress")
        for task in active:
            session = f"yeehaw-task-{task['id']}"

            if not has_session(session):
                # Check for late signal
                signal_dir = Path(task["signal_dir"])
                signal_file = signal_dir / "signal.json"
                if signal_file.exists():
                    self._process_signal_file(signal_file)
                else:
                    self._handle_crash(task)
                continue

            if self._is_timed_out(task):
                self._handle_timeout(task, session)

    def _process_signal_file(self, signal_path: Path) -> None:
        """Read a signal file and process the result."""
        data = read_signal(signal_path)
        if data is None:
            return

        task = self.store.get_task(data["task_id"])
        if task is None or task["status"] != "in-progress":
            return

        session = f"yeehaw-task-{task['id']}"

        if data["status"] == "done":
            if self._run_verification(task):
                self.store.complete_task(task["id"], "done")
                self.store.log_event("task_done", data.get("summary", ""),
                                     task_id=task["id"])
            else:
                self.store.fail_task(task["id"], "Verification command failed")
                self._maybe_retry(task)

        elif data["status"] == "failed":
            self.store.fail_task(task["id"], data.get("summary", "Unknown failure"))
            self._maybe_retry(task)

        elif data["status"] == "blocked":
            self.store.complete_task(task["id"], "blocked")
            self.store.create_alert("warn",
                                     f"Task {task['id']} blocked: {data.get('summary', '')}",
                                     task_id=task["id"])

        # Cleanup
        kill_session(session)
        if task.get("worktree_path"):
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

        # Check if phase is complete
        self._check_phase_completion(task["phase_id"])

    # --- Dispatch ---

    def _dispatch_queued(self, project_id: int | None) -> None:
        global_active = self.store.count_active_tasks()
        if global_active >= self.config["max_global_tasks"]:
            return

        queued = self.store.list_tasks(project_id=project_id, status="queued")
        for task in queued:
            if self.store.count_active_tasks() >= self.config["max_global_tasks"]:
                break
            project_active = self.store.count_active_tasks(task["project_id"])
            if project_active >= self.config["max_per_project"]:
                continue
            self._launch_task(task)

    def _launch_task(self, task: dict) -> None:
        try:
            profile = resolve_profile(task.get("assigned_agent"))
            branch = branch_name(task["task_number"], task["title"])
            worktree_path = prepare_worktree(self.repo_root, branch)

            signal_dir = self.repo_root / ".yeehaw" / "signals" / f"task-{task['id']}"
            signal_dir.mkdir(parents=True, exist_ok=True)

            prompt = build_task_prompt(task, str(signal_dir), task.get("last_failure"))

            self.store.assign_task(
                task["id"], profile.name, branch,
                str(worktree_path), str(signal_dir),
            )

            session = f"yeehaw-task-{task['id']}"

            # Use launcher script for long prompts
            launcher_path = signal_dir / "launch.sh"
            write_launcher(launcher_path, profile, prompt)
            launch_agent(session, str(worktree_path), str(launcher_path))

            self.store.log_event("task_launched", f"Agent: {profile.name}",
                                 task_id=task["id"])

        except (subprocess.CalledProcessError, OSError) as exc:
            self.store.fail_task(task["id"], str(exc))
            self.store.create_alert("error", f"Failed to launch task {task['id']}: {exc}",
                                     task_id=task["id"])

    # --- Helpers ---

    def _run_verification(self, task: dict) -> bool:
        """Run the phase verification command if set. Returns True if ok."""
        phase = self.store.get_phase(task["phase_id"])
        if not phase or not phase.get("verify_cmd"):
            return True
        result = subprocess.run(
            phase["verify_cmd"], shell=True, cwd=self.repo_root,
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0

    def _is_timed_out(self, task: dict) -> bool:
        if not task.get("started_at"):
            return False
        from datetime import datetime, timezone
        started = datetime.fromisoformat(task["started_at"])
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return elapsed > self.config["task_timeout_min"] * 60

    def _handle_timeout(self, task: dict, session: str) -> None:
        kill_session(session)
        self.store.fail_task(task["id"], "Task timed out")
        self.store.log_event("task_timeout", "", task_id=task["id"])
        self._maybe_retry(task)
        if task.get("worktree_path"):
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

    def _handle_crash(self, task: dict) -> None:
        self.store.fail_task(task["id"], "Tmux session lost")
        self.store.log_event("session_lost", "", task_id=task["id"])
        self._maybe_retry(task)
        if task.get("worktree_path"):
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

    def _maybe_retry(self, task: dict) -> None:
        if task["attempts"] < task["max_attempts"]:
            self.store.queue_task(task["id"])
            self.store.log_event("task_retry", f"Attempt {task['attempts'] + 1}",
                                 task_id=task["id"])
        else:
            self.store.create_alert("error",
                                     f"Task {task['id']} exhausted {task['max_attempts']} retries",
                                     task_id=task["id"])

    def _check_phase_completion(self, phase_id: int) -> None:
        tasks = self.store.list_tasks_by_phase(phase_id)
        if not tasks:
            return
        if all(t["status"] == "done" for t in tasks):
            phase = self.store.get_phase(phase_id)
            if phase and phase.get("verify_cmd"):
                result = subprocess.run(
                    phase["verify_cmd"], shell=True, cwd=self.repo_root,
                    capture_output=True, text=True, timeout=120,
                )
                status = "completed" if result.returncode == 0 else "failed"
            else:
                status = "completed"
            self.store.update_phase_status(phase_id, status)
            if status == "completed":
                self._queue_next_phase(phase_id)

    def _queue_next_phase(self, completed_phase_id: int) -> None:
        phase = self.store.get_phase(completed_phase_id)
        if not phase:
            return
        phases = self.store.list_phases(phase["roadmap_id"])
        next_phases = [p for p in phases if p["phase_number"] == phase["phase_number"] + 1]
        if next_phases:
            next_phase = next_phases[0]
            tasks = self.store.list_tasks_by_phase(next_phase["id"])
            for task in tasks:
                self.store.queue_task(task["id"])
            self.store.update_phase_status(next_phase["id"], "executing")
        else:
            # All phases done — mark roadmap completed
            self.store.update_roadmap_status(phase["roadmap_id"], "completed")
            self.store.log_event("roadmap_completed",
                                 f"Roadmap {phase['roadmap_id']} finished")

    # --- PID file ---

    def _write_pid_file(self) -> None:
        pid_path = self.repo_root / ".yeehaw" / "orchestrator.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        if pid_path.exists():
            try:
                old_pid = int(pid_path.read_text().strip())
                os.kill(old_pid, 0)  # Check if process exists
                raise RuntimeError(
                    f"Another orchestrator is running (PID {old_pid}). "
                    f"Kill it first or remove {pid_path}"
                )
            except (ProcessLookupError, ValueError):
                pass  # Stale PID file

        pid_path.write_text(str(os.getpid()))

    def _remove_pid_file(self) -> None:
        pid_path = self.repo_root / ".yeehaw" / "orchestrator.pid"
        pid_path.unlink(missing_ok=True)

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
```

---

### 5.10 `mcp/server.py`

FastMCP server exposing the SQLite store as tools for the Planner agent.

```python
"""FastMCP server exposing yeehaw task management tools."""
from __future__ import annotations
import argparse
from pathlib import Path
from fastmcp import FastMCP

from yeehaw.store.store import Store
from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap

# Module-level store, initialized in main()
_store: Store | None = None

mcp = FastMCP("yeehaw")


def _get_store() -> Store:
    assert _store is not None, "Store not initialized"
    return _store


@mcp.tool()
def create_project(name: str, repo_root: str) -> dict:
    """Create a new project. repo_root should be the absolute path to the git repository."""
    store = _get_store()
    project_id = store.create_project(name, repo_root)
    return {"id": project_id, "name": name, "repo_root": repo_root}


@mcp.tool()
def list_projects() -> list[dict]:
    """List all registered projects with their IDs and names."""
    return _get_store().list_projects()


@mcp.tool()
def create_roadmap(project_name: str, markdown: str) -> dict:
    """Create a roadmap from structured markdown.

    The markdown must follow the yeehaw roadmap format:
    - H1: # Roadmap: project-name
    - H2: ## Phase N: title
    - Optional verify: **Verify:** `command`
    - H3: ### Task N.M: title
    - Body text is the task description

    Returns the roadmap ID and counts of created phases and tasks.
    """
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    try:
        roadmap = parse_roadmap(markdown)
    except ValueError as e:
        return {"error": str(e)}

    errors = validate_roadmap(roadmap)
    if errors:
        return {"error": "Validation failed", "details": errors}

    roadmap_id = store.create_roadmap(project["id"], markdown)
    task_count = 0
    for phase in roadmap.phases:
        phase_id = store.create_phase(roadmap_id, phase.number, phase.title, phase.verify_cmd)
        for task in phase.tasks:
            store.create_task(roadmap_id, phase_id, task.number, task.title, task.description)
            task_count += 1

    return {
        "roadmap_id": roadmap_id,
        "phases": len(roadmap.phases),
        "tasks": task_count,
    }


@mcp.tool()
def list_tasks(project_name: str | None = None, status: str | None = None) -> list[dict]:
    """List tasks, optionally filtered by project name and/or status."""
    store = _get_store()
    project_id = None
    if project_name:
        project = store.get_project(project_name)
        if not project:
            return [{"error": f"Project '{project_name}' not found"}]
        project_id = project["id"]
    return store.list_tasks(project_id=project_id, status=status)


@mcp.tool()
def get_project_status(project_name: str) -> dict:
    """Get comprehensive status for a project including phase progress."""
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        return {"project": project_name, "roadmap": None, "phases": []}

    phases = store.list_phases(roadmap["id"])
    phase_status = []
    for phase in phases:
        tasks = store.list_tasks_by_phase(phase["id"])
        phase_status.append({
            "phase": phase["phase_number"],
            "title": phase["title"],
            "status": phase["status"],
            "tasks_total": len(tasks),
            "tasks_done": sum(1 for t in tasks if t["status"] == "done"),
            "tasks_in_progress": sum(1 for t in tasks if t["status"] == "in-progress"),
            "tasks_failed": sum(1 for t in tasks if t["status"] == "failed"),
        })

    return {
        "project": project_name,
        "roadmap_id": roadmap["id"],
        "roadmap_status": roadmap["status"],
        "phases": phase_status,
    }


@mcp.tool()
def approve_roadmap(project_name: str) -> dict:
    """Approve the active roadmap and queue Phase 1 tasks for execution."""
    store = _get_store()
    project = store.get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        return {"error": "No active roadmap"}
    if roadmap["status"] != "draft":
        return {"error": f"Roadmap is '{roadmap['status']}', not 'draft'"}

    store.update_roadmap_status(roadmap["id"], "approved")

    # Queue Phase 1 tasks
    phases = store.list_phases(roadmap["id"])
    queued = 0
    if phases:
        phase1 = phases[0]
        tasks = store.list_tasks_by_phase(phase1["id"])
        for task in tasks:
            store.queue_task(task["id"])
            queued += 1
        store.update_phase_status(phase1["id"], "executing")

    store.update_roadmap_status(roadmap["id"], "executing")

    return {"approved": True, "queued_tasks": queued}


@mcp.tool()
def update_task(task_id: int, status: str | None = None,
                assigned_agent: str | None = None) -> dict:
    """Update a task's status or assigned agent."""
    store = _get_store()
    task = store.get_task(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    if status:
        if status in ("done", "blocked"):
            store.complete_task(task_id, status)
        elif status == "failed":
            store.fail_task(task_id, "Manually marked as failed")
        elif status == "queued":
            store.queue_task(task_id)

    return {"task_id": task_id, "updated": True}


def main() -> None:
    """Entry point for the MCP server process."""
    parser = argparse.ArgumentParser(description="Yeehaw MCP Server")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    args = parser.parse_args()

    global _store
    _store = Store(Path(args.db))

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

---

### 5.11 `planner/session.py`

Manages the lifecycle of a Planner agent session with MCP connectivity.

```python
"""Planner session — launches an AI agent connected to the yeehaw MCP server."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def start_planner_session(
    db_path: Path,
    briefing_file: Path | None = None,
    agent: str = "claude",
) -> None:
    """Start an interactive planner session.

    1. Writes a temporary MCP config pointing to yeehaw's MCP server
    2. Launches the chosen AI agent with MCP config
    3. Optionally pre-loads a briefing file as initial context
    """
    mcp_config = {
        "mcpServers": {
            "yeehaw": {
                "command": sys.executable,
                "args": ["-m", "yeehaw.mcp.server", "--db", str(db_path)],
            }
        }
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="yeehaw-mcp-", delete=False
    ) as f:
        json.dump(mcp_config, f)
        config_path = f.name

    try:
        prompt_parts = [
            "You are a project planner connected to the yeehaw task management system.",
            "You have access to MCP tools: create_project, create_roadmap, list_projects,",
            "list_tasks, get_project_status, approve_roadmap, update_task.",
            "",
            "Your job is to translate the human's briefing into structured projects and roadmaps.",
            "Use the create_roadmap tool with properly formatted markdown.",
            "",
            "Roadmap format:",
            "# Roadmap: project-name",
            "## Phase N: title",
            "**Verify:** `command`",
            "### Task N.M: title",
            "Description...",
        ]

        if briefing_file and briefing_file.exists():
            content = briefing_file.read_text()
            prompt_parts.extend([
                "",
                "## Briefing",
                "",
                content,
            ])

        prompt = "\n".join(prompt_parts)

        if agent == "claude":
            cmd = [
                "claude",
                "--mcp-config", config_path,
                "-p", prompt,
            ]
        elif agent == "gemini":
            # Gemini MCP config approach may differ; adapt as needed
            cmd = ["gemini", "-p", prompt]
        else:
            raise ValueError(f"Unsupported planner agent: {agent}")

        # Replace process with the agent
        os.execvp(cmd[0], cmd)

    finally:
        # This only runs if execvp fails
        os.unlink(config_path)
```

---

### 5.12 `cli/main.py`

Root argparse parser with subcommand routing.

```python
"""Yeehaw CLI — Multi-agent coding orchestrator."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def _get_db_path() -> Path:
    """Resolve the database path from current directory."""
    return Path.cwd() / ".yeehaw" / "yeehaw.db"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="yeehaw",
        description="Multi-agent coding orchestrator",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- init ---
    subparsers.add_parser("init", help="Initialize yeehaw in the current directory")

    # --- project ---
    project_parser = subparsers.add_parser("project", help="Manage projects")
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)

    add_p = project_sub.add_parser("add", help="Register a project")
    add_p.add_argument("name", help="Project name")
    add_p.add_argument("--repo", default=".", help="Repository root (default: current dir)")

    project_sub.add_parser("list", help="List projects")

    rm_p = project_sub.add_parser("remove", help="Remove a project")
    rm_p.add_argument("name", help="Project name to remove")

    # --- roadmap ---
    roadmap_parser = subparsers.add_parser("roadmap", help="Manage roadmaps")
    roadmap_sub = roadmap_parser.add_subparsers(dest="roadmap_command", required=True)

    create_r = roadmap_sub.add_parser("create", help="Create roadmap from markdown file")
    create_r.add_argument("file", help="Markdown file path")
    create_r.add_argument("--project", required=True, help="Project name")

    show_r = roadmap_sub.add_parser("show", help="Show active roadmap")
    show_r.add_argument("--project", required=True, help="Project name")

    approve_r = roadmap_sub.add_parser("approve", help="Approve roadmap for execution")
    approve_r.add_argument("--project", required=True, help="Project name")

    # --- plan ---
    plan_parser = subparsers.add_parser("plan", help="Start AI planning session")
    plan_parser.add_argument("briefing", nargs="?", help="Briefing file (optional)")
    plan_parser.add_argument("--agent", default="claude", choices=["claude", "gemini"],
                             help="Planner agent (default: claude)")

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Start the orchestrator")
    run_parser.add_argument("--project", help="Limit to a specific project")

    # --- status ---
    status_parser = subparsers.add_parser("status", help="Show task status")
    status_parser.add_argument("--project", help="Filter by project")
    status_parser.add_argument("--json", action="store_true", dest="as_json",
                               help="Output as JSON")

    # --- attach ---
    attach_parser = subparsers.add_parser("attach", help="Attach to a worker's tmux session")
    attach_parser.add_argument("task_id", type=int, help="Task ID")

    # --- stop ---
    stop_parser = subparsers.add_parser("stop", help="Stop a running task")
    stop_parser.add_argument("task_id", nargs="?", type=int, help="Task ID")
    stop_parser.add_argument("--all", action="store_true", help="Stop all tasks")

    # --- scheduler ---
    sched_parser = subparsers.add_parser("scheduler", help="Manage scheduler config")
    sched_sub = sched_parser.add_subparsers(dest="scheduler_command", required=True)

    sched_sub.add_parser("show", help="Show scheduler configuration")

    config_s = sched_sub.add_parser("config", help="Update scheduler configuration")
    config_s.add_argument("--max-global", type=int, help="Max concurrent tasks globally")
    config_s.add_argument("--max-project", type=int, help="Max concurrent tasks per project")
    config_s.add_argument("--tick", type=int, help="Tick interval in seconds")
    config_s.add_argument("--timeout", type=int, help="Task timeout in minutes")

    # --- alerts ---
    alerts_parser = subparsers.add_parser("alerts", help="Show alerts")
    alerts_parser.add_argument("--ack", type=int, metavar="ID", help="Acknowledge alert by ID")

    # --- Parse and dispatch ---
    args = parser.parse_args(argv)

    # Import handlers lazily to keep startup fast
    if args.command == "init":
        from yeehaw.cli.project import handle_init
        handle_init(_get_db_path())

    elif args.command == "project":
        from yeehaw.cli.project import handle_project
        handle_project(args, _get_db_path())

    elif args.command == "roadmap":
        from yeehaw.cli.roadmap import handle_roadmap
        handle_roadmap(args, _get_db_path())

    elif args.command == "plan":
        from yeehaw.cli.plan import handle_plan
        handle_plan(args, _get_db_path())

    elif args.command == "run":
        from yeehaw.cli.run import handle_run
        handle_run(args, _get_db_path())

    elif args.command == "status":
        from yeehaw.cli.status import handle_status
        handle_status(args, _get_db_path())

    elif args.command == "attach":
        from yeehaw.cli.attach import handle_attach
        handle_attach(args, _get_db_path())

    elif args.command == "stop":
        from yeehaw.cli.stop import handle_stop
        handle_stop(args, _get_db_path())

    elif args.command == "scheduler":
        from yeehaw.cli.scheduler import handle_scheduler
        handle_scheduler(args, _get_db_path())

    elif args.command == "alerts":
        from yeehaw.cli.status import handle_alerts
        handle_alerts(args, _get_db_path())
```

---

### 5.13 CLI Subcommands

Each CLI subcommand lives in its own file. Here are the specifications:

#### `cli/project.py`

```python
"""Project management commands."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store


def handle_init(db_path: Path) -> None:
    """Initialize yeehaw in the current directory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    store.close()
    print(f"Initialized yeehaw at {db_path.parent}")


def handle_project(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        if args.project_command == "add":
            repo_root = str(Path(args.repo).resolve())
            project_id = store.create_project(args.name, repo_root)
            print(f"Project '{args.name}' created (id={project_id})")

        elif args.project_command == "list":
            projects = store.list_projects()
            if not projects:
                print("No projects.")
                return
            print(f"{'ID':<6} {'Name':<30} {'Repo Root'}")
            print("-" * 70)
            for p in projects:
                print(f"{p['id']:<6} {p['name']:<30} {p['repo_root']}")

        elif args.project_command == "remove":
            if store.delete_project(args.name):
                print(f"Project '{args.name}' removed.")
            else:
                print(f"Project '{args.name}' not found.")
    finally:
        store.close()
```

#### `cli/roadmap.py`

```python
"""Roadmap management commands."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store
from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap


def handle_roadmap(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        if args.roadmap_command == "create":
            _create_roadmap(store, args)
        elif args.roadmap_command == "show":
            _show_roadmap(store, args)
        elif args.roadmap_command == "approve":
            _approve_roadmap(store, args)
    finally:
        store.close()


def _create_roadmap(store: Store, args) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"Error: File '{args.file}' not found.")
        return

    raw_md = md_path.read_text()
    try:
        roadmap = parse_roadmap(raw_md)
    except ValueError as e:
        print(f"Error: {e}")
        return

    errors = validate_roadmap(roadmap)
    if errors:
        print("Validation errors:")
        for err in errors:
            print(f"  - {err}")
        return

    roadmap_id = store.create_roadmap(project["id"], raw_md)
    for phase in roadmap.phases:
        phase_id = store.create_phase(roadmap_id, phase.number, phase.title, phase.verify_cmd)
        for task in phase.tasks:
            store.create_task(roadmap_id, phase_id, task.number, task.title, task.description)

    total_tasks = sum(len(p.tasks) for p in roadmap.phases)
    print(f"Roadmap created (id={roadmap_id}): {len(roadmap.phases)} phases, {total_tasks} tasks")


def _show_roadmap(store: Store, args) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        print("No active roadmap.")
        return

    print(f"Roadmap #{roadmap['id']} [{roadmap['status']}]")
    print()
    phases = store.list_phases(roadmap["id"])
    for phase in phases:
        print(f"  Phase {phase['phase_number']}: {phase['title']} [{phase['status']}]")
        if phase.get("verify_cmd"):
            print(f"    Verify: {phase['verify_cmd']}")
        tasks = store.list_tasks_by_phase(phase["id"])
        for task in tasks:
            status_icon = {"pending": " ", "queued": "~", "in-progress": ">",
                           "done": "+", "failed": "!", "blocked": "#"}.get(task["status"], "?")
            agent = f" ({task['assigned_agent']})" if task.get("assigned_agent") else ""
            print(f"    [{status_icon}] Task {task['task_number']}: {task['title']}{agent}")
    print()


def _approve_roadmap(store: Store, args) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        print("No active roadmap.")
        return
    if roadmap["status"] != "draft":
        print(f"Roadmap is '{roadmap['status']}', not 'draft'.")
        return

    store.update_roadmap_status(roadmap["id"], "approved")

    # Queue Phase 1 tasks
    phases = store.list_phases(roadmap["id"])
    queued = 0
    if phases:
        phase1 = phases[0]
        tasks = store.list_tasks_by_phase(phase1["id"])
        for task in tasks:
            store.queue_task(task["id"])
            queued += 1
        store.update_phase_status(phase1["id"], "executing")

    store.update_roadmap_status(roadmap["id"], "executing")
    print(f"Roadmap approved. {queued} tasks queued for Phase 1.")
```

#### `cli/run.py`

```python
"""Orchestrator run command."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store
from yeehaw.orchestrator.engine import Orchestrator


def handle_run(args, db_path: Path) -> None:
    store = Store(db_path)
    repo_root = db_path.parent.parent  # .yeehaw/ -> repo root

    project_id = None
    if args.project:
        project = store.get_project(args.project)
        if not project:
            print(f"Error: Project '{args.project}' not found.")
            store.close()
            return
        project_id = project["id"]

    print("Starting orchestrator... (Ctrl+C to stop)")
    orchestrator = Orchestrator(store, repo_root)
    try:
        orchestrator.run(project_id=project_id)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        store.close()
```

#### `cli/status.py`

```python
"""Status display commands."""
from __future__ import annotations
import json as json_module
from pathlib import Path
from yeehaw.store.store import Store


def handle_status(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        project_id = None
        if args.project:
            project = store.get_project(args.project)
            if not project:
                print(f"Error: Project '{args.project}' not found.")
                return
            project_id = project["id"]

        tasks = store.list_tasks(project_id=project_id)

        if args.as_json:
            print(json_module.dumps(tasks, indent=2, default=str))
            return

        if not tasks:
            print("No tasks.")
            return

        print(f"{'ID':<6} {'Task':<10} {'Title':<35} {'Status':<14} {'Agent':<10}")
        print("-" * 80)
        for t in tasks:
            agent = t.get("assigned_agent") or ""
            print(f"{t['id']:<6} {t['task_number']:<10} {t['title']:<35} "
                  f"{t['status']:<14} {agent:<10}")

        # Summary
        by_status = {}
        for t in tasks:
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(by_status.items())]
        print(f"\nTotal: {len(tasks)} tasks ({', '.join(parts)})")

    finally:
        store.close()


def handle_alerts(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        if args.ack:
            store.ack_alert(args.ack)
            print(f"Alert {args.ack} acknowledged.")
            return

        alerts = store.list_alerts()
        if not alerts:
            print("No alerts.")
            return

        for a in alerts:
            print(f"[{a['severity'].upper()}] #{a['id']} - {a['message']} ({a['created_at']})")
    finally:
        store.close()
```

#### `cli/plan.py`

```python
"""AI planning session command."""
from __future__ import annotations
from pathlib import Path
from yeehaw.planner.session import start_planner_session


def handle_plan(args, db_path: Path) -> None:
    briefing = Path(args.briefing) if args.briefing else None
    if briefing and not briefing.exists():
        print(f"Error: Briefing file '{args.briefing}' not found.")
        return

    print(f"Starting planner session (agent={args.agent})...")
    start_planner_session(db_path, briefing_file=briefing, agent=args.agent)
```

#### `cli/attach.py`

```python
"""Attach to a worker agent's tmux session."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store
from yeehaw.tmux.session import has_session, attach_session


def handle_attach(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        task = store.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found.")
            return

        session = f"yeehaw-task-{task['id']}"
        if not has_session(session):
            print(f"No active tmux session for task {task['id']}.")
            return

        print(f"Attaching to {session}... (Ctrl+b, d to detach)")
        attach_session(session)  # This replaces the process
    finally:
        store.close()
```

#### `cli/stop.py`

```python
"""Stop running tasks."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store
from yeehaw.tmux.session import kill_session, has_session
from yeehaw.git.worktree import cleanup_worktree


def handle_stop(args, db_path: Path) -> None:
    store = Store(db_path)
    repo_root = db_path.parent.parent

    try:
        if args.all:
            tasks = store.list_tasks(status="in-progress")
        elif args.task_id:
            task = store.get_task(args.task_id)
            tasks = [task] if task else []
        else:
            print("Specify a task ID or --all.")
            return

        for task in tasks:
            if not task:
                continue
            session = f"yeehaw-task-{task['id']}"
            if has_session(session):
                kill_session(session)
            if task.get("worktree_path"):
                cleanup_worktree(repo_root, Path(task["worktree_path"]))
            store.fail_task(task["id"], "Manually stopped")
            store.log_event("task_stopped", f"Task {task['id']} stopped by user",
                            task_id=task["id"])
            print(f"Stopped task {task['id']}: {task['title']}")

        if not tasks:
            print("No matching tasks found.")
    finally:
        store.close()
```

#### `cli/scheduler.py`

```python
"""Scheduler configuration commands."""
from __future__ import annotations
from pathlib import Path
from yeehaw.store.store import Store


def handle_scheduler(args, db_path: Path) -> None:
    store = Store(db_path)
    try:
        if args.scheduler_command == "show":
            config = store.get_scheduler_config()
            print("Scheduler Configuration:")
            print(f"  Max global tasks:   {config['max_global_tasks']}")
            print(f"  Max per project:    {config['max_per_project']}")
            print(f"  Tick interval:      {config['tick_interval_sec']}s")
            print(f"  Task timeout:       {config['task_timeout_min']} min")

        elif args.scheduler_command == "config":
            updates = {}
            if args.max_global is not None:
                updates["max_global_tasks"] = args.max_global
            if args.max_project is not None:
                updates["max_per_project"] = args.max_project
            if args.tick is not None:
                updates["tick_interval_sec"] = args.tick
            if args.timeout is not None:
                updates["task_timeout_min"] = args.timeout

            if not updates:
                print("No changes specified.")
                return

            store.update_scheduler_config(**updates)
            print("Scheduler config updated:")
            for k, v in updates.items():
                print(f"  {k} = {v}")
    finally:
        store.close()
```

---

## 6. CLI Command Reference

```
yeehaw init                              Initialize .yeehaw/ in current directory
yeehaw project add <name> [--repo .]     Register a project
yeehaw project list                      List all projects
yeehaw project remove <name>             Remove a project

yeehaw roadmap create <file> --project P Create roadmap from markdown
yeehaw roadmap show --project P          Display active roadmap with status
yeehaw roadmap approve --project P       Approve and queue Phase 1

yeehaw plan [briefing.md] [--agent X]    Start AI planner session via MCP
yeehaw run [--project P]                 Start orchestrator loop
yeehaw status [--project P] [--json]     Show task status table
yeehaw attach <task-id>                  Attach to worker's tmux session
yeehaw stop <task-id>                    Stop a specific task
yeehaw stop --all                        Stop all running tasks

yeehaw scheduler show                    Show scheduler config
yeehaw scheduler config --max-global N   Update scheduler config
yeehaw alerts                            Show unacked alerts
yeehaw alerts --ack ID                   Acknowledge an alert
```

---

## 7. Testing Specification

See `ARCHITECTURE/11-testing.md` for full details. Summary:

### Fixtures (`tests/conftest.py`)

- `tmp_db` — ephemeral SQLite database (per-test)
- `tmp_repo` — temporary git repository (for worktree tests)
- `sample_roadmap_md` — valid roadmap markdown string

### Test Files

| File | Tests | Notes |
|------|-------|-------|
| `test_store.py` | CRUD for all entities, constraints, config singleton | Unit |
| `test_roadmap.py` | Parse valid/invalid, validation errors | Unit |
| `test_signal.py` | Read with retries, handler debounce | Unit |
| `test_git.py` | Branch naming, worktree lifecycle | Integration (needs git) |
| `test_agent.py` | Profile resolution, prompt building, escaping | Unit |
| `test_orchestrator.py` | Tick logic with mocked tmux/git | Unit + mocks |
| `test_mcp.py` | Tool registration, responses | Unit |
| `test_cli.py` | Argument parsing, output format | Unit |

### Running

```bash
uv run pytest                                    # All tests
uv run pytest --cov=yeehaw --cov-report=term     # With coverage
uv run pytest -m "not integration"               # Skip git/tmux tests
```

---

## 8. Implementation Order

Build the project **bottom-up**, testing each layer before moving to the next:

### Phase 1: Foundation (no external deps)

1. **`store/schema.py`** — DDL strings, `init_db()`
2. **`store/store.py`** — Full Store class
3. **`tests/test_store.py`** — All CRUD tests
4. **`roadmap/parser.py`** — Parser + validator
5. **`tests/test_roadmap.py`** — Parse/validate tests

### Phase 2: Infrastructure

6. **`git/worktree.py`** — Branch naming + worktree management
7. **`tests/test_git.py`** — Branch naming tests (mark worktree tests as integration)
8. **`tmux/session.py`** — All tmux operations
9. **`agent/profiles.py`** — Registry + resolution
10. **`agent/launcher.py`** — Prompt building + launch command
11. **`tests/test_agent.py`** — Profile + launcher tests

### Phase 3: Signal Protocol

12. **`signal/protocol.py`** — `read_signal()`, `SignalHandler`, `SignalWatcher`
13. **`tests/test_signal.py`** — Read retries, debounce logic

### Phase 4: Orchestrator

14. **`orchestrator/engine.py`** — Full orchestrator with all methods
15. **`tests/test_orchestrator.py`** — Mocked tick loop tests

### Phase 5: MCP Server

16. **`mcp/server.py`** — FastMCP tools
17. **`tests/test_mcp.py`** — Tool response tests
18. **`planner/session.py`** — Planner session launcher

### Phase 6: CLI

19. **`cli/main.py`** — Root parser + dispatch
20. **`cli/project.py`**, **`cli/roadmap.py`**, **`cli/run.py`**,
    **`cli/status.py`**, **`cli/plan.py`**, **`cli/attach.py`**,
    **`cli/stop.py`**, **`cli/scheduler.py`**
21. **`tests/test_cli.py`** — CLI parsing tests
22. **`__main__.py`** — Entry point

### Phase 7: Integration

23. **`pyproject.toml`** — Package config, entry point
24. End-to-end manual test: `yeehaw init` → `project add` → `roadmap create` →
    `roadmap approve` → `run` → verify tasks dispatch

---

## 9. Edge Cases & Error Handling

See `ARCHITECTURE/10-edge-cases.md` for comprehensive coverage. Key points:

- **Signal race conditions**: 500ms debounce + 3 parse retries at 200ms
- **Agent timeout**: Kill session, check late signal, retry up to 4 attempts
- **Tmux crash**: Detect via `has_session()`, check signal, retry
- **Concurrent orchestrators**: PID file enforcement
- **Graceful shutdown**: SIGINT/SIGTERM → finish current tick → cleanup
- **Worktree conflicts**: Force-update branch, force-remove stale worktree
- **SQLite busy**: WAL mode + 5000ms busy timeout

---

## 10. Acceptance Criteria

The implementation is complete when:

- [ ] `uv run pytest` passes with 80%+ coverage
- [ ] `yeehaw init` creates `.yeehaw/yeehaw.db` with correct schema
- [ ] `yeehaw project add/list/remove` works correctly
- [ ] `yeehaw roadmap create` parses markdown and populates DB
- [ ] `yeehaw roadmap show` displays status tree
- [ ] `yeehaw roadmap approve` queues Phase 1 tasks
- [ ] `yeehaw run` starts orchestrator, dispatches tasks to tmux
- [ ] `yeehaw status` prints formatted table
- [ ] `yeehaw attach <id>` drops into tmux session
- [ ] `yeehaw stop <id>` kills session and cleans up
- [ ] `yeehaw plan` launches MCP server + Planner agent
- [ ] `yeehaw scheduler show/config` manages concurrency
- [ ] `yeehaw alerts` shows and acknowledges alerts
- [ ] Signal protocol detects task completion within 5 seconds
- [ ] Phase advancement works after all phase tasks complete
- [ ] Retry logic re-queues failed tasks up to max_attempts
- [ ] Concurrent orchestrator instances are prevented by PID file
- [ ] Graceful shutdown preserves running tmux sessions
