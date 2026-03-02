"""Tests for in-place migration from legacy yeehaw SQLite schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from yeehaw.store.store import Store


def _create_legacy_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            root_path   TEXT NOT NULL,
            guidelines  TEXT NOT NULL DEFAULT '',
            git_remote  TEXT NOT NULL DEFAULT '',
            main_branch TEXT NOT NULL DEFAULT 'main',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE roadmaps (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            raw_text   TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','invalid','approved','executing','completed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE roadmap_phases (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            roadmap_id        INTEGER NOT NULL REFERENCES roadmaps(id),
            number            INTEGER NOT NULL,
            title             TEXT NOT NULL,
            verification_text TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','running','passed','failed')),
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE tasks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            phase_id      INTEGER NOT NULL REFERENCES roadmap_phases(id),
            number        TEXT NOT NULL,
            title         TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued','dispatched','running','done','failed','timeout','skipped')),
            agent         TEXT NOT NULL DEFAULT '',
            branch        TEXT NOT NULL DEFAULT '',
            worktree_path TEXT NOT NULL DEFAULT '',
            signal_dir    TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts  INTEGER NOT NULL DEFAULT 4,
            started_at    TEXT,
            finished_at   TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE git_worktrees (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   INTEGER NOT NULL REFERENCES tasks(id),
            path      TEXT NOT NULL,
            branch    TEXT NOT NULL,
            base_sha  TEXT NOT NULL DEFAULT '',
            state     TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','merged','removed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            task_id    INTEGER REFERENCES tasks(id),
            kind       TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            task_id    INTEGER REFERENCES tasks(id),
            severity   TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','error')),
            message    TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE TABLE scheduler_config (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            max_global      INTEGER NOT NULL DEFAULT 5,
            max_per_project INTEGER NOT NULL DEFAULT 3,
            timeout_minutes INTEGER NOT NULL DEFAULT 60,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    conn.execute(
        "INSERT INTO projects (id, name, root_path) VALUES (1, 'legacy', '/legacy/repo')"
    )
    conn.execute(
        "INSERT INTO roadmaps (id, project_id, raw_text, status) VALUES (10, 1, '# Legacy', 'approved')"
    )
    conn.execute(
        """
        INSERT INTO roadmap_phases (id, roadmap_id, number, title, verification_text, status)
        VALUES (100, 10, 1, 'Legacy Phase', 'pytest -q', 'running')
        """
    )
    conn.execute(
        """
        INSERT INTO tasks (
            id, phase_id, number, title, description, status,
            agent, branch, worktree_path, signal_dir,
            attempt_count, max_attempts, started_at, finished_at
        ) VALUES (
            1000, 100, '1.1', 'Legacy Task', 'desc', 'running',
            'codex', 'legacy-branch', '/tmp/legacy-worktree', '/tmp/legacy-signal',
            2, 4, '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00'
        )
        """
    )
    conn.execute(
        "INSERT INTO git_worktrees (id, task_id, path, branch, state) VALUES (500, 1000, '/tmp/legacy-worktree', 'legacy-branch', 'removed')"
    )
    conn.execute(
        "INSERT INTO events (id, project_id, task_id, kind, message) VALUES (700, 1, 1000, 'legacy_event', 'message')"
    )
    conn.execute(
        "INSERT INTO alerts (id, project_id, task_id, severity, message, status) VALUES (800, 1, 1000, 'warn', 'legacy alert', 'resolved')"
    )
    conn.execute(
        "INSERT INTO scheduler_config (id, max_global, max_per_project, timeout_minutes) VALUES (1, 7, 4, 45)"
    )

    conn.commit()
    conn.close()


def _create_prepaused_db(db_path: Path) -> None:
    """Create a modern-like DB whose tasks CHECK does not yet include paused."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            repo_root   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE roadmaps (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES projects(id),
            raw_md      TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','approved','executing','completed','invalid')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE roadmap_phases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            roadmap_id    INTEGER NOT NULL REFERENCES roadmaps(id),
            phase_number  INTEGER NOT NULL,
            title         TEXT    NOT NULL,
            verify_cmd    TEXT,
            status        TEXT    NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','executing','completed','failed')),
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE tasks (
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
        """
    )
    conn.execute(
        "INSERT INTO projects (id, name, repo_root) VALUES (1, 'proj-a', '/tmp/repo-a')"
    )
    conn.execute(
        "INSERT INTO roadmaps (id, project_id, raw_md, status) VALUES (1, 1, '# Roadmap: proj-a', 'executing')"
    )
    conn.execute(
        "INSERT INTO roadmap_phases (id, roadmap_id, phase_number, title, status) VALUES (1, 1, 1, 'Phase 1', 'executing')"
    )
    conn.execute(
        """
        INSERT INTO tasks (
            id, roadmap_id, phase_id, task_number, title, description, status
        ) VALUES (1, 1, 1, '1.1', 'Task 1', 'desc', 'queued')
        """
    )
    conn.commit()
    conn.close()


def test_legacy_schema_migrates_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    _create_legacy_db(db_path)

    store = Store(db_path)
    try:
        project = store.get_project("legacy")
        assert project is not None
        assert project["repo_root"] == "/legacy/repo"

        roadmap = store.get_roadmap(10)
        assert roadmap is not None
        assert roadmap["raw_md"] == "# Legacy"

        phase = store.get_phase(100)
        assert phase is not None
        assert phase["phase_number"] == 1
        assert phase["verify_cmd"] == "pytest -q"
        assert phase["status"] == "executing"

        task = store.get_task(1000)
        assert task is not None
        assert task["roadmap_id"] == 10
        assert task["task_number"] == "1.1"
        assert task["status"] == "in-progress"
        assert task["assigned_agent"] == "codex"
        assert task["branch_name"] == "legacy-branch"
        assert task["attempts"] == 2

        worktrees = store._conn.execute("SELECT * FROM git_worktrees WHERE id = 500").fetchone()
        assert worktrees is not None
        assert worktrees["status"] == "cleaned"

        events = store.list_events(limit=5)
        assert any(event["kind"] == "legacy_event" for event in events)

        acked_alerts = store.list_alerts(acked=True)
        assert len(acked_alerts) == 1
        assert acked_alerts[0]["message"] == "legacy alert"

        config = store.get_scheduler_config()
        assert config["max_global_tasks"] == 7
        assert config["max_per_project"] == 4
        assert config["tick_interval_sec"] == 5
        assert config["task_timeout_min"] == 45

        created_id = store.create_project("new", "/new/repo")
        assert created_id > 0
    finally:
        store.close()

    backups = list(db_path.parent.glob("yeehaw.legacy-backup-*.db"))
    assert len(backups) == 1


def test_fresh_db_does_not_create_legacy_backup(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    store = Store(db_path)
    store.close()

    backups = list(db_path.parent.glob("yeehaw.legacy-backup-*.db"))
    assert backups == []


def test_existing_modern_db_migrates_tasks_to_support_paused(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    _create_prepaused_db(db_path)

    store = Store(db_path)
    try:
        assert store.pause_task(1) is True
        task = store.get_task(1)
        assert task is not None
        assert task["status"] == "paused"
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
    ).fetchone()
    roadmap_cols = {
        str(item[1])
        for item in conn.execute("PRAGMA table_info(roadmaps)").fetchall()
    }
    task_dependencies = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'task_dependencies'"
    ).fetchone()
    task_file_targets = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'task_file_targets'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert "'paused'" in str(row[0] or "")
    assert "integration_branch" in roadmap_cols
    assert task_dependencies is not None
    assert task_file_targets is not None


def test_scheduler_default_per_project_is_bumped_from_3_to_5(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE scheduler_config (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            max_global_tasks    INTEGER NOT NULL DEFAULT 5,
            max_per_project     INTEGER NOT NULL DEFAULT 3,
            tick_interval_sec   INTEGER NOT NULL DEFAULT 5,
            task_timeout_min    INTEGER NOT NULL DEFAULT 60
        );
        INSERT INTO scheduler_config (
            id, max_global_tasks, max_per_project, tick_interval_sec, task_timeout_min
        ) VALUES (1, 5, 3, 5, 60);
        """
    )
    conn.commit()
    conn.close()

    store = Store(db_path)
    try:
        config = store.get_scheduler_config()
        assert config["max_per_project"] == 5
    finally:
        store.close()


def test_scheduler_custom_per_project_value_is_preserved(tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE scheduler_config (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            max_global_tasks    INTEGER NOT NULL DEFAULT 5,
            max_per_project     INTEGER NOT NULL DEFAULT 3,
            tick_interval_sec   INTEGER NOT NULL DEFAULT 5,
            task_timeout_min    INTEGER NOT NULL DEFAULT 60
        );
        INSERT INTO scheduler_config (
            id, max_global_tasks, max_per_project, tick_interval_sec, task_timeout_min
        ) VALUES (1, 7, 3, 5, 60);
        """
    )
    conn.commit()
    conn.close()

    store = Store(db_path)
    try:
        config = store.get_scheduler_config()
        assert config["max_global_tasks"] == 7
        assert config["max_per_project"] == 3
    finally:
        store.close()
