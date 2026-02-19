from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .agents import DEFAULT_PROFILES, resolve_command
from .roadmap import RoadmapDef, StageDef, TrackDef, load_roadmap
from .runner import _extract_question, _marker_followed_by, _parse_summary_and_artifacts
from .tmux import TmuxError, capture_pane, ensure_session, ensure_window, kill_session, send_keys, send_text


SUMMARY_RE = re.compile(r"^Summary:\s*$", re.IGNORECASE)
ARTIFACTS_RE = re.compile(r"^Artifacts:\s*$", re.IGNORECASE)
INTERACTIVE_TRAP_RE = re.compile(
    r"(password|passphrase|sudo|are you sure|press enter|interactive|select an option|\(y/n\)|\[y/N\]|Continue\?)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class SchedulerStats:
    dispatched: int = 0
    completed: int = 0
    awaiting_input: int = 0
    reassigned: int = 0
    failed: int = 0


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "task"


def _safe_session_name(prefix: str, project_name: str, task_id: int) -> str:
    project_slug = _safe_slug(project_name)[:20]
    suffix = uuid.uuid4().hex[:6]
    return f"{prefix}-{project_slug}-t{task_id}-{suffix}"[:60]


def _branch_name(project_name: str, task_id: int, title: str) -> str:
    return f"yeehaw/{_safe_slug(project_name)}/task-{task_id}-{_safe_slug(title)[:28]}"


def _done_marker(task_id: int) -> str:
    return f"[[YEEHAW_DONE TASK-{task_id}]]"


def _input_marker(task_id: int) -> str:
    return f"[[YEEHAW_NEEDS_INPUT TASK-{task_id}]]"


def _progress_marker(task_id: int) -> str:
    return f"[[YEEHAW_PROGRESS TASK-{task_id}]]"


def _pane_hash(pane: str) -> str:
    tail = pane[-8000:]
    return hashlib.sha1(tail.encode("utf-8", errors="ignore")).hexdigest()


def _fresh_question_marker(pane_text: str, marker: str) -> bool:
    marker_pos = pane_text.rfind(marker)
    if marker_pos == -1:
        return False
    tail = pane_text[marker_pos + len(marker) :]
    non_empty = [line.strip() for line in tail.splitlines() if line.strip()]
    if not non_empty:
        return False
    if not non_empty[0].lower().startswith("question:"):
        return False
    # If additional lines exist, this is likely stale history (e.g., echoed operator reply).
    return len(non_empty) == 1


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    value = ts.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def _elapsed_minutes(ts: str | None) -> float:
    parsed = _parse_iso_utc(ts)
    if parsed is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _run_git(project_root: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", project_root, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return proc.stdout.strip()


def _task_worktree_path(project_root: str, task_id: int, attempt_count: int) -> Path:
    base = Path(project_root) / ".yeehaw" / "worktrees"
    return base / f"task-{task_id}-a{attempt_count}"


def _prepare_task_worktree(project_root: str, task_id: int, attempt_count: int, branch_name: str, base_sha: str) -> str:
    worktree_path = _task_worktree_path(project_root, task_id, attempt_count)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        try:
            _run_git(project_root, "worktree", "remove", "--force", str(worktree_path))
        except RuntimeError:
            shutil.rmtree(worktree_path, ignore_errors=True)
    _run_git(project_root, "worktree", "prune")
    _run_git(
        project_root,
        "worktree",
        "add",
        "--force",
        "-B",
        branch_name,
        str(worktree_path),
        base_sha,
    )
    return str(worktree_path)


def _cleanup_task_worktree(project_root: str, worktree_path: str | None) -> None:
    if not worktree_path:
        return
    root = Path(project_root).resolve()
    candidate = Path(worktree_path).resolve()
    if candidate == root:
        return
    try:
        _run_git(project_root, "worktree", "remove", "--force", str(candidate))
    except RuntimeError:
        shutil.rmtree(candidate, ignore_errors=True)
    try:
        _run_git(project_root, "worktree", "prune")
    except RuntimeError:
        return


def _choose_agent(preferred_agent: str | None, attempt_count: int) -> str:
    if preferred_agent and preferred_agent.strip():
        return preferred_agent.strip().lower()
    pool = tuple(DEFAULT_PROFILES.keys()) or ("codex",)
    return pool[attempt_count % len(pool)]


def _next_agent(current: str) -> str:
    pool = tuple(DEFAULT_PROFILES.keys()) or ("codex",)
    current_key = current.strip().lower()
    if current_key not in pool:
        return pool[0]
    idx = pool.index(current_key)
    return pool[(idx + 1) % len(pool)]


def _stage_prompt(
    project_name: str,
    project_root: str,
    branch_name: str,
    global_guidelines: str,
    task_title: str,
    task_description: str,
    done_marker: str,
    input_marker: str,
    progress_marker: str,
) -> str:
    return (
        f"Project: {project_name}\n"
        f"Project root: {project_root}\n"
        f"Task: {task_title}\n"
        f"Branch policy: work ONLY on branch '{branch_name}'.\n"
        "\n"
        "Hard repository safety constraints:\n"
        f"- Ensure current branch is exactly: {branch_name}\n"
        "- You may commit only on that branch.\n"
        "- Never merge. Never commit on another branch.\n"
        "- Never change git remotes.\n"
        "\n"
        "Global guidelines:\n"
        f"{global_guidelines.strip() or '(none)'}\n"
        "\n"
        "Task objective:\n"
        f"{task_description.strip() or task_title}\n"
        "\n"
        "Execution policy:\n"
        "- Work autonomously and incrementally.\n"
        f"- Periodically print progress marker: {progress_marker}\n"
        "- If blocked, ask one precise question.\n"
        f"- Blocked marker: {input_marker}\n"
        "- Then one line starting with: Question:\n"
        f"- Completion marker: {done_marker}\n"
        "- Then print:\n"
        "Summary:\n"
        "- up to 5 bullets\n"
        "Artifacts:\n"
        "- relative/path.ext\n"
        "- commit:<sha>\n"
    )


def _planner_prompt(
    project_name: str,
    project_root: str,
    task_list_text: str,
    output_path: str,
    done_marker: str,
) -> str:
    return (
        "You are a roadmap planner for the yeehaw agent harness.\n"
        f"Project: {project_name}\n"
        f"Project root: {project_root}\n"
        "\n"
        "Transform the free-form task list below into a clear markdown roadmap using this exact phase format:\n"
        "## 2. Execution Phases\n"
        "\n"
        "### Phase N: <Title>\n"
        "**Status:** TODO\n"
        "**Token Budget:** Low|Medium|High\n"
        "**Prerequisites:** None|Phase X\n"
        "\n"
        "**Objective:**\n"
        "<short objective paragraph>\n"
        "\n"
        "**Tasks:**\n"
        "- [ ] Task item\n"
        "\n"
        "**Verification:**\n"
        "- [ ] Check item\n"
        "\n"
        "---\n"
        "\n"
        "Write the generated roadmap to this path:\n"
        f"{output_path}\n"
        "\n"
        "Free-form task list:\n"
        f"{task_list_text.strip()}\n"
        "\n"
        f"When written successfully, print exactly: {done_marker}\n"
        "Then print:\n"
        "Summary:\n"
        "- one short bullet\n"
    )


def create_batch_from_roadmap(
    conn,
    project_id: int,
    batch_name: str,
    roadmap: RoadmapDef,
    source_text: str,
    roadmap_path: str,
    priority: str = "medium",
    batch_id: int | None = None,
) -> int:
    if batch_id is None:
        batch_id = db.create_task_batch(
            conn,
            project_id=project_id,
            name=batch_name,
            source_text=source_text,
            roadmap_path=roadmap_path,
            roadmap_text=roadmap.raw_text,
            status="ready",
            priority=priority,
        )
    else:
        conn.execute(
            f"""
            UPDATE task_batches
            SET name = ?, source_text = ?, roadmap_path = ?, roadmap_text = ?, status = 'ready', priority = ?, updated_at = ({db.utc_now()})
            WHERE id = ?
            """,
            (batch_name, source_text, roadmap_path, roadmap.raw_text, priority, batch_id),
        )
        conn.commit()

    for track in roadmap.tracks:
        for stage in track.stages:
            db.create_task(
                conn,
                batch_id=batch_id,
                project_id=project_id,
                title=stage.title,
                description=stage.goal + ("\n\n" + stage.instructions if stage.instructions else ""),
                priority=priority,
                preferred_agent=track.agent,
                track_id=track.id,
                stage_id=stage.id,
            )

    db.add_roadmap_revision(
        conn,
        project_id=project_id,
        batch_id=batch_id,
        path=roadmap_path,
        source="planner",
        raw_text=roadmap.raw_text,
    )
    db.set_task_batch_status(conn, batch_id, "queued")
    return batch_id


def create_batch_from_task_list(
    project_name: str,
    batch_name: str,
    task_list_text: str,
    planner_agent: str = "codex",
    db_path: str | Path | None = None,
    timeout_minutes: int = 20,
    session_prefix: str = "yeehaw-planner",
) -> int:
    conn = db.connect(db_path)
    project = db.get_project(conn, project_name)
    if project is None:
        raise ValueError(f"Project '{project_name}' not found.")

    project_id = int(project["id"])
    project_root = str(project["root_path"])
    planner_dir = Path(project_root) / ".yeehaw" / "roadmaps"
    planner_dir.mkdir(parents=True, exist_ok=True)

    draft_batch_id = db.create_task_batch(
        conn,
        project_id=project_id,
        name=batch_name,
        source_text=task_list_text,
        status="planning",
        priority="medium",
    )

    roadmap_path = planner_dir / f"batch-{draft_batch_id}.roadmap.md"
    marker = f"[[YEEHAW_DONE PLAN-{draft_batch_id}]]"
    command, warmup_seconds = resolve_command(planner_agent, None)
    session = _safe_session_name(session_prefix, project_name, draft_batch_id)
    target = f"{session}:planner.0"
    prompt = _planner_prompt(
        project_name=project_name,
        project_root=project_root,
        task_list_text=task_list_text,
        output_path=str(roadmap_path),
        done_marker=marker,
    )

    try:
        ensure_session(session, project_root)
        ensure_window(session, "planner", project_root, command)
        time.sleep(warmup_seconds)
        send_text(target, prompt, press_enter=True)
        time.sleep(0.25)
        baseline_marker_count = capture_pane(target).count(marker)

        deadline = time.monotonic() + (max(1, timeout_minutes) * 60)
        done = False
        while time.monotonic() <= deadline:
            pane = capture_pane(target)
            if pane.count(marker) > baseline_marker_count or _marker_followed_by(pane, marker, "Summary:"):
                done = True
                break
            time.sleep(1.0)

        if not done:
            raise TimeoutError(f"planner timeout after {timeout_minutes} minute(s)")
        if not roadmap_path.exists():
            raise RuntimeError(f"planner did not write roadmap: {roadmap_path}")

        roadmap = load_roadmap(roadmap_path)
        batch_id = create_batch_from_roadmap(
            conn,
            project_id=project_id,
            batch_name=batch_name,
            roadmap=roadmap,
            source_text=task_list_text,
            roadmap_path=str(roadmap_path),
            priority="medium",
            batch_id=draft_batch_id,
        )
        return batch_id
    except Exception:
        db.set_task_batch_status(conn, draft_batch_id, "failed")
        raise
    finally:
        kill_session(session)


def replan_batch_from_roadmap(
    batch_id: int,
    roadmap_path: str | Path,
    db_path: str | Path | None = None,
) -> None:
    conn = db.connect(db_path)
    batch = db.get_task_batch(conn, batch_id)
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")

    roadmap = load_roadmap(roadmap_path)
    conn.execute(
        f"""
        UPDATE tasks
        SET status = 'canceled', updated_at = ({db.utc_now()})
        WHERE batch_id = ? AND status IN ('queued', 'stuck')
        """,
        (batch_id,),
    )
    conn.commit()

    for track in roadmap.tracks:
        for stage in track.stages:
            db.create_task(
                conn,
                batch_id=batch_id,
                project_id=int(batch["project_id"]),
                title=stage.title,
                description=stage.goal + ("\n\n" + stage.instructions if stage.instructions else ""),
                priority=str(batch["priority"]),
                preferred_agent=track.agent,
                track_id=track.id,
                stage_id=stage.id,
            )
    db.update_task_batch_roadmap(conn, batch_id, str(Path(roadmap_path).resolve()), roadmap.raw_text)
    db.add_roadmap_revision(
        conn,
        project_id=int(batch["project_id"]),
        batch_id=batch_id,
        path=str(Path(roadmap_path).resolve()),
        source="manual-edit",
        raw_text=roadmap.raw_text,
    )
    db.set_task_batch_status(conn, batch_id, "queued")


class GlobalScheduler:
    def __init__(
        self,
        db_path: str | Path | None = None,
        poll_seconds: float = 2.0,
        session_prefix: str = "yeehaw-task",
        max_attempts: int = 4,
    ) -> None:
        self.conn = db.connect(db_path)
        self.poll_seconds = max(0.5, poll_seconds)
        self.session_prefix = session_prefix
        self.max_attempts = max(2, max_attempts)

    def tick(self) -> SchedulerStats:
        stats = SchedulerStats()
        self._dispatch_queued(stats)
        self._monitor_active(stats)
        return stats

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(self.poll_seconds)

    def reply_to_task(self, task_id: int, answer: str) -> None:
        task = db.get_task(self.conn, task_id)
        if task is None:
            raise ValueError(f"task {task_id} not found")
        if str(task["status"]) != "awaiting_input":
            raise ValueError(f"task {task_id} is not awaiting input")
        target = str(task["tmux_target"] or "").strip()
        if not target:
            raise RuntimeError(f"task {task_id} has no tmux target")
        question = str(task["blocked_question"] or "")
        db.save_operator_reply(self.conn, task_id, question=question, answer=answer)
        send_text(target, answer.strip(), press_enter=True)
        db.set_task_resume_ready(self.conn, task_id)
        db.add_task_event(self.conn, task_id, "info", "Operator reply sent; task resumed")

    def pause_task(self, task_id: int) -> None:
        task = db.get_task(self.conn, task_id)
        if task is None:
            raise ValueError(f"task {task_id} not found")
        status = str(task["status"])
        if status not in {"running", "dispatching", "stuck", "awaiting_input"}:
            return
        target = str(task["tmux_target"] or "").strip()
        if target:
            send_keys(target, "C-c")
        _cleanup_task_worktree(str(task["project_root"]), str(task["worktree_path"] or ""))
        db.set_task_state(self.conn, task_id, "queued", blocked_question="")
        db.add_task_event(self.conn, task_id, "warn", "Task preempted and re-queued")

    def _dispatch_queued(self, stats: SchedulerStats) -> None:
        cfg = db.scheduler_config(self.conn)
        max_global = int(cfg["max_global_sessions"])
        max_project = int(cfg["max_project_sessions"])
        active_global = db.count_active_tasks(self.conn)
        if active_global >= max_global:
            return

        for task in db.next_queued_tasks(self.conn, limit=200):
            if active_global >= max_global:
                break
            project_id = int(task["project_id"])
            active_project = db.count_active_tasks(self.conn, project_id=project_id)
            if active_project >= max_project:
                continue
            self._dispatch_task(task)
            stats.dispatched += 1
            active_global += 1

    def _dispatch_task(self, task) -> None:
        task_id = int(task["id"])
        full_task = db.get_task(self.conn, task_id)
        if full_task is None:
            return
        project_name = str(full_task["project_name"])
        project_root = str(full_task["project_root"])
        title = str(full_task["title"])
        description = str(full_task["description"] or "")
        assigned_agent = _choose_agent(full_task["preferred_agent"], int(full_task["attempt_count"]))
        branch_name = _branch_name(project_name, task_id, title)
        session_name = _safe_session_name(self.session_prefix, project_name, task_id)
        command, warmup_seconds = resolve_command(assigned_agent, None)
        target = f"{session_name}:task.0"

        base_sha = _run_git(project_root, "rev-parse", "HEAD")
        attempt_no = int(full_task["attempt_count"]) + 1
        worktree_path = _prepare_task_worktree(
            project_root=project_root,
            task_id=task_id,
            attempt_count=attempt_no,
            branch_name=branch_name,
            base_sha=base_sha,
        )

        db.mark_task_dispatching(
            self.conn,
            task_id=task_id,
            assigned_agent=assigned_agent,
            branch_name=branch_name,
            worktree_path=worktree_path,
            base_sha=base_sha,
            tmux_session=session_name,
            tmux_target=target,
        )
        session_id = db.create_agent_session(
            self.conn,
            task_id=task_id,
            project_id=int(full_task["project_id"]),
            agent=assigned_agent,
            status="dispatching",
            tmux_session=session_name,
            tmux_target=target,
        )

        prompt = _stage_prompt(
            project_name=project_name,
            project_root=worktree_path,
            branch_name=branch_name,
            global_guidelines=str(full_task["guidelines"] or ""),
            task_title=title,
            task_description=description,
            done_marker=_done_marker(task_id),
            input_marker=_input_marker(task_id),
            progress_marker=_progress_marker(task_id),
        )

        ensure_session(session_name, worktree_path)
        ensure_window(session_name, "task", worktree_path, command)
        time.sleep(warmup_seconds)
        send_text(target, prompt, press_enter=True)
        db.set_task_state(self.conn, task_id, "running")
        db.set_agent_session_status(self.conn, session_id, "running")
        db.add_task_event(
            self.conn,
            task_id,
            "info",
            f"Dispatched to agent={assigned_agent} session={session_name} branch={branch_name}",
        )

    def _monitor_active(self, stats: SchedulerStats) -> None:
        cfg = db.scheduler_config(self.conn)
        stuck_minutes = int(cfg["default_stuck_minutes"])
        auto_reassign = int(cfg["auto_reassign"]) == 1

        active = self.conn.execute(
            """
            SELECT t.id
            FROM tasks t
            WHERE t.status IN ('running', 'dispatching', 'awaiting_input', 'stuck')
            ORDER BY t.id ASC
            """
        ).fetchall()
        for row in active:
            task_id = int(row["id"])
            task = db.get_task(self.conn, task_id)
            if task is None:
                continue
            status = str(task["status"])
            if status in {"awaiting_input"}:
                continue
            if status == "stuck" and not auto_reassign:
                continue
            if not str(task["tmux_target"] or "").strip():
                continue
            try:
                self._monitor_one(task, stuck_minutes=stuck_minutes, auto_reassign=auto_reassign, stats=stats)
            except TmuxError as exc:
                db.set_task_state(self.conn, task_id, "failed", finished=True)
                db.add_task_event(self.conn, task_id, "error", f"tmux failure: {exc}")
                db.create_alert(
                    self.conn,
                    level="error",
                    kind="tmux",
                    message=f"Task {task_id} failed due to tmux error: {exc}",
                    task_id=task_id,
                    project_id=int(task["project_id"]),
                )
                stats.failed += 1

    def _monitor_one(self, task, stuck_minutes: int, auto_reassign: bool, stats: SchedulerStats) -> None:
        task_id = int(task["id"])
        target = str(task["tmux_target"])
        pane = capture_pane(target)
        pane_hash = _pane_hash(pane)
        old_hash = str(task["last_output_hash"] or "")
        changed = pane_hash != old_hash
        loop_count = int(task["loop_count"] or 0)
        if not changed:
            loop_count += 1
        else:
            loop_count = 0
            db.touch_task_progress(self.conn, task_id, last_output_hash=pane_hash)
        db.set_task_state(self.conn, task_id, str(task["status"]), loop_count=loop_count)

        done_marker = _done_marker(task_id)
        input_marker = _input_marker(task_id)
        progress_marker = _progress_marker(task_id)

        if progress_marker in pane:
            db.touch_task_progress(self.conn, task_id, last_output_hash=pane_hash)

        if _marker_followed_by(pane, done_marker, "Summary:"):
            self._complete_task(task, pane)
            stats.completed += 1
            return

        if changed and _fresh_question_marker(pane, input_marker):
            question = _extract_question(pane, input_marker)
            db.set_task_state(self.conn, task_id, "awaiting_input", blocked_question=question)
            db.add_task_event(self.conn, task_id, "warn", f"Awaiting input: {question}")
            db.create_alert(
                self.conn,
                level="warn",
                kind="blocked",
                message=f"Task {task_id} is awaiting input: {question}",
                task_id=task_id,
                project_id=int(task["project_id"]),
            )
            stats.awaiting_input += 1
            return

        elapsed = _elapsed_minutes(str(task["last_progress_at"] or ""))
        interactive_trap = INTERACTIVE_TRAP_RE.search(pane[-1200:]) is not None
        loop_detected = loop_count >= 6
        timeout_stuck = elapsed >= max(1, stuck_minutes)
        if interactive_trap or loop_detected or timeout_stuck:
            reason_parts: list[str] = []
            if interactive_trap:
                reason_parts.append("interactive prompt trap detected")
            if loop_detected:
                reason_parts.append("output loop detected")
            if timeout_stuck:
                reason_parts.append(f"no meaningful progress for {elapsed:.1f} min")
            reason = ", ".join(reason_parts)
            db.set_task_state(self.conn, task_id, "stuck", blocked_question="")
            db.add_task_event(self.conn, task_id, "warn", f"Task stuck: {reason}")
            db.create_alert(
                self.conn,
                level="warn",
                kind="stuck",
                message=f"Task {task_id} stuck: {reason}",
                task_id=task_id,
                project_id=int(task["project_id"]),
            )
            if auto_reassign:
                self._reassign_task(task, reason)
                stats.reassigned += 1

    def _complete_task(self, task, pane: str) -> None:
        task_id = int(task["id"])
        summary, artifacts = _parse_summary_and_artifacts(pane, _done_marker(task_id))
        project_root = str(task["project_root"])
        worktree_path = str(task["worktree_path"] or project_root)
        branch_name = str(task["branch_name"] or "")
        base_sha = str(task["base_sha"] or "")

        current_branch = _run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD")
        if branch_name and current_branch != branch_name:
            db.set_task_state(self.conn, task_id, "failed", finished=True)
            db.add_task_event(
                self.conn,
                task_id,
                "error",
                f"Task completed on unexpected branch '{current_branch}' (expected '{branch_name}')",
            )
            db.create_alert(
                self.conn,
                level="error",
                kind="branch-policy",
                message=f"Task {task_id} violated branch policy: expected {branch_name}, got {current_branch}",
                task_id=task_id,
                project_id=int(task["project_id"]),
            )
            _cleanup_task_worktree(project_root, worktree_path)
            return

        if base_sha:
            merges = _run_git(worktree_path, "rev-list", "--merges", f"{base_sha}..HEAD")
            if merges.strip():
                db.set_task_state(self.conn, task_id, "failed", finished=True)
                db.add_task_event(self.conn, task_id, "error", "Merge commits detected; policy violation")
                db.create_alert(
                    self.conn,
                    level="error",
                    kind="merge-policy",
                    message=f"Task {task_id} produced merge commits which are forbidden",
                    task_id=task_id,
                    project_id=int(task["project_id"]),
                )
                _cleanup_task_worktree(project_root, worktree_path)
                return

        db.set_task_state(self.conn, task_id, "completed", blocked_question="", finished=True)
        db.add_phase_checkpoint(self.conn, task_id, summary=summary, decisions="", next_context=artifacts)
        db.add_task_event(self.conn, task_id, "info", "Task completed")
        session = str(task["tmux_session"] or "")
        if session:
            kill_session(session)
        _cleanup_task_worktree(project_root, worktree_path)

    def _reassign_task(self, task, reason: str) -> None:
        task_id = int(task["id"])
        session = str(task["tmux_session"] or "")
        target = str(task["tmux_target"] or "")
        if target:
            send_keys(target, "C-c")
        if session:
            kill_session(session)
        _cleanup_task_worktree(str(task["project_root"]), str(task["worktree_path"] or ""))

        attempts = int(task["attempt_count"])
        if attempts >= self.max_attempts:
            db.set_task_state(self.conn, task_id, "failed", finished=True)
            db.add_task_event(
                self.conn,
                task_id,
                "error",
                f"Task exhausted retries after {attempts} attempts ({reason})",
            )
            return

        new_agent = _next_agent(str(task["assigned_agent"] or "codex"))
        self.conn.execute(
            f"""
            UPDATE tasks
            SET status = 'queued',
                preferred_agent = ?,
                blocked_question = NULL,
                loop_count = 0,
                tmux_session = NULL,
                tmux_target = NULL,
                updated_at = ({db.utc_now()})
            WHERE id = ?
            """,
            (new_agent, task_id),
        )
        self.conn.commit()
        db.add_task_event(
            self.conn,
            task_id,
            "warn",
            f"Task auto-reassigned to agent '{new_agent}' after stuck condition ({reason})",
        )
