"""SQLite store for yeehaw state management."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from yeehaw.roadmap.dependencies import parse_task_dependencies
from yeehaw.store.schema import init_db

if TYPE_CHECKING:
    from yeehaw.roadmap.parser import Roadmap, Task

_EDITABLE_TASK_STATUSES = {"pending", "queued"}
_LOCKED_TASK_STATUSES = {"paused", "in-progress", "done", "failed", "blocked"}
_RE_TASK_COMPONENTS = re.compile(r"^(\d+)\.(\d+)$")
_RE_TASK_METADATA_LINE = re.compile(r"^\*\*([^*]+):\*\*\s*(.+)$")
_OVERLAP_SAFE_METADATA_KEYS = frozenset(
    {
        "safe",
        "overlapsafe",
        "safetooverlap",
        "conflictsafe",
        "parallelsafe",
        "dispatchsafe",
        "allowoverlap",
    }
)
_TRUTHY_METADATA_VALUES = frozenset({"1", "true", "yes", "y", "on", "safe", "allow"})


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

    @staticmethod
    def _decode_conflict_files(raw: Any) -> list[str]:
        if not isinstance(raw, str) or not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if isinstance(item, str)]

    def _task_merge_attempt_row_to_dict(
        self,
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        record = self._row_to_dict(row)
        if record is None:
            return None
        record["conflict_files"] = self._decode_conflict_files(record.get("conflict_files"))
        return record

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_budget_value(field: str, value: int | None) -> int | None:
        """Validate optional positive integer budget metadata."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer >= 1")
        if value < 1:
            raise ValueError(f"{field} must be >= 1")
        return value

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

            self._replace_roadmap_dependencies(roadmap_id, roadmap)
            self._replace_roadmap_file_targets(roadmap_id, roadmap)
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

    def set_roadmap_integration_branch(self, roadmap_id: int, branch_name: str) -> None:
        """Persist integration branch for a roadmap execution."""
        self._conn.execute(
            "UPDATE roadmaps SET integration_branch = ?, updated_at = ? WHERE id = ?",
            (branch_name, self._now(), roadmap_id),
        )
        self._conn.commit()

    def apply_roadmap_dependencies(self, roadmap_id: int, roadmap: "Roadmap") -> None:
        """Apply parsed task dependency edges for one roadmap."""
        self._conn.execute("BEGIN")
        try:
            self._replace_roadmap_dependencies(roadmap_id, roadmap)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def apply_roadmap_file_targets(self, roadmap_id: int, roadmap: "Roadmap") -> None:
        """Apply parsed task file targets for one roadmap."""
        self._conn.execute("BEGIN")
        try:
            self._replace_roadmap_file_targets(roadmap_id, roadmap)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

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
        file_targets: list[str] | None = None,
        max_tokens: int | None = None,
        max_runtime_min: int | None = None,
    ) -> int:
        """Insert task and return id."""
        validated_max_tokens = self._validate_budget_value("max_tokens", max_tokens)
        validated_max_runtime_min = self._validate_budget_value(
            "max_runtime_min",
            max_runtime_min,
        )
        cur = self._conn.execute(
            "INSERT INTO tasks "
            "(roadmap_id, phase_id, task_number, title, description, max_tokens, max_runtime_min) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                roadmap_id,
                phase_id,
                number,
                title,
                description,
                validated_max_tokens,
                validated_max_runtime_min,
            ),
        )
        task_id = int(cur.lastrowid)
        if file_targets:
            self._replace_task_file_targets(task_id, file_targets)
        self._conn.commit()
        return task_id

    def create_linked_reconcile_task(
        self,
        *,
        failed_task_id: int,
        failure_threshold: int,
        observed_attempts: int,
        failure_messages: list[str] | None = None,
    ) -> int | None:
        """Create a queued reconcile follow-up task linked to a failed task."""
        failed_task = self.get_task(failed_task_id)
        if failed_task is None:
            return None

        source_task_number = str(failed_task["task_number"])
        reconcile_number = self._next_reconcile_task_number(
            phase_id=int(failed_task["phase_id"]),
            source_task_number=source_task_number,
        )
        title = f"Reconcile {source_task_number} after repeated failures"
        observed_failures: list[str] = []
        if failure_messages:
            observed_failures.extend(str(message) for message in failure_messages)
        last_failure = failed_task.get("last_failure")
        if isinstance(last_failure, str) and last_failure.strip():
            observed_failures.append(last_failure)

        description = self._build_reconcile_description(
            failed_task=failed_task,
            failure_threshold=failure_threshold,
            observed_attempts=observed_attempts,
            failure_messages=observed_failures,
        )
        file_targets = self.list_task_file_targets(failed_task_id)
        reconcile_task_id = self.create_task(
            roadmap_id=int(failed_task["roadmap_id"]),
            phase_id=int(failed_task["phase_id"]),
            number=reconcile_number,
            title=title,
            description=description,
            file_targets=file_targets,
        )

        # Reconcile tasks should not recursively spawn more reconcile tasks.
        self._conn.execute(
            "UPDATE tasks SET max_attempts = 1, updated_at = ? WHERE id = ?",
            (self._now(), reconcile_task_id),
        )
        self._conn.commit()
        return reconcile_task_id

    def set_task_file_targets(self, task_id: int, file_targets: list[str]) -> None:
        """Replace persisted file targets for one task."""
        self._conn.execute("BEGIN")
        try:
            self._replace_task_file_targets(task_id, file_targets)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def list_task_file_targets(self, task_id: int) -> list[str]:
        """List normalized file targets for one task."""
        rows = self._conn.execute(
            "SELECT target_path FROM task_file_targets WHERE task_id = ? ORDER BY target_path",
            (task_id,),
        ).fetchall()
        return [str(row["target_path"]) for row in rows]

    def get_task_budget(self, task_id: int) -> dict[str, int | None] | None:
        """Return persisted task budget metadata."""
        row = self._conn.execute(
            "SELECT max_tokens, max_runtime_min FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "max_tokens": int(row["max_tokens"]) if row["max_tokens"] is not None else None,
            "max_runtime_min": (
                int(row["max_runtime_min"]) if row["max_runtime_min"] is not None else None
            ),
        }

    def set_task_budget(
        self,
        task_id: int,
        *,
        max_tokens: int | None,
        max_runtime_min: int | None,
    ) -> bool:
        """Replace task budget metadata, allowing values to be cleared with None."""
        validated_max_tokens = self._validate_budget_value("max_tokens", max_tokens)
        validated_max_runtime_min = self._validate_budget_value(
            "max_runtime_min",
            max_runtime_min,
        )
        cur = self._conn.execute(
            "UPDATE tasks SET max_tokens = ?, max_runtime_min = ?, updated_at = ? WHERE id = ?",
            (validated_max_tokens, validated_max_runtime_min, self._now(), task_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        """Get task plus project metadata."""
        row = self._conn.execute(
            "SELECT t.*, r.status as roadmap_status, r.integration_branch as roadmap_integration_branch, "
            "p.name as project_name, p.id as project_id, "
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
            "SELECT t.*, r.status as roadmap_status, r.integration_branch as roadmap_integration_branch, "
            "p.name as project_name, p.id as project_id, "
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

    def are_task_dependencies_satisfied(self, task_id: int) -> bool:
        """Return True when all blocker tasks for task_id are done."""
        row = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM task_dependencies d
            JOIN tasks blocker ON blocker.id = d.blocker_task_id
            WHERE d.blocked_task_id = ? AND blocker.status != 'done'
            """,
            (task_id,),
        ).fetchone()
        return int(row[0]) == 0

    def has_in_progress_overlap_conflict(self, task_id: int) -> bool:
        """Return True when queued task overlaps with non-safe in-progress tasks."""
        return bool(self.list_in_progress_overlap_conflicts(task_id))

    def list_in_progress_overlap_conflicts(self, task_id: int) -> list[dict[str, Any]]:
        """List in-progress same-project tasks that overlap on file targets."""
        task_row = self._conn.execute(
            "SELECT description FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None:
            return []
        if self._task_is_overlap_safe(str(task_row["description"] or "")):
            return []

        rows = self._conn.execute(
            """
            SELECT
                active.id AS active_task_id,
                active.task_number AS active_task_number,
                active.title AS active_task_title,
                active.description AS active_task_description,
                overlap.target_path AS target_path
            FROM tasks queued
            JOIN roadmaps queued_roadmap
                ON queued_roadmap.id = queued.roadmap_id
            JOIN task_file_targets queued_target
                ON queued_target.task_id = queued.id
            JOIN roadmaps active_roadmap
                ON active_roadmap.project_id = queued_roadmap.project_id
                AND active_roadmap.status != 'invalid'
            JOIN tasks active
                ON active.roadmap_id = active_roadmap.id
                AND active.status = 'in-progress'
                AND active.id != queued.id
            JOIN task_file_targets overlap
                ON overlap.task_id = active.id
                AND overlap.target_path = queued_target.target_path
            WHERE queued.id = ?
            ORDER BY active.id, overlap.target_path
            """,
            (task_id,),
        ).fetchall()

        conflicts: dict[int, dict[str, Any]] = {}
        for row in rows:
            active_id = int(row["active_task_id"])
            active_description = str(row["active_task_description"] or "")
            if self._task_is_overlap_safe(active_description):
                continue

            conflict = conflicts.get(active_id)
            if conflict is None:
                conflict = {
                    "task_id": active_id,
                    "task_number": str(row["active_task_number"]),
                    "title": str(row["active_task_title"]),
                    "target_paths": [],
                }
                conflicts[active_id] = conflict
            conflict["target_paths"].append(str(row["target_path"]))

        return list(conflicts.values())

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

    def create_hook_run(
        self,
        *,
        event_name: str,
        event_id: str,
        hook_name: str,
        status: str,
        duration_ms: int,
        summary: str | None = None,
        error: str | None = None,
        returncode: int | None = None,
        project_id: int | None = None,
        roadmap_id: int | None = None,
        phase_id: int | None = None,
        task_id: int | None = None,
    ) -> int:
        """Persist telemetry for a single hook invocation."""
        if duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        cur = self._conn.execute(
            """
            INSERT INTO hook_runs (
                project_id,
                roadmap_id,
                phase_id,
                task_id,
                event_name,
                event_id,
                hook_name,
                status,
                duration_ms,
                summary,
                error,
                returncode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                roadmap_id,
                phase_id,
                task_id,
                event_name,
                event_id,
                hook_name,
                status,
                duration_ms,
                summary,
                error,
                returncode,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_hook_run(self, hook_run_id: int) -> dict[str, Any] | None:
        """Return one hook run row by id."""
        row = self._conn.execute(
            "SELECT * FROM hook_runs WHERE id = ?",
            (hook_run_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_hook_runs(
        self,
        *,
        limit: int = 50,
        event_name: str | None = None,
        hook_name: str | None = None,
        task_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List hook run telemetry newest-first with optional filters."""
        if limit < 1:
            raise ValueError("limit must be >= 1")

        query = "SELECT * FROM hook_runs WHERE 1 = 1"
        params: list[Any] = []
        if event_name is not None:
            query += " AND event_name = ?"
            params.append(event_name)
        if hook_name is not None:
            query += " AND hook_name = ?"
            params.append(hook_name)
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def create_task_merge_attempt(
        self,
        *,
        task_id: int,
        attempt_number: int,
        status: str,
        source_branch: str,
        target_branch: str,
        source_sha_before: str | None = None,
        target_sha_before: str | None = None,
    ) -> int:
        """Insert one rebase/merge attempt row and return id."""
        if attempt_number < 1:
            raise ValueError("attempt_number must be >= 1")
        cur = self._conn.execute(
            """
            INSERT INTO task_merge_attempts (
                task_id,
                attempt_number,
                status,
                source_branch,
                target_branch,
                source_sha_before,
                target_sha_before,
                started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                attempt_number,
                status,
                source_branch,
                target_branch,
                source_sha_before,
                target_sha_before,
                self._now(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_task_merge_attempt(
        self,
        merge_attempt_id: int,
        *,
        status: str,
        source_sha_after: str | None = None,
        target_sha_after: str | None = None,
        conflict_type: str | None = None,
        conflict_files: list[str] | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Update terminal state and metadata for one merge attempt row."""
        conflict_files_json: str | None = None
        if conflict_files is not None:
            conflict_files_json = json.dumps(conflict_files)
        self._conn.execute(
            """
            UPDATE task_merge_attempts
            SET status = ?,
                source_sha_after = ?,
                target_sha_after = ?,
                conflict_type = ?,
                conflict_files = ?,
                error_detail = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                source_sha_after,
                target_sha_after,
                conflict_type,
                conflict_files_json,
                error_detail,
                self._now(),
                merge_attempt_id,
            ),
        )
        self._conn.commit()

    def get_task_merge_attempt(self, merge_attempt_id: int) -> dict[str, Any] | None:
        """Return one merge attempt by id."""
        row = self._conn.execute(
            "SELECT * FROM task_merge_attempts WHERE id = ?",
            (merge_attempt_id,),
        ).fetchone()
        return self._task_merge_attempt_row_to_dict(row)

    def list_task_merge_attempts(
        self,
        *,
        task_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List merge attempts for a task, newest first."""
        if limit < 1:
            raise ValueError("limit must be >= 1")
        rows = self._conn.execute(
            "SELECT * FROM task_merge_attempts WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = self._task_merge_attempt_row_to_dict(row)
            if record is not None:
                records.append(record)
        return records

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

    def _replace_roadmap_dependencies(self, roadmap_id: int, roadmap: "Roadmap") -> None:
        """Replace dependency edges for one roadmap based on parsed task metadata."""
        task_rows = self._conn.execute(
            "SELECT id, task_number FROM tasks WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchall()
        task_id_by_number = {
            str(row["task_number"]).strip(): int(row["id"])
            for row in task_rows
        }

        edges: list[tuple[int, int]] = []
        graph: dict[str, list[str]] = {}
        for phase in roadmap.phases:
            for task in phase.tasks:
                blocked_number = task.number.strip()
                blocked_id = task_id_by_number.get(blocked_number)
                if blocked_id is None:
                    raise ValueError(f"Cannot map dependencies: missing task {blocked_number}")
                refs = parse_task_dependencies(task.description)
                graph[blocked_number] = refs
                for ref in refs:
                    blocker_id = task_id_by_number.get(ref)
                    if blocker_id is None:
                        raise ValueError(
                            f"Cannot map dependencies: task {blocked_number} depends on unknown task {ref}"
                        )
                    if blocker_id == blocked_id:
                        raise ValueError(f"Task {blocked_number} cannot depend on itself")
                    edges.append((blocked_id, blocker_id))

        cycle = self._find_dependency_cycle(graph)
        if cycle:
            raise ValueError("Task dependency cycle detected: " + " -> ".join(cycle))

        task_ids = [int(row["id"]) for row in task_rows]
        if task_ids:
            placeholders = ", ".join("?" for _ in task_ids)
            self._conn.execute(
                f"DELETE FROM task_dependencies WHERE blocked_task_id IN ({placeholders}) "
                f"OR blocker_task_id IN ({placeholders})",
                [*task_ids, *task_ids],
            )
        if edges:
            self._conn.executemany(
                "INSERT OR IGNORE INTO task_dependencies (blocked_task_id, blocker_task_id) "
                "VALUES (?, ?)",
                edges,
            )

    def _replace_roadmap_file_targets(self, roadmap_id: int, roadmap: "Roadmap") -> None:
        """Replace file target rows for one roadmap based on parsed task metadata."""
        task_rows = self._conn.execute(
            "SELECT id, task_number FROM tasks WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchall()
        task_id_by_number = {
            str(row["task_number"]).strip(): int(row["id"])
            for row in task_rows
        }

        target_rows: list[tuple[int, str]] = []
        for phase in roadmap.phases:
            for task in phase.tasks:
                task_number = task.number.strip()
                task_id = task_id_by_number.get(task_number)
                if task_id is None:
                    raise ValueError(f"Cannot map file targets: missing task {task_number}")
                for target in self._normalize_file_targets(task.file_targets):
                    target_rows.append((task_id, target))

        task_ids = [int(row["id"]) for row in task_rows]
        if task_ids:
            placeholders = ", ".join("?" for _ in task_ids)
            self._conn.execute(
                f"DELETE FROM task_file_targets WHERE task_id IN ({placeholders})",
                task_ids,
            )
        if target_rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO task_file_targets (task_id, target_path) VALUES (?, ?)",
                target_rows,
            )

    def _replace_task_file_targets(self, task_id: int, file_targets: list[str]) -> None:
        """Replace file targets for one task within the current transaction."""
        normalized = self._normalize_file_targets(file_targets)
        self._conn.execute("DELETE FROM task_file_targets WHERE task_id = ?", (task_id,))
        if normalized:
            self._conn.executemany(
                "INSERT OR IGNORE INTO task_file_targets (task_id, target_path) VALUES (?, ?)",
                [(task_id, target) for target in normalized],
            )

    @staticmethod
    def _find_dependency_cycle(graph: dict[str, list[str]]) -> list[str]:
        """Return one cycle path when graph contains a dependency cycle."""
        temp_mark: set[str] = set()
        perm_mark: set[str] = set()
        stack: list[str] = []

        def visit(node: str) -> list[str]:
            if node in perm_mark:
                return []
            if node in temp_mark:
                idx = stack.index(node) if node in stack else 0
                return stack[idx:] + [node]

            temp_mark.add(node)
            stack.append(node)
            for dep in graph.get(node, []):
                if dep not in graph:
                    continue
                cycle = visit(dep)
                if cycle:
                    return cycle
            stack.pop()
            temp_mark.remove(node)
            perm_mark.add(node)
            return []

        for candidate in graph:
            cycle = visit(candidate)
            if cycle:
                return cycle
        return []

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

    def _next_reconcile_task_number(
        self,
        *,
        phase_id: int,
        source_task_number: str,
    ) -> str:
        """Generate the next stable reconcile task number within a phase."""
        source_match = _RE_TASK_COMPONENTS.match(source_task_number.strip())
        if source_match is not None:
            major = int(source_match.group(1))
        else:
            phase = self.get_phase(phase_id)
            major = int(phase["phase_number"]) if phase is not None else 0

        rows = self._conn.execute(
            "SELECT task_number FROM tasks WHERE phase_id = ?",
            (phase_id,),
        ).fetchall()
        used_minors: set[int] = set()
        for row in rows:
            candidate = str(row["task_number"] or "").strip()
            match = _RE_TASK_COMPONENTS.match(candidate)
            if match is None or int(match.group(1)) != major:
                continue
            used_minors.add(int(match.group(2)))

        minor = 9000
        while minor in used_minors:
            minor += 1
        return f"{major}.{minor}"

    def _build_reconcile_description(
        self,
        *,
        failed_task: dict[str, Any],
        failure_threshold: int,
        observed_attempts: int,
        failure_messages: list[str],
    ) -> str:
        """Build auto-generated reconcile task details."""
        source_task_id = int(failed_task["id"])
        source_task_number = str(failed_task["task_number"])
        source_title = str(failed_task["title"])

        normalized_failures: list[str] = []
        seen_failures: set[str] = set()
        for raw_message in failure_messages:
            message = str(raw_message or "").strip()
            if not message or message in seen_failures:
                continue
            normalized_failures.append(message)
            seen_failures.add(message)

        upstream = self._dependency_summary(
            source_task_id,
            relation="upstream",
        )
        downstream = self._dependency_summary(
            source_task_id,
            relation="downstream",
        )

        lines = [
            "Auto-generated reconcile task for repeated execution failures.",
            "",
            f"**Reconcile Source Task ID:** {source_task_id}",
            f"**Reconcile Source Task:** {source_task_number} - {source_title}",
            f"**Failure Threshold:** {failure_threshold}",
            f"**Observed Attempts:** {observed_attempts}",
            "",
            "**Failure Context:**",
        ]
        if normalized_failures:
            for idx, message in enumerate(normalized_failures, start=1):
                lines.append(f"- Failure {idx}: {message}")
        else:
            lines.append("- Failure details unavailable")

        lines.extend(
            [
                "",
                "**Dependency Context:**",
                f"- Upstream blockers: {upstream}",
                f"- Downstream blocked tasks: {downstream}",
            ]
        )
        return "\n".join(lines)

    def _dependency_summary(self, task_id: int, *, relation: str) -> str:
        """Return concise dependency context for one task."""
        if relation == "upstream":
            rows = self._conn.execute(
                """
                SELECT blocker.id, blocker.task_number, blocker.title, blocker.status
                FROM task_dependencies dep
                JOIN tasks blocker ON blocker.id = dep.blocker_task_id
                WHERE dep.blocked_task_id = ?
                """,
                (task_id,),
            ).fetchall()
        elif relation == "downstream":
            rows = self._conn.execute(
                """
                SELECT blocked.id, blocked.task_number, blocked.title, blocked.status
                FROM task_dependencies dep
                JOIN tasks blocked ON blocked.id = dep.blocked_task_id
                WHERE dep.blocker_task_id = ?
                """,
                (task_id,),
            ).fetchall()
        else:
            raise ValueError(f"Unknown dependency relation: {relation}")

        if not rows:
            return "none"

        records = [dict(row) for row in rows]
        records.sort(
            key=lambda record: self._task_sort_key(
                str(record["task_number"]),
                int(record["id"]),
            )
        )

        labels: list[str] = []
        for record in records:
            labels.append(
                f"{record['task_number']} ({record['status']}) {record['title']}",
            )
        return "; ".join(labels)

    @staticmethod
    def _normalize_file_targets(file_targets: list[str]) -> list[str]:
        """Normalize file target values into stable slash-delimited paths."""
        normalized_targets: list[str] = []
        seen: set[str] = set()
        for raw_target in file_targets:
            target = Store._normalize_file_target(raw_target)
            if target is None or target in seen:
                continue
            normalized_targets.append(target)
            seen.add(target)
        return normalized_targets

    @staticmethod
    def _normalize_file_target(raw_target: str) -> str | None:
        """Normalize one file target string."""
        value = str(raw_target).strip().strip("`").strip().strip("\"'")
        if not value:
            return None

        value = value.replace("\\", "/")
        parts = [part for part in value.split("/") if part and part != "."]
        if not parts:
            return None

        normalized = PurePosixPath(*parts).as_posix().strip()
        if not normalized or normalized == ".":
            return None
        return normalized

    @staticmethod
    def _task_is_overlap_safe(description: str) -> bool:
        """Return True when task metadata explicitly marks overlap as safe."""
        for raw_line in description.splitlines():
            match = _RE_TASK_METADATA_LINE.match(raw_line.strip())
            if match is None:
                continue
            key = re.sub(r"[^a-z0-9]+", "", match.group(1).lower())
            if key not in _OVERLAP_SAFE_METADATA_KEYS:
                continue
            value = re.sub(r"[^a-z0-9]+", "", match.group(2).lower())
            if value in _TRUTHY_METADATA_VALUES:
                return True
        return False

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
        self._conn.execute(
            f"DELETE FROM task_file_targets WHERE task_id IN ({placeholders})",
            task_ids,
        )
        self._conn.execute(
            f"DELETE FROM task_dependencies WHERE blocked_task_id IN ({placeholders}) "
            f"OR blocker_task_id IN ({placeholders})",
            [*task_ids, *task_ids],
        )
