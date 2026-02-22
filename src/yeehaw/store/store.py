"""SQLite store for yeehaw state management."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yeehaw.store.schema import init_db

if TYPE_CHECKING:
    from yeehaw.roadmap.parser import Roadmap, Task

_EDITABLE_TASK_STATUSES = {"pending", "queued"}
_LOCKED_TASK_STATUSES = {"paused", "in-progress", "done", "failed", "blocked"}
_RE_TASK_COMPONENTS = re.compile(r"^(\d+)\.(\d+)$")


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
        """Insert a roadmap and return its id.

        A project may only have one active roadmap at a time. Creating a new roadmap
        supersedes older non-invalid roadmaps for the same project.
        """
        now = self._now()
        self._conn.execute(
            "UPDATE roadmaps SET status = 'invalid', updated_at = ? "
            "WHERE project_id = ? AND status != 'invalid'",
            (now, project_id),
        )
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

    def delete_roadmap(self, roadmap_id: int) -> bool:
        """Delete a roadmap and all dependent phase/task/worktree rows."""
        existing = self.get_roadmap(roadmap_id)
        if existing is None:
            return False

        task_rows = self._conn.execute(
            "SELECT id FROM tasks WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchall()
        task_ids = [int(row[0]) for row in task_rows]

        try:
            self._clear_task_relationships(task_ids)

            self._conn.execute("DELETE FROM tasks WHERE roadmap_id = ?", (roadmap_id,))
            self._conn.execute("DELETE FROM roadmap_phases WHERE roadmap_id = ?", (roadmap_id,))
            self._conn.execute("DELETE FROM roadmaps WHERE id = ?", (roadmap_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return True

    def edit_roadmap_in_place(
        self,
        roadmap_id: int,
        raw_md: str,
        roadmap: "Roadmap",
    ) -> dict[str, int]:
        """Apply a parsed roadmap to an existing roadmap row without creating a new roadmap id."""
        existing_roadmap = self.get_roadmap(roadmap_id)
        if existing_roadmap is None:
            raise ValueError(f"Roadmap {roadmap_id} not found")

        roadmap_status = str(existing_roadmap["status"])
        if roadmap_status in {"invalid", "completed"}:
            raise ValueError(f"Roadmap is '{roadmap_status}' and cannot be edited in place")

        existing_phases = self.list_phases(roadmap_id)
        existing_phase_numbers = [int(phase["phase_number"]) for phase in existing_phases]
        requested_phase_numbers = [phase.number for phase in roadmap.phases]
        if roadmap_status != "draft" and requested_phase_numbers != existing_phase_numbers:
            raise ValueError(
                "Cannot add/remove/reorder phases after roadmap leaves 'draft'; "
                "edit tasks within existing phases only"
            )

        existing_phase_by_number = {
            int(phase["phase_number"]): phase for phase in existing_phases
        }
        requested_phase_set = set(requested_phase_numbers)

        stats = {
            "phases_created": 0,
            "phases_updated": 0,
            "phases_deleted": 0,
            "tasks_created": 0,
            "tasks_updated": 0,
            "tasks_deleted": 0,
            "tasks_queued": 0,
        }

        now = self._now()
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                "UPDATE roadmaps SET raw_md = ?, updated_at = ? WHERE id = ?",
                (raw_md, now, roadmap_id),
            )

            if roadmap_status == "draft":
                for phase_number, phase in existing_phase_by_number.items():
                    if phase_number in requested_phase_set:
                        continue
                    existing_tasks = self._list_tasks_for_phase(int(phase["id"]))
                    blocked = [
                        task
                        for task in existing_tasks
                        if task["status"] not in _EDITABLE_TASK_STATUSES
                    ]
                    if blocked:
                        blocked_ids = ", ".join(task["task_number"] for task in blocked)
                        raise ValueError(
                            f"Cannot remove phase {phase_number}; contains non-editable task(s): {blocked_ids}"
                        )
                    task_ids = [int(task["id"]) for task in existing_tasks]
                    self._clear_task_relationships(task_ids)
                    if task_ids:
                        placeholders = ", ".join("?" for _ in task_ids)
                        self._conn.execute(
                            f"DELETE FROM tasks WHERE id IN ({placeholders})",
                            task_ids,
                        )
                    self._conn.execute("DELETE FROM roadmap_phases WHERE id = ?", (phase["id"],))
                    stats["phases_deleted"] += 1

            phase_rows: dict[int, dict[str, Any]] = {}
            for phase in roadmap.phases:
                existing_phase = existing_phase_by_number.get(phase.number)
                if existing_phase is None:
                    cur = self._conn.execute(
                        "INSERT INTO roadmap_phases (roadmap_id, phase_number, title, verify_cmd) "
                        "VALUES (?, ?, ?, ?)",
                        (roadmap_id, phase.number, phase.title, phase.verify_cmd),
                    )
                    phase_rows[phase.number] = {
                        "id": int(cur.lastrowid),
                        "status": "pending",
                    }
                    stats["phases_created"] += 1
                    continue

                if (
                    existing_phase["title"] != phase.title
                    or existing_phase["verify_cmd"] != phase.verify_cmd
                ):
                    self._conn.execute(
                        "UPDATE roadmap_phases SET title = ?, verify_cmd = ? WHERE id = ?",
                        (phase.title, phase.verify_cmd, existing_phase["id"]),
                    )
                    stats["phases_updated"] += 1

                phase_rows[phase.number] = {
                    "id": int(existing_phase["id"]),
                    "status": str(existing_phase["status"]),
                }

            for phase in roadmap.phases:
                phase_row = phase_rows[phase.number]
                phase_stats = self._sync_phase_tasks(
                    roadmap_id=roadmap_id,
                    phase_id=int(phase_row["id"]),
                    phase_number=phase.number,
                    phase_status=str(phase_row["status"]),
                    new_tasks=phase.tasks,
                    now=now,
                )
                for key, value in phase_stats.items():
                    stats[key] += value

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return stats

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
        return self._list_tasks_for_phase(phase_id)

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
            "SELECT t.*, r.status as roadmap_status, p.name as project_name, p.id as project_id, "
            "p.repo_root as project_repo_root "
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
            "SELECT t.*, r.status as roadmap_status, p.name as project_name, p.id as project_id, "
            "p.repo_root as project_repo_root "
            "FROM tasks t "
            "JOIN roadmaps r ON t.roadmap_id = r.id "
            "JOIN projects p ON r.project_id = p.id "
            "WHERE r.status != 'invalid'"
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

    def reset_task_attempts(self, task_id: int) -> bool:
        """Reset retry attempt metadata for a task."""
        cur = self._conn.execute(
            "UPDATE tasks SET attempts = 0, last_failure = NULL, updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def pause_task(self, task_id: int) -> bool:
        """Pause a task that is pending, queued, or in-progress."""
        task = self.get_task(task_id)
        if task is None:
            return False
        if task["status"] not in {"pending", "queued", "in-progress"}:
            return False
        self._conn.execute(
            "UPDATE tasks SET status = 'paused', updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self._conn.commit()
        return True

    def resume_task(self, task_id: int) -> bool:
        """Resume a paused task by queuing it for dispatch."""
        task = self.get_task(task_id)
        if task is None:
            return False
        if task["status"] != "paused":
            return False
        self._conn.execute(
            "UPDATE tasks SET status = 'queued', completed_at = NULL, updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self._conn.commit()
        return True

    def count_active_tasks(self, project_id: int | None = None) -> int:
        """Count in-progress tasks, optionally for a project."""
        if project_id is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks t JOIN roadmaps r ON t.roadmap_id = r.id "
                "WHERE r.project_id = ? AND r.status != 'invalid' AND t.status = 'in-progress'",
                (project_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks t JOIN roadmaps r ON t.roadmap_id = r.id "
                "WHERE r.status != 'invalid' AND t.status = 'in-progress'",
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

    def _sync_phase_tasks(
        self,
        *,
        roadmap_id: int,
        phase_id: int,
        phase_number: int,
        phase_status: str,
        new_tasks: list["Task"],
        now: str,
    ) -> dict[str, int]:
        """Synchronize one phase's task set while protecting non-editable history."""
        existing_tasks = self._list_tasks_for_phase(phase_id)
        existing_by_number = {str(task["task_number"]): task for task in existing_tasks}
        new_by_number = {task.number: task for task in new_tasks}

        if phase_status in {"completed", "failed"}:
            if set(existing_by_number.keys()) != set(new_by_number.keys()):
                raise ValueError(
                    f"Phase {phase_number} is '{phase_status}' and cannot be structurally edited"
                )
            for task_number, existing in existing_by_number.items():
                candidate = new_by_number[task_number]
                if (
                    existing["title"].strip() != candidate.title.strip()
                    or str(existing["description"]).strip() != candidate.description.strip()
                ):
                    raise ValueError(
                        f"Phase {phase_number} is '{phase_status}' and task {task_number} cannot be modified"
                    )
            return {
                "tasks_created": 0,
                "tasks_updated": 0,
                "tasks_deleted": 0,
                "tasks_queued": 0,
            }

        mapped_existing_ids: set[int] = set()
        mapped_new_numbers: set[str] = set()

        for existing in existing_tasks:
            existing_status = str(existing["status"])
            if existing_status not in _LOCKED_TASK_STATUSES:
                continue
            task_number = str(existing["task_number"])
            candidate = new_by_number.get(task_number)
            if candidate is None:
                raise ValueError(
                    f"Cannot remove task {task_number}; status is '{existing_status}'"
                )
            if (
                existing["title"].strip() != candidate.title.strip()
                or str(existing["description"]).strip() != candidate.description.strip()
            ):
                raise ValueError(
                    f"Cannot modify task {task_number}; status is '{existing_status}'"
                )
            mapped_existing_ids.add(int(existing["id"]))
            mapped_new_numbers.add(task_number)

        editable_existing = [
            task
            for task in existing_tasks
            if str(task["status"]) in _EDITABLE_TASK_STATUSES
            and int(task["id"]) not in mapped_existing_ids
        ]

        existing_fingerprint = {
            int(task["id"]): self._task_fingerprint(
                str(task["title"]),
                str(task["description"]),
            )
            for task in editable_existing
        }

        for new_task in new_tasks:
            if new_task.number in mapped_new_numbers:
                continue
            new_fp = self._task_fingerprint(new_task.title, new_task.description)
            match_id: int | None = None
            for existing in editable_existing:
                existing_id = int(existing["id"])
                if existing_id in mapped_existing_ids:
                    continue
                if existing_fingerprint[existing_id] == new_fp:
                    match_id = existing_id
                    break
            if match_id is not None:
                mapped_existing_ids.add(match_id)
                mapped_new_numbers.add(new_task.number)
                existing = next(task for task in editable_existing if int(task["id"]) == match_id)
                if (
                    str(existing["task_number"]) != new_task.number
                    or str(existing["title"]) != new_task.title
                    or str(existing["description"]) != new_task.description
                ):
                    self._conn.execute(
                        "UPDATE tasks SET task_number = ?, title = ?, description = ?, updated_at = ? "
                        "WHERE id = ?",
                        (new_task.number, new_task.title, new_task.description, now, match_id),
                    )

        remaining_existing_by_number = {
            str(task["task_number"]): task
            for task in editable_existing
            if int(task["id"]) not in mapped_existing_ids
        }
        for new_task in new_tasks:
            if new_task.number in mapped_new_numbers:
                continue
            existing = remaining_existing_by_number.get(new_task.number)
            if existing is None:
                continue
            existing_id = int(existing["id"])
            mapped_existing_ids.add(existing_id)
            mapped_new_numbers.add(new_task.number)
            if (
                str(existing["title"]) != new_task.title
                or str(existing["description"]) != new_task.description
            ):
                self._conn.execute(
                    "UPDATE tasks SET title = ?, description = ?, updated_at = ? WHERE id = ?",
                    (new_task.title, new_task.description, now, existing_id),
                )

        tasks_updated = 0
        for existing in editable_existing:
            existing_id = int(existing["id"])
            if existing_id not in mapped_existing_ids:
                continue
            refreshed = self._conn.execute(
                "SELECT task_number, title, description FROM tasks WHERE id = ?",
                (existing_id,),
            ).fetchone()
            if refreshed is None:
                continue
            if (
                str(existing["task_number"]) != str(refreshed["task_number"])
                or str(existing["title"]) != str(refreshed["title"])
                or str(existing["description"]) != str(refreshed["description"])
            ):
                tasks_updated += 1

        tasks_created = 0
        tasks_queued = 0
        for new_task in new_tasks:
            if new_task.number in mapped_new_numbers:
                continue
            new_status = "queued" if phase_status == "executing" else "pending"
            self._conn.execute(
                "INSERT INTO tasks (roadmap_id, phase_id, task_number, title, description, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    roadmap_id,
                    phase_id,
                    new_task.number,
                    new_task.title,
                    new_task.description,
                    new_status,
                ),
            )
            tasks_created += 1
            if new_status == "queued":
                tasks_queued += 1

        to_delete = [
            int(task["id"])
            for task in editable_existing
            if int(task["id"]) not in mapped_existing_ids
        ]
        if to_delete:
            self._clear_task_relationships(to_delete)
            placeholders = ", ".join("?" for _ in to_delete)
            self._conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", to_delete)

        return {
            "tasks_created": tasks_created,
            "tasks_updated": tasks_updated,
            "tasks_deleted": len(to_delete),
            "tasks_queued": tasks_queued,
        }

    def _list_tasks_for_phase(self, phase_id: int) -> list[dict[str, Any]]:
        """List tasks for one phase in stable numeric order."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE phase_id = ?",
            (phase_id,),
        ).fetchall()
        tasks = [dict(row) for row in rows]
        tasks.sort(
            key=lambda task: self._task_sort_key(
                str(task["task_number"]),
                int(task["id"]),
            )
        )
        return tasks

    @staticmethod
    def _task_sort_key(task_number: str, task_id: int) -> tuple[int, int, int]:
        """Return a numeric sort key for task numbering."""
        match = _RE_TASK_COMPONENTS.match(task_number.strip())
        if match:
            return (int(match.group(1)), int(match.group(2)), task_id)
        return (10**9, 10**9, task_id)

    @staticmethod
    def _task_fingerprint(title: str, description: str) -> tuple[str, str]:
        """Return a compact identity fingerprint for a task body."""
        return (title.strip(), description.strip())

    def _clear_task_relationships(self, task_ids: list[int]) -> None:
        """Detach task-linked rows that must survive task deletion."""
        if not task_ids:
            return
        placeholders = ", ".join("?" for _ in task_ids)
        self._conn.execute(
            f"UPDATE events SET task_id = NULL WHERE task_id IN ({placeholders})",
            task_ids,
        )
        self._conn.execute(
            f"UPDATE alerts SET task_id = NULL WHERE task_id IN ({placeholders})",
            task_ids,
        )
        self._conn.execute(
            f"DELETE FROM git_worktrees WHERE task_id IN ({placeholders})",
            task_ids,
        )
