"""SQLite store for yeehaw state management."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yeehaw.store.schema import init_db


class Store:
    """Single-writer SQLite store."""

    def __init__(self, db_path: Path) -> None:
        self._conn = init_db(db_path)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_project(self, name: str, repo_root: str) -> int:
        """Insert a project and return its id."""
        cur = self._conn.execute(
            "INSERT INTO projects (name, repo_root) VALUES (?, ?)",
            (name, repo_root),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_project(self, name: str) -> dict[str, Any] | None:
        """Get project by name."""
        row = self._conn.execute(
            "SELECT * FROM projects WHERE name = ?",
            (name,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_projects(self) -> list[dict[str, Any]]:
        """List all projects."""
        rows = self._conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_project(self, name: str) -> bool:
        """Delete project by name and return True if it existed."""
        cur = self._conn.execute("DELETE FROM projects WHERE name = ?", (name,))
        self._conn.commit()
        return cur.rowcount > 0

    def create_roadmap(self, project_id: int, raw_md: str) -> int:
        """Insert a roadmap and return its id."""
        cur = self._conn.execute(
            "INSERT INTO roadmaps (project_id, raw_md) VALUES (?, ?)",
            (project_id, raw_md),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_roadmap(self, roadmap_id: int) -> dict[str, Any] | None:
        """Get roadmap by id."""
        row = self._conn.execute(
            "SELECT * FROM roadmaps WHERE id = ?",
            (roadmap_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def get_active_roadmap(self, project_id: int) -> dict[str, Any] | None:
        """Get latest non-invalid roadmap for a project."""
        row = self._conn.execute(
            "SELECT * FROM roadmaps WHERE project_id = ? AND status != 'invalid' "
            "ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def update_roadmap_status(self, roadmap_id: int, status: str) -> None:
        """Update roadmap status."""
        self._conn.execute(
            "UPDATE roadmaps SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), roadmap_id),
        )
        self._conn.commit()

    def create_phase(
        self,
        roadmap_id: int,
        number: int,
        title: str,
        verify_cmd: str | None,
    ) -> int:
        """Insert roadmap phase and return id."""
        cur = self._conn.execute(
            "INSERT INTO roadmap_phases (roadmap_id, phase_number, title, verify_cmd) "
            "VALUES (?, ?, ?, ?)",
            (roadmap_id, number, title, verify_cmd),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_phase(self, phase_id: int) -> dict[str, Any] | None:
        """Get phase by id."""
        row = self._conn.execute(
            "SELECT * FROM roadmap_phases WHERE id = ?",
            (phase_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_phases(self, roadmap_id: int) -> list[dict[str, Any]]:
        """List roadmap phases in numeric order."""
        rows = self._conn.execute(
            "SELECT * FROM roadmap_phases WHERE roadmap_id = ? ORDER BY phase_number",
            (roadmap_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_tasks_by_phase(self, phase_id: int) -> list[dict[str, Any]]:
        """List tasks for a phase in task-number order."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE phase_id = ? ORDER BY task_number",
            (phase_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_phase_status(self, phase_id: int, status: str) -> None:
        """Update phase status."""
        self._conn.execute(
            "UPDATE roadmap_phases SET status = ? WHERE id = ?",
            (status, phase_id),
        )
        self._conn.commit()

    def create_task(
        self,
        roadmap_id: int,
        phase_id: int,
        number: str,
        title: str,
        description: str,
    ) -> int:
        """Insert task and return id."""
        cur = self._conn.execute(
            "INSERT INTO tasks (roadmap_id, phase_id, task_number, title, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (roadmap_id, phase_id, number, title, description),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        """Get task plus project metadata."""
        row = self._conn.execute(
            "SELECT t.*, p.name as project_name, p.id as project_id "
            "FROM tasks t "
            "JOIN roadmaps r ON t.roadmap_id = r.id "
            "JOIN projects p ON r.project_id = p.id "
            "WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_tasks(
        self,
        project_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks, optionally filtered by project and status."""
        query = (
            "SELECT t.*, p.name as project_name, p.id as project_id "
            "FROM tasks t "
            "JOIN roadmaps r ON t.roadmap_id = r.id "
            "JOIN projects p ON r.project_id = p.id "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if project_id is not None:
            query += " AND p.id = ?"
            params.append(project_id)
        if status is not None:
            query += " AND t.status = ?"
            params.append(status)
        query += " ORDER BY t.task_number"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def assign_task(
        self,
        task_id: int,
        agent: str,
        branch: str,
        worktree: str,
        signal_dir: str,
    ) -> None:
        """Mark task in-progress and persist assignment metadata."""
        now = self._now()
        self._conn.execute(
            "UPDATE tasks SET status = 'in-progress', assigned_agent = ?, "
            "branch_name = ?, worktree_path = ?, signal_dir = ?, "
            "attempts = attempts + 1, started_at = ?, updated_at = ? WHERE id = ?",
            (agent, branch, worktree, signal_dir, now, now, task_id),
        )
        self._conn.commit()

    def complete_task(self, task_id: int, status: str) -> None:
        """Mark task complete with terminal status."""
        now = self._now()
        self._conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (status, now, now, task_id),
        )
        self._conn.commit()

    def fail_task(self, task_id: int, failure_msg: str) -> None:
        """Mark task failed and record failure reason."""
        self._conn.execute(
            "UPDATE tasks SET status = 'failed', last_failure = ?, updated_at = ? "
            "WHERE id = ?",
            (failure_msg, self._now(), task_id),
        )
        self._conn.commit()

    def queue_task(self, task_id: int) -> None:
        """Queue task for dispatch."""
        self._conn.execute(
            "UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self._conn.commit()

    def count_active_tasks(self, project_id: int | None = None) -> int:
        """Count in-progress tasks, optionally for a project."""
        if project_id is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks t JOIN roadmaps r ON t.roadmap_id = r.id "
                "WHERE r.project_id = ? AND t.status = 'in-progress'",
                (project_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'in-progress'",
            ).fetchone()
        return int(row[0])

    def log_event(
        self,
        kind: str,
        message: str,
        project_id: int | None = None,
        task_id: int | None = None,
    ) -> None:
        """Create an event record."""
        self._conn.execute(
            "INSERT INTO events (project_id, task_id, kind, message) VALUES (?, ?, ?, ?)",
            (project_id, task_id, kind, message),
        )
        self._conn.commit()

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent events newest-first."""
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_alert(
        self,
        severity: str,
        message: str,
        project_id: int | None = None,
        task_id: int | None = None,
    ) -> None:
        """Create an alert."""
        self._conn.execute(
            "INSERT INTO alerts (project_id, task_id, severity, message) VALUES (?, ?, ?, ?)",
            (project_id, task_id, severity, message),
        )
        self._conn.commit()

    def list_alerts(self, acked: bool = False) -> list[dict[str, Any]]:
        """List alerts by ack status."""
        rows = self._conn.execute(
            "SELECT * FROM alerts WHERE acked = ? ORDER BY id DESC",
            (int(acked),),
        ).fetchall()
        return [dict(r) for r in rows]

    def ack_alert(self, alert_id: int) -> None:
        """Acknowledge an alert."""
        self._conn.execute("UPDATE alerts SET acked = 1 WHERE id = ?", (alert_id,))
        self._conn.commit()

    def get_scheduler_config(self) -> dict[str, Any]:
        """Return scheduler config row."""
        row = self._conn.execute(
            "SELECT * FROM scheduler_config WHERE id = 1",
        ).fetchone()
        return dict(row)

    def update_scheduler_config(self, **kwargs: Any) -> None:
        """Patch scheduler config using provided known keys."""
        valid_keys = {
            "max_global_tasks",
            "max_per_project",
            "tick_interval_sec",
            "task_timeout_min",
        }
        for key in kwargs:
            if key not in valid_keys:
                raise ValueError(f"Invalid config key: {key}")
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values())
        self._conn.execute(f"UPDATE scheduler_config SET {sets} WHERE id = 1", values)
        self._conn.commit()
