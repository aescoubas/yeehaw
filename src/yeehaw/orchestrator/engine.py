"""Orchestrator engine - dispatch/monitor tick loop."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yeehaw.agent.launcher import build_task_prompt, write_launcher
from yeehaw.agent.profiles import resolve_profile
from yeehaw.agent.runtime_config import default_no_mcp_args, resolve_worker_launch_config
from yeehaw.git.worktree import branch_name, cleanup_worktree, prepare_worktree
from yeehaw.signal.protocol import SignalWatcher, read_signal
from yeehaw.store.store import Store
from yeehaw.tmux.session import (
    capture_pane,
    has_session,
    kill_session,
    launch_agent,
    pipe_output,
)

MAIN_BRANCH = "main"
INTEGRATION_BRANCH_PREFIX = "yeehaw/roadmap-"
MERGE_WORKTREE_DIR = "merge-worktrees"
REBASE_WORKTREE_DIR = "rebase-worktrees"


class Orchestrator:
    """Coordinates task dispatch, monitoring, and completion processing."""

    def __init__(
        self,
        store: Store,
        repo_root: Path,
        runtime_root: Path | None = None,
        default_agent: str | None = None,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.runtime_root = runtime_root or (repo_root / ".yeehaw")
        self.config = store.get_scheduler_config()
        self.signal_watcher = SignalWatcher(self.runtime_root / "signals")
        self.default_agent = resolve_profile(default_agent).name if default_agent else None
        self.running = False
        self._stop_event = threading.Event()
        self._poll_counter = 0

    def run(self, project_id: int | None = None) -> None:
        """Start orchestrator loop until stopped."""
        self._write_pid_file()
        self._install_signal_handlers()
        self.running = True
        self._stop_event.clear()
        self.signal_watcher.start()
        self.store.log_event("orchestrator_start", "Orchestrator started")

        try:
            while self.running:
                self._tick(project_id)
                if not self.running:
                    break
                if self._stop_event.wait(self.config["tick_interval_sec"]):
                    break
        finally:
            self.signal_watcher.stop()
            self._remove_pid_file()
            self.store.log_event("orchestrator_stop", "Orchestrator stopped")

    def stop(self) -> None:
        """Request orchestrator shutdown."""
        self.running = False
        self._stop_event.set()

    def _tick(self, project_id: int | None) -> None:
        self._monitor_active(project_id)
        self._dispatch_queued(project_id)
        self._poll_counter += 1

    def _monitor_active(self, project_id: int | None) -> None:
        for signal_path in self.signal_watcher.get_ready_signals():
            self._process_signal_file(signal_path)

        if self._poll_counter % 6 == 0:
            for signal_path in self.signal_watcher.poll_signals():
                self._process_signal_file(signal_path)

        active = self.store.list_tasks(project_id=project_id, status="in-progress")
        for task in active:
            session = f"yeehaw-task-{task['id']}"

            if not has_session(session):
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
        """Read signal file and transition task state accordingly."""
        data = read_signal(signal_path)
        if data is None:
            return

        task = self.store.get_task(data["task_id"])
        if task is None or task["status"] != "in-progress":
            return

        session = f"yeehaw-task-{task['id']}"

        if data["status"] == "done":
            cleanliness_error = self._validate_done_signal_worktree(task)
            if cleanliness_error:
                self.store.fail_task(task["id"], cleanliness_error)
                self._maybe_retry(task)
            else:
                merge_error = self._merge_done_task_branch(task)
                if merge_error:
                    self.store.fail_task(task["id"], merge_error)
                    self._maybe_retry(task)
                else:
                    # Phase verify commands are phase-level gates and should run only
                    # once all tasks in the phase report done.
                    self.store.complete_task(task["id"], "done")
                    self.store.log_event("task_done", data.get("summary", ""), task_id=task["id"])

        elif data["status"] == "failed":
            self.store.fail_task(task["id"], data.get("summary", "Unknown failure"))
            self._maybe_retry(task)

        elif data["status"] == "blocked":
            self.store.complete_task(task["id"], "blocked")
            self.store.create_alert(
                "warn",
                f"Task {task['id']} blocked: {data.get('summary', '')}",
                task_id=task["id"],
            )

        kill_session(session)
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

        self._check_phase_completion(task["phase_id"])

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
            if not self.store.are_task_dependencies_satisfied(int(task["id"])):
                continue
            self._launch_task(task)

    def _launch_task(self, task: dict[str, Any]) -> None:
        try:
            task_repo_root = self._task_repo_root(task)
            profile = resolve_profile(task.get("assigned_agent") or self.default_agent)
            worker_cfg = resolve_worker_launch_config(self.runtime_root, profile.name)
            integration_branch = self._ensure_integration_branch(task)
            branch = str(task.get("branch_name") or branch_name(task["task_number"], task["title"]))
            existing_worktree = task.get("worktree_path")
            worktree_path: Path
            if isinstance(existing_worktree, str) and existing_worktree:
                candidate = Path(existing_worktree)
                if candidate.exists():
                    worktree_path = candidate
                else:
                    worktree_path = prepare_worktree(
                        task_repo_root,
                        self.runtime_root,
                        branch,
                        base_ref=integration_branch,
                    )
            else:
                worktree_path = prepare_worktree(
                    task_repo_root,
                    self.runtime_root,
                    branch,
                    base_ref=integration_branch,
                )
            attempt_num = int(task.get("attempts") or 0) + 1

            signal_dir = self.runtime_root / "signals" / f"task-{task['id']}"
            signal_dir.mkdir(parents=True, exist_ok=True)
            # Prevent stale completion signals from previous attempts being reprocessed.
            (signal_dir / "signal.json").unlink(missing_ok=True)
            log_path = self._task_log_path(task["id"], attempt_num, profile.name)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            prompt_path = signal_dir / f"task-{task['id']}-prompt.md"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt = build_task_prompt(
                task,
                str(signal_dir),
                task.get("last_failure"),
                prompt_file=str(prompt_path),
            )
            prompt_path.write_text(prompt)

            launch_args = list(worker_cfg.extra_args)
            if worker_cfg.disable_default_mcp:
                launch_args = [*default_no_mcp_args(profile.name), *launch_args]
            launch_env = dict(worker_cfg.env)
            launch_env.setdefault("YEEHAW_TASK_PROMPT_FILE", str(prompt_path))

            self.store.assign_task(
                task["id"],
                profile.name,
                branch,
                str(worktree_path),
                str(signal_dir),
            )

            session = f"yeehaw-task-{task['id']}"
            launcher_path = signal_dir / "launch.sh"
            write_launcher(
                launcher_path,
                profile,
                prompt,
                extra_args=launch_args,
                env=launch_env,
            )
            launch_agent(session, str(worktree_path), str(launcher_path))
            try:
                pipe_output(session, str(log_path))
            except (subprocess.CalledProcessError, OSError) as exc:
                self.store.log_event("task_log_pipe_failed", str(exc), task_id=task["id"])
                self.store.create_alert(
                    "warn",
                    f"Task {task['id']} launched but log pipe failed: {exc}",
                    task_id=task["id"],
                )

            self.store.log_event(
                "task_launched",
                f"Agent: {profile.name}, log: {log_path}, prompt: {prompt_path}",
                task_id=task["id"],
            )

        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            self.store.fail_task(task["id"], str(exc))
            self.store.create_alert(
                "error",
                f"Failed to launch task {task['id']}: {exc}",
                task_id=task["id"],
            )

    def _run_verification(self, task: dict[str, Any]) -> bool:
        """Run phase verify command if configured."""
        phase = self.store.get_phase(task["phase_id"])
        if not phase or not phase.get("verify_cmd"):
            return True
        verify_root = self._task_verification_root(task)
        result = subprocess.run(
            phase["verify_cmd"],
            shell=True,
            cwd=verify_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0

    def _is_timed_out(self, task: dict[str, Any]) -> bool:
        started_at = task.get("started_at")
        if not started_at:
            return False
        started = datetime.fromisoformat(started_at)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return elapsed > self.config["task_timeout_min"] * 60

    def _handle_timeout(self, task: dict[str, Any], session: str) -> None:
        pane_text = ""
        try:
            pane_text = capture_pane(session)
        except OSError:
            pane_text = ""
        kill_session(session)
        failure_msg = "Task timed out"
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        if pane_text.strip():
            snapshot_path = self._write_pane_snapshot(task["id"], pane_text, "timeout")
            failure_msg = f"{failure_msg}. Pane snapshot: {snapshot_path}"
        self.store.fail_task(task["id"], failure_msg)
        self.store.log_event("task_timeout", failure_msg, task_id=task["id"])
        self._maybe_retry(task)
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _handle_crash(self, task: dict[str, Any]) -> None:
        failure_msg = "Tmux session lost"
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        self.store.fail_task(task["id"], failure_msg)
        self.store.log_event("session_lost", failure_msg, task_id=task["id"])
        self._maybe_retry(task)
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _maybe_retry(self, task: dict[str, Any]) -> None:
        if task["attempts"] < task["max_attempts"]:
            self.store.queue_task(task["id"])
            self.store.log_event(
                "task_retry",
                f"Attempt {task['attempts'] + 1}",
                task_id=task["id"],
            )
        else:
            self.store.create_alert(
                "error",
                f"Task {task['id']} exhausted {task['max_attempts']} retries",
                task_id=task["id"],
            )

    def _check_phase_completion(self, phase_id: int) -> None:
        tasks = self.store.list_tasks_by_phase(phase_id)
        if not tasks:
            return
        if all(task["status"] == "done" for task in tasks):
            phase = self.store.get_phase(phase_id)
            verify_repo_root = self._phase_repo_root(phase_id)
            if phase and phase.get("verify_cmd"):
                result = subprocess.run(
                    phase["verify_cmd"],
                    shell=True,
                    cwd=verify_repo_root,
                    capture_output=True,
                    text=True,
                    timeout=120,
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
        next_phases = [
            candidate
            for candidate in phases
            if candidate["phase_number"] == phase["phase_number"] + 1
        ]
        if next_phases:
            next_phase = next_phases[0]
            tasks = self.store.list_tasks_by_phase(next_phase["id"])
            for task in tasks:
                self.store.queue_task(task["id"])
            self.store.update_phase_status(next_phase["id"], "executing")
        else:
            self.store.update_roadmap_status(phase["roadmap_id"], "completed")
            self.store.log_event(
                "roadmap_completed",
                f"Roadmap {phase['roadmap_id']} finished",
            )

    def _ensure_integration_branch(self, task: dict[str, Any]) -> str:
        """Ensure roadmap integration branch exists and return its name."""
        existing = task.get("roadmap_integration_branch")
        if isinstance(existing, str) and existing:
            if self._git_branch_exists(self._task_repo_root(task), existing):
                return existing
            raise RuntimeError(
                f"Integration branch '{existing}' is missing for roadmap {task['roadmap_id']}"
            )

        roadmap_id = int(task["roadmap_id"])
        branch = f"{INTEGRATION_BRANCH_PREFIX}{roadmap_id}"
        repo_root = self._task_repo_root(task)
        if not self._git_branch_exists(repo_root, branch):
            subprocess.run(
                ["git", "branch", branch, "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.store.log_event(
                "roadmap_branch_created",
                f"Created integration branch {branch}",
                project_id=int(task["project_id"]),
                task_id=int(task["id"]),
            )

        self.store.set_roadmap_integration_branch(roadmap_id, branch)
        task["roadmap_integration_branch"] = branch
        return branch

    def _resolve_merge_target_branch(self, task: dict[str, Any]) -> str:
        """Resolve branch where completed task changes should be merged."""
        integration_branch = task.get("roadmap_integration_branch")
        if isinstance(integration_branch, str) and integration_branch:
            return integration_branch
        return MAIN_BRANCH

    def _merge_done_task_branch(self, task: dict[str, Any]) -> str | None:
        """Merge completed task branch into the roadmap integration branch."""
        source_branch = task.get("branch_name")
        if not isinstance(source_branch, str) or not source_branch:
            return None

        repo_root = self._task_repo_root(task)
        if task.get("roadmap_integration_branch"):
            target_branch = self._resolve_merge_target_branch(task)
        else:
            try:
                target_branch = self._ensure_integration_branch(task)
            except RuntimeError as exc:
                return str(exc)

        if not self._git_branch_exists(repo_root, source_branch):
            return f"Task branch '{source_branch}' is missing"
        if not self._git_branch_exists(repo_root, target_branch):
            return f"Merge target branch '{target_branch}' is missing"

        if self._git_is_ancestor(repo_root, source_branch, target_branch):
            self.store.log_event(
                "task_merge_skipped",
                f"Task branch {source_branch} already merged into {target_branch}",
                task_id=int(task["id"]),
            )
            return None

        rebase_error = self._rebase_branch_onto_target(
            repo_root=repo_root,
            source_branch=source_branch,
            target_branch=target_branch,
            task_id=int(task["id"]),
        )
        if rebase_error:
            return rebase_error

        merge_root = self.runtime_root / MERGE_WORKTREE_DIR
        merge_root.mkdir(parents=True, exist_ok=True)
        merge_worktree = merge_root / f"task-{task['id']}"
        if merge_worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(merge_worktree)],
                cwd=repo_root,
                capture_output=True,
            )

        add_result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(merge_worktree), f"refs/heads/{target_branch}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if add_result.returncode != 0:
            detail = add_result.stderr.strip() or add_result.stdout.strip() or "unknown git error"
            return f"Failed to prepare merge worktree: {detail}"

        try:
            target_before = self._git_ref(repo_root, target_branch)
            if target_before is None:
                return f"Merge target branch '{target_branch}' is missing"

            merge_env = dict(os.environ)
            merge_env.setdefault("GIT_AUTHOR_NAME", "Yeehaw")
            merge_env.setdefault("GIT_AUTHOR_EMAIL", "yeehaw@local")
            merge_env.setdefault("GIT_COMMITTER_NAME", "Yeehaw")
            merge_env.setdefault("GIT_COMMITTER_EMAIL", "yeehaw@local")

            merge_result = subprocess.run(
                ["git", "merge", "--ff-only", f"refs/heads/{source_branch}"],
                cwd=merge_worktree,
                capture_output=True,
                text=True,
                env=merge_env,
            )
            if merge_result.returncode != 0:
                merge_result = subprocess.run(
                    ["git", "merge", "--no-edit", f"refs/heads/{source_branch}"],
                    cwd=merge_worktree,
                    capture_output=True,
                    text=True,
                    env=merge_env,
                )
            if merge_result.returncode != 0:
                conflict_detail = self._format_conflict_detail(
                    worktree_path=merge_worktree,
                    detail=self._git_command_error(merge_result, fallback="unknown merge error"),
                )
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=merge_worktree,
                    capture_output=True,
                    text=True,
                )
                return (
                    f"Failed to merge {source_branch} into {target_branch}: {conflict_detail}. "
                    "Task will be retried against latest integration branch."
                )

            merged_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=merge_worktree,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            update = subprocess.run(
                [
                    "git",
                    "update-ref",
                    f"refs/heads/{target_branch}",
                    merged_head,
                    target_before,
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if update.returncode != 0:
                detail = update.stderr.strip() or update.stdout.strip() or "unknown update-ref error"
                return f"Failed to update integration branch '{target_branch}': {detail}"

            self.store.log_event(
                "task_merged",
                f"Merged {source_branch} into {target_branch}",
                task_id=int(task["id"]),
            )
            return None
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(merge_worktree)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

    def _rebase_branch_onto_target(
        self,
        *,
        repo_root: Path,
        source_branch: str,
        target_branch: str,
        task_id: int,
    ) -> str | None:
        """Rebase source branch onto target branch before merge."""
        source_before = self._git_ref(repo_root, source_branch)
        if source_before is None:
            return f"Task branch '{source_branch}' is missing"

        rebase_root = self.runtime_root / REBASE_WORKTREE_DIR
        rebase_root.mkdir(parents=True, exist_ok=True)
        rebase_worktree = rebase_root / f"task-{task_id}"
        if rebase_worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(rebase_worktree)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

        add_result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(rebase_worktree), f"refs/heads/{source_branch}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if add_result.returncode != 0:
            detail = self._git_command_error(add_result, fallback="unknown git error")
            return f"Failed to prepare rebase worktree: {detail}"

        try:
            rebase_result = subprocess.run(
                ["git", "rebase", f"refs/heads/{target_branch}"],
                cwd=rebase_worktree,
                capture_output=True,
                text=True,
            )
            if rebase_result.returncode != 0:
                conflict_detail = self._format_conflict_detail(
                    worktree_path=rebase_worktree,
                    detail=self._git_command_error(rebase_result, fallback="unknown rebase error"),
                )
                subprocess.run(
                    ["git", "rebase", "--abort"],
                    cwd=rebase_worktree,
                    capture_output=True,
                    text=True,
                )
                return (
                    f"Failed to rebase {source_branch} onto {target_branch}: {conflict_detail}. "
                    "Task will be retried against latest integration branch."
                )

            rebased_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=rebase_worktree,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            update = subprocess.run(
                ["git", "update-ref", f"refs/heads/{source_branch}", rebased_head, source_before],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if update.returncode != 0:
                detail = self._git_command_error(update, fallback="unknown update-ref error")
                return f"Failed to update rebased task branch '{source_branch}': {detail}"

            self.store.log_event(
                "task_rebased",
                f"Rebased {source_branch} onto {target_branch}",
                task_id=task_id,
            )
            return None
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(rebase_worktree)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

    @staticmethod
    def _git_command_error(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        """Return best-effort human-readable error text from a git subprocess result."""
        detail = result.stderr.strip() or result.stdout.strip()
        return detail or fallback

    @staticmethod
    def _git_conflicted_files(worktree_path: Path) -> list[str]:
        """Return list of currently conflicted files in a worktree."""
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        files: list[str] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                files.append(stripped)
        return files

    @staticmethod
    def _classify_conflict(detail: str) -> str:
        """Classify common git conflict categories from command output."""
        lowered = detail.lower()
        if "add/add" in lowered or "both added" in lowered:
            return "add_add_conflict"
        if "modify/delete" in lowered or "deleted by" in lowered:
            return "modify_delete_conflict"
        if "rename/rename" in lowered or "rename/delete" in lowered:
            return "rename_conflict"
        if "binary" in lowered and "conflict" in lowered:
            return "binary_conflict"
        if "conflict" in lowered:
            return "content_conflict"
        return "unknown_conflict"

    def _format_conflict_detail(self, worktree_path: Path, detail: str) -> str:
        """Format conflict classification and file list for retry guidance."""
        conflict_type = self._classify_conflict(detail)
        files = self._git_conflicted_files(worktree_path)
        if not files:
            return f"{conflict_type}; {detail}"
        preview = ", ".join(files[:5])
        if len(files) > 5:
            preview += ", ..."
        return f"{conflict_type}; files: {preview}; {detail}"

    @staticmethod
    def _git_branch_exists(repo_root: Path, branch: str) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    @staticmethod
    def _git_ref(repo_root: Path, branch: str) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    @staticmethod
    def _git_is_ancestor(repo_root: Path, maybe_ancestor: str, maybe_descendant: str) -> bool:
        result = subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                f"refs/heads/{maybe_ancestor}",
                f"refs/heads/{maybe_descendant}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _task_log_path(self, task_id: int, attempt: int, agent: str) -> Path:
        logs_root = self.runtime_root / "logs" / f"task-{task_id}"
        return logs_root / f"attempt-{attempt:02d}-{agent}.log"

    def _latest_task_log_path(self, task_id: int) -> Path | None:
        logs_root = self.runtime_root / "logs" / f"task-{task_id}"
        if not logs_root.exists():
            return None
        candidates = sorted(logs_root.glob("attempt-*.log"))
        if not candidates:
            return None
        return candidates[-1]

    def _write_pane_snapshot(self, task_id: int, pane_text: str, kind: str) -> Path:
        logs_root = self.runtime_root / "logs" / f"task-{task_id}"
        logs_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = logs_root / f"{kind}-pane-{timestamp}.txt"
        snapshot_path.write_text(pane_text)
        return snapshot_path

    def _task_repo_root(self, task: dict[str, Any]) -> Path:
        """Resolve git repo root for a task from project metadata."""
        candidate = task.get("project_repo_root")
        if isinstance(candidate, str) and candidate:
            return Path(candidate)
        return self.repo_root

    def _phase_repo_root(self, phase_id: int) -> Path:
        """Resolve git repo root for phase verification."""
        phase_tasks = self.store.list_tasks_by_phase(phase_id)
        if not phase_tasks:
            return self.repo_root
        first = self.store.get_task(int(phase_tasks[0]["id"]))
        if first is None:
            return self.repo_root
        return self._task_repo_root(first)

    def _task_verification_root(self, task: dict[str, Any]) -> Path:
        """Resolve verification cwd, preferring the task worktree when available."""
        worktree_path = task.get("worktree_path")
        if isinstance(worktree_path, str) and worktree_path:
            candidate = Path(worktree_path)
            if candidate.exists():
                return candidate
        return self._task_repo_root(task)

    def _validate_done_signal_worktree(self, task: dict[str, Any]) -> str | None:
        """Ensure done signals only pass when task worktree has no pending changes."""
        worktree_path = task.get("worktree_path")
        if not isinstance(worktree_path, str) or not worktree_path:
            return None

        candidate = Path(worktree_path)
        if not candidate.exists():
            return "Task reported done but worktree path is missing"

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=candidate,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
            return f"Task reported done but worktree validation failed: {detail}"

        if result.stdout.strip():
            return "Task reported done with uncommitted changes in worktree"

        return None

    def _write_pid_file(self) -> None:
        """Ensure only one orchestrator process owns this repo."""
        pid_path = self.runtime_root / "orchestrator.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        if pid_path.exists():
            try:
                old_pid = int(pid_path.read_text().strip())
                os.kill(old_pid, 0)
                raise RuntimeError(
                    f"Another orchestrator is running (PID {old_pid}). "
                    f"Kill it first or remove {pid_path}",
                )
            except (ProcessLookupError, ValueError):
                pass

        pid_path.write_text(str(os.getpid()))

    def _remove_pid_file(self) -> None:
        """Remove orchestrator pid file."""
        pid_path = self.runtime_root / "orchestrator.pid"
        pid_path.unlink(missing_ok=True)

    def _install_signal_handlers(self) -> None:
        """Stop run loop on SIGINT/SIGTERM."""
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
