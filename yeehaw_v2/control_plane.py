from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import ControlPlaneConfig
from .db import connect as connect_db
from .models import RuntimeKind, SessionHandle, SessionSpec
from .runtime import LocalPtyRuntimeAdapter, RuntimeRegistry, TmuxRuntimeAdapter
from .store import apply_latest_dispatcher_decision, record_usage_snapshot
from .usage_parser import parse_usage_snapshots


@dataclass(slots=True)
class TickStats:
    dispatched: int = 0
    completed: int = 0
    failed: int = 0
    stuck: int = 0


@dataclass(slots=True)
class BatchControlResult:
    task_rows_changed: int = 0
    sessions_ended: int = 0


class ControlPlane:
    _MAX_REASSIGN_ATTEMPTS = 3
    _LOOP_REPEAT_THRESHOLD = 4

    def __init__(self, config: ControlPlaneConfig) -> None:
        self.config = config
        self.conn = connect_db(config.db_path)
        self.runtimes = RuntimeRegistry()
        self.runtimes.register(TmuxRuntimeAdapter())
        self.runtimes.register(LocalPtyRuntimeAdapter())

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(max(0.1, self.config.poll_seconds))

    def tick(self) -> TickStats:
        stats = TickStats()
        self._reconcile_sessions(stats)
        self._dispatch_queued(stats)
        return stats

    def _session_handle_from_row(self, session_row: sqlite3.Row) -> SessionHandle:
        kind = RuntimeKind(str(session_row["runtime_kind"]))
        return SessionHandle(
            runtime_kind=kind,
            session_id=str(session_row["transport_session_id"]),
            target=str(session_row["transport_target"]),
            pid=None,
        )

    def _terminate_session_row(self, session_row: sqlite3.Row, reason: str) -> None:
        handle = self._session_handle_from_row(session_row)
        adapter = self.runtimes.get(handle.runtime_kind)
        try:
            adapter.terminate_session(handle)
        except Exception as exc:
            task_id = int(session_row["task_id"]) if session_row["task_id"] is not None else None
            self.conn.execute(
                """
                INSERT INTO alerts(task_id, level, kind, message, status, created_at)
                VALUES (?, 'warn', 'session_terminate_failed', ?, 'open', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
                """,
                (task_id, f"{reason}: {exc}"),
            )
        self.conn.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE id = ?
            """,
            (int(session_row["id"]),),
        )

    def pause_batch(self, batch_id: int) -> BatchControlResult:
        sessions = self.conn.execute(
            """
            SELECT s.id, s.task_id, s.project_id, s.runtime_kind, s.transport_session_id, s.transport_target
            FROM agent_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE t.batch_id = ? AND s.status IN ('starting', 'active', 'paused')
            ORDER BY s.id ASC
            """,
            (batch_id,),
        ).fetchall()
        for row in sessions:
            self._terminate_session_row(row, reason="pause_batch")
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'paused',
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE batch_id = ? AND status IN ('queued', 'running', 'awaiting_input')
            """,
            (batch_id,),
        )
        self.conn.execute(
            """
            UPDATE git_worktrees
            SET state = 'paused'
            WHERE task_id IN (
                SELECT id FROM tasks WHERE batch_id = ?
            ) AND state = 'active'
            """,
            (batch_id,),
        )
        self.conn.commit()
        return BatchControlResult(task_rows_changed=int(cur.rowcount), sessions_ended=len(sessions))

    def resume_batch(self, batch_id: int) -> BatchControlResult:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'queued',
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE batch_id = ? AND status = 'paused'
            """,
            (batch_id,),
        )
        self.conn.execute(
            """
            UPDATE git_worktrees
            SET state = 'queued'
            WHERE task_id IN (
                SELECT id FROM tasks WHERE batch_id = ?
            ) AND state = 'paused'
            """,
            (batch_id,),
        )
        self.conn.commit()
        return BatchControlResult(task_rows_changed=int(cur.rowcount), sessions_ended=0)

    def preempt_batch(self, batch_id: int) -> BatchControlResult:
        sessions = self.conn.execute(
            """
            SELECT s.id, s.task_id, s.project_id, s.runtime_kind, s.transport_session_id, s.transport_target
            FROM agent_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE t.batch_id = ? AND s.status IN ('starting', 'active', 'paused')
            ORDER BY s.id ASC
            """,
            (batch_id,),
        ).fetchall()
        for row in sessions:
            self._terminate_session_row(row, reason="preempt_batch")
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'queued',
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE batch_id = ? AND status IN ('running', 'paused', 'awaiting_input')
            """,
            (batch_id,),
        )
        self.conn.execute(
            """
            UPDATE git_worktrees
            SET state = 'preempted'
            WHERE task_id IN (
                SELECT id FROM tasks WHERE batch_id = ?
            ) AND state = 'active'
            """,
            (batch_id,),
        )
        self.conn.commit()
        return BatchControlResult(task_rows_changed=int(cur.rowcount), sessions_ended=len(sessions))

    def reply_to_task(self, task_id: int, text: str) -> None:
        message = text.strip()
        if not message:
            raise ValueError("reply text cannot be empty")
        task_row = self.conn.execute(
            """
            SELECT id, status
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise ValueError(f"task not found: {task_id}")
        session_row = self.conn.execute(
            """
            SELECT id, task_id, project_id, runtime_kind, transport_session_id, transport_target, status, started_at
            FROM agent_sessions
            WHERE task_id = ? AND status IN ('starting', 'active', 'paused')
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if session_row is None:
            raise ValueError(f"no active session found for task {task_id}")

        handle = self._session_handle_from_row(session_row)
        adapter = self.runtimes.get(handle.runtime_kind)
        adapter.send_user_input(handle, message)
        self.conn.execute(
            """
            INSERT INTO operator_messages(session_id, direction, body, created_at)
            VALUES (?, 'to_agent', ?, (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
            """,
            (int(session_row["id"]), message),
        )
        self.conn.execute(
            """
            UPDATE agent_sessions
            SET status = 'active',
                last_heartbeat_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE id = ?
            """,
            (int(session_row["id"]),),
        )
        self.conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                blocked_question = NULL,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE id = ?
            """,
            (task_id,),
        )
        self.conn.commit()

    def _scheduler_limits(self) -> tuple[int, int]:
        row = self.conn.execute(
            """
            SELECT max_global_sessions, max_project_sessions
            FROM scheduler_config
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return (20, 10)
        return int(row["max_global_sessions"]), int(row["max_project_sessions"])

    def _scheduler_policy(self) -> tuple[int, bool]:
        row = self.conn.execute(
            """
            SELECT stuck_minutes, auto_reassign
            FROM scheduler_config
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return (12, True)
        return (int(row["stuck_minutes"]), bool(int(row["auto_reassign"])))

    @staticmethod
    def _slug(text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()
        return slug or "project"

    def _run_git(self, repo_root: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "unknown git error"
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
        return proc.stdout.strip()

    def _prepare_task_worktree(
        self,
        project_name: str,
        project_root: Path,
        task_id: int,
        batch_id: int | None,
        attempt_no: int,
    ) -> tuple[Path, str, str]:
        base_sha = self._run_git(project_root, "rev-parse", "HEAD")
        project_slug = self._slug(project_name)
        batch_value = 0 if batch_id is None else int(batch_id)
        branch_name = f"yeehaw/{project_slug}/b{batch_value}-t{task_id}-a{attempt_no}"
        worktrees_root = (self.config.db_path.parent / "worktrees").resolve()
        worktrees_root.mkdir(parents=True, exist_ok=True)
        worktree_path = (worktrees_root / f"{project_slug}-b{batch_value}-t{task_id}-a{attempt_no}").resolve()
        if worktree_path.exists():
            subprocess.run(
                ["git", "-C", str(project_root), "worktree", "remove", "--force", str(worktree_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(worktree_path, ignore_errors=True)
        self._run_git(project_root, "worktree", "add", "-B", branch_name, str(worktree_path), base_sha)
        return worktree_path, branch_name, base_sha

    @staticmethod
    def _parse_ts(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _normalize_output(output: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return "\n".join(lines[-40:])

    @staticmethod
    def _looks_meaningful(output: str) -> bool:
        low = output.lower()
        markers = [
            "[[yeehaw_done",
            "completed",
            "done",
            "implemented",
            "fixed",
            "tests passed",
            "all tests pass",
            "wrote",
            "created",
            "updated",
            "summary:",
            "artifacts:",
        ]
        return any(marker in low for marker in markers)

    @staticmethod
    def _interactive_trap_reason(output: str) -> str | None:
        patterns = [
            r"\[sudo\]\s+password",
            r"password\s+for\s+",
            r"press\s+any\s+key",
            r"\bconfirm\b.*\[[yYnN]/?[yYnN]?\]",
            r"\bcontinue\?\s*\[[yYnN]/?[yYnN]?\]",
            r"enter\s+passphrase",
            r"waiting\s+for\s+input",
            r"select\s+an\s+option",
        ]
        low = output.lower()
        for pattern in patterns:
            if re.search(pattern, low):
                return "interactive command trap detected in session output"
        return None

    def _record_session_watch(
        self,
        session_row: sqlite3.Row,
        output: str,
        meaningful: bool,
    ) -> sqlite3.Row:
        session_id = int(session_row["id"])
        previous = self.conn.execute(
            """
            SELECT session_id, last_fingerprint, repeat_count, last_output_at, last_meaningful_at
            FROM session_watch_state
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        normalized = self._normalize_output(output)
        fingerprint = hashlib.sha1(normalized.encode("utf-8")).hexdigest() if normalized else ""
        repeat_count = 0
        if previous is not None and previous["last_fingerprint"] and fingerprint:
            repeat_count = int(previous["repeat_count"]) + 1 if str(previous["last_fingerprint"]) == fingerprint else 1
        elif fingerprint:
            repeat_count = 1
        else:
            repeat_count = int(previous["repeat_count"]) if previous is not None else 0

        last_output_at = (
            self.conn.execute("SELECT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) AS ts").fetchone()["ts"]
            if output.strip()
            else (previous["last_output_at"] if previous is not None else None)
        )
        if meaningful:
            last_meaningful_at = self.conn.execute("SELECT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) AS ts").fetchone()["ts"]
        elif previous is not None and previous["last_meaningful_at"] is not None:
            last_meaningful_at = previous["last_meaningful_at"]
        else:
            last_meaningful_at = session_row["started_at"]

        self.conn.execute(
            """
            INSERT INTO session_watch_state(session_id, last_fingerprint, repeat_count, last_output_at, last_meaningful_at, updated_at)
            VALUES (?, ?, ?, ?, ?, (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
            ON CONFLICT(session_id) DO UPDATE SET
                last_fingerprint = excluded.last_fingerprint,
                repeat_count = excluded.repeat_count,
                last_output_at = excluded.last_output_at,
                last_meaningful_at = excluded.last_meaningful_at,
                updated_at = excluded.updated_at
            """,
            (session_id, fingerprint, repeat_count, last_output_at, last_meaningful_at),
        )
        row = self.conn.execute(
            """
            SELECT session_id, last_fingerprint, repeat_count, last_output_at, last_meaningful_at
            FROM session_watch_state
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to persist session watch state for session_id={session_id}")
        return row

    def _stuck_reason(
        self,
        session_row: sqlite3.Row,
        task_row: sqlite3.Row,
        output: str,
        watch_row: sqlite3.Row,
        stuck_minutes: int,
    ) -> str | None:
        trap = self._interactive_trap_reason(output)
        if trap:
            return trap

        repeat_count = int(watch_row["repeat_count"] or 0)
        if repeat_count >= self._LOOP_REPEAT_THRESHOLD and len(self._normalize_output(output)) >= 80:
            return f"loop detected: repeated output fingerprint x{repeat_count}"

        now = datetime.now(timezone.utc)
        last_meaningful = self._parse_ts(str(watch_row["last_meaningful_at"]) if watch_row["last_meaningful_at"] else None)
        if last_meaningful is None:
            last_meaningful = self._parse_ts(str(session_row["started_at"]) if session_row["started_at"] else None)
        if last_meaningful is None:
            return None
        idle_minutes = (now - last_meaningful).total_seconds() / 60.0
        if idle_minutes >= max(1, stuck_minutes):
            return f"no meaningful progress for {idle_minutes:.1f} minutes"
        return None

    def _handle_stuck(
        self,
        session_row: sqlite3.Row,
        task_row: sqlite3.Row,
        reason: str,
        auto_reassign: bool,
        stats: TickStats,
    ) -> None:
        task_id = int(task_row["id"])
        attempt_count = int(task_row["attempt_count"] or 0)
        self.conn.execute(
            """
            INSERT INTO alerts(task_id, level, kind, message, status, created_at)
            VALUES (?, 'warn', 'task_stuck', ?, 'open', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
            """,
            (task_id, reason),
        )
        stats.stuck += 1

        if auto_reassign and attempt_count < self._MAX_REASSIGN_ATTEMPTS:
            self._terminate_session_row(session_row, reason="stuck_reassign")
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    blocked_question = ?,
                    assigned_agent = NULL,
                    updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                WHERE id = ?
                """,
                (reason, task_id),
            )
            self.conn.execute(
                """
                UPDATE git_worktrees
                SET state = 'stuck_reassign'
                WHERE task_id = ? AND state = 'active'
                """,
                (task_id,),
            )
        else:
            self._terminate_session_row(session_row, reason="stuck_failed")
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    blocked_question = ?,
                    updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                WHERE id = ?
                """,
                (reason, task_id),
            )
            self.conn.execute(
                """
                UPDATE git_worktrees
                SET state = 'failed'
                WHERE task_id = ? AND state = 'active'
                """,
                (task_id,),
            )
            stats.failed += 1
        self.conn.commit()

    def _count_active(self, project_id: int | None = None) -> int:
        statuses = ("starting", "active", "paused")
        if project_id is None:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS c FROM agent_sessions WHERE status IN ({','.join('?' for _ in statuses)})",
                statuses,
            ).fetchone()
        else:
            row = self.conn.execute(
                f"""
                SELECT COUNT(*) AS c
                FROM agent_sessions
                WHERE project_id = ? AND status IN ({','.join('?' for _ in statuses)})
                """,
                (project_id, *statuses),
            ).fetchone()
        return int(row["c"]) if row else 0

    def _dispatch_queued(self, stats: TickStats) -> None:
        max_global, max_project = self._scheduler_limits()
        active_global = self._count_active()
        if active_global >= max_global:
            return

        queued = self.conn.execute(
            """
            SELECT t.id, t.project_id, t.title, t.runtime_kind, t.preferred_agent, p.root_path
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.status = 'queued'
            ORDER BY t.priority DESC, t.id ASC
            LIMIT 200
            """
        ).fetchall()

        for row in queued:
            if active_global >= max_global:
                break
            project_id = int(row["project_id"])
            if self._count_active(project_id) >= max_project:
                continue
            dispatched = self._dispatch_one(row)
            if dispatched:
                stats.dispatched += 1
                active_global += 1
            else:
                stats.failed += 1

    def _dispatch_one(self, row: sqlite3.Row) -> bool:
        task_id = int(row["id"])
        self._apply_dispatcher(task_id)
        refreshed = self.conn.execute(
            """
            SELECT t.id, t.project_id, t.batch_id, t.attempt_count, t.title, t.runtime_kind, t.preferred_agent,
                   p.root_path, p.name AS project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if refreshed is None:
            return False

        runtime_kind = RuntimeKind(str(refreshed["runtime_kind"] or self.config.default_runtime))
        command = str(refreshed["preferred_agent"] or "codex")
        project_root = Path(str(refreshed["root_path"]))
        attempt_no = int(refreshed["attempt_count"] or 0) + 1
        worktree_path = project_root
        branch_name: str | None = None
        base_sha: str | None = None
        try:
            worktree_path, branch_name, base_sha = self._prepare_task_worktree(
                project_name=str(refreshed["project_name"]),
                project_root=project_root,
                task_id=int(refreshed["id"]),
                batch_id=int(refreshed["batch_id"]) if refreshed["batch_id"] is not None else None,
                attempt_no=attempt_no,
            )
            self.conn.execute(
                """
                INSERT INTO git_worktrees(task_id, path, branch_name, base_sha, state, created_at)
                VALUES (?, ?, ?, ?, 'active', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
                """,
                (int(refreshed["id"]), str(worktree_path), branch_name, base_sha),
            )
        except Exception as exc:
            self.conn.execute(
                """
                INSERT INTO alerts(task_id, level, kind, message, status, created_at)
                VALUES (?, 'warn', 'worktree_prepare_failed', ?, 'open', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
                """,
                (int(refreshed["id"]), str(exc)),
            )
        spec = SessionSpec(
            project_id=int(refreshed["project_id"]),
            task_id=int(refreshed["id"]),
            project_root=worktree_path,
            title=str(refreshed["title"]),
            command=command,
            runtime_kind=runtime_kind,
        )
        try:
            adapter = self.runtimes.get(runtime_kind)
            handle = adapter.start_session(spec)
        except Exception as exc:
            self.conn.execute(
                """
                INSERT INTO alerts(task_id, level, kind, message, status, created_at)
                VALUES (?, 'error', 'dispatch_failed', ?, 'open', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
                """,
                (int(refreshed["id"]), str(exc)),
            )
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    blocked_question = ?,
                    updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                WHERE id = ?
                """,
                (f"dispatch failed: {exc}", int(refreshed["id"])),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            f"""
            INSERT INTO agent_sessions(task_id, project_id, runtime_kind, transport_session_id, transport_target, status, started_at, last_heartbeat_at)
            VALUES (?, ?, ?, ?, ?, 'active', (strftime('%Y-%m-%dT%H:%M:%fZ','now')), (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
            """,
            (spec.task_id, spec.project_id, runtime_kind.value, handle.session_id, handle.target),
        )
        self.conn.execute(
            f"""
            UPDATE tasks
            SET status = 'running',
                assigned_agent = ?,
                attempt_count = attempt_count + 1,
                blocked_question = NULL,
                branch_name = ?,
                worktree_path = ?,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            WHERE id = ?
            """,
            (command, branch_name, str(worktree_path), spec.task_id),
        )
        self.conn.commit()
        return True

    def _apply_dispatcher(self, task_id: int) -> None:
        try:
            apply_latest_dispatcher_decision(self.conn, task_id)
        except Exception as exc:
            self.conn.execute(
                """
                INSERT INTO alerts(task_id, level, kind, message, status, created_at)
                VALUES (?, 'warn', 'dispatcher_apply_failed', ?, 'open', (strftime('%Y-%m-%dT%H:%M:%fZ','now')))
                """,
                (task_id, str(exc)),
            )
            self.conn.commit()

    def _ingest_usage(self, session_row: sqlite3.Row, task_id: int | None, adapter, handle: SessionHandle) -> str:
        try:
            output = adapter.capture_output(handle, lines=500)
        except Exception:
            return ""
        if not output.strip():
            return output
        snapshots = parse_usage_snapshots(output)
        if not snapshots:
            return output
        session_db_id = int(session_row["id"])
        for snapshot in snapshots:
            record_usage_snapshot(
                self.conn,
                session_id=session_db_id,
                task_id=task_id,
                provider=snapshot.provider,
                model=snapshot.model,
                input_tokens=snapshot.input_tokens,
                output_tokens=snapshot.output_tokens,
                cost_usd=snapshot.cost_usd,
                source="runtime_parse",
            )
        return output

    def _reconcile_sessions(self, stats: TickStats) -> None:
        stuck_minutes, auto_reassign = self._scheduler_policy()
        active = self.conn.execute(
            """
            SELECT id, task_id, project_id, runtime_kind, transport_session_id, transport_target, status, started_at
            FROM agent_sessions
            WHERE status IN ('starting', 'active', 'paused')
            ORDER BY id ASC
            """
        ).fetchall()
        for session in active:
            handle = self._session_handle_from_row(session)
            adapter = self.runtimes.get(handle.runtime_kind)
            task_id = int(session["task_id"]) if session["task_id"] is not None else None
            output = self._ingest_usage(session, task_id, adapter, handle)
            alive = adapter.is_session_alive(handle)
            if alive:
                if task_id is not None:
                    task_row = self.conn.execute(
                        """
                        SELECT id, status, attempt_count
                        FROM tasks
                        WHERE id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                    if task_row is not None and str(task_row["status"]) == "running":
                        meaningful = self._looks_meaningful(output)
                        watch = self._record_session_watch(session, output, meaningful=meaningful)
                        reason = self._stuck_reason(session, task_row, output, watch, stuck_minutes=stuck_minutes)
                        if reason is not None:
                            self._handle_stuck(session, task_row, reason=reason, auto_reassign=auto_reassign, stats=stats)
                            continue
                self.conn.execute(
                    """
                    UPDATE agent_sessions
                    SET last_heartbeat_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    WHERE id = ?
                    """,
                    (int(session["id"]),),
                )
                self.conn.commit()
                continue
            self.conn.execute(
                f"""
                UPDATE agent_sessions
                SET status = 'ended',
                    ended_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                WHERE id = ?
                """,
                (int(session["id"]),),
            )
            if task_id is not None:
                self.conn.execute(
                    f"""
                    UPDATE tasks
                    SET status = 'completed',
                        updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    WHERE id = ? AND status = 'running'
                    """,
                    (task_id,),
                )
                self.conn.execute(
                    """
                    UPDATE git_worktrees
                    SET state = 'completed'
                    WHERE task_id = ? AND state = 'active'
                    """,
                    (task_id,),
                )
                stats.completed += 1
            self.conn.commit()
