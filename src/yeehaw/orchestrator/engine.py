"""Orchestrator engine - dispatch/monitor tick loop."""

from __future__ import annotations

import os
import signal
import subprocess
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


class Orchestrator:
    """Coordinates task dispatch, monitoring, and completion processing."""

    def __init__(
        self,
        store: Store,
        repo_root: Path,
        default_agent: str | None = None,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.config = store.get_scheduler_config()
        self.signal_watcher = SignalWatcher(repo_root / ".yeehaw" / "signals")
        self.default_agent = resolve_profile(default_agent).name if default_agent else None
        self.running = False
        self._poll_counter = 0

    def run(self, project_id: int | None = None) -> None:
        """Start orchestrator loop until stopped."""
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
        """Request orchestrator shutdown."""
        self.running = False

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
            if self._run_verification(task):
                self.store.complete_task(task["id"], "done")
                self.store.log_event("task_done", data.get("summary", ""), task_id=task["id"])
            else:
                self.store.fail_task(task["id"], "Verification command failed")
                self._maybe_retry(task)

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
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

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
            self._launch_task(task)

    def _launch_task(self, task: dict[str, Any]) -> None:
        try:
            profile = resolve_profile(task.get("assigned_agent") or self.default_agent)
            worker_cfg = resolve_worker_launch_config(self.repo_root, profile.name)
            branch = branch_name(task["task_number"], task["title"])
            worktree_path = prepare_worktree(self.repo_root, branch)
            attempt_num = int(task.get("attempts") or 0) + 1

            signal_dir = self.repo_root / ".yeehaw" / "signals" / f"task-{task['id']}"
            signal_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._task_log_path(task["id"], attempt_num, profile.name)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            prompt_path = worktree_path / ".yeehaw" / f"task-{task['id']}-prompt.md"
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
        result = subprocess.run(
            phase["verify_cmd"],
            shell=True,
            cwd=self.repo_root,
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
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

    def _handle_crash(self, task: dict[str, Any]) -> None:
        failure_msg = "Tmux session lost"
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        self.store.fail_task(task["id"], failure_msg)
        self.store.log_event("session_lost", failure_msg, task_id=task["id"])
        self._maybe_retry(task)
        if task.get("worktree_path"):
            cleanup_worktree(self.repo_root, Path(task["worktree_path"]))

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
            if phase and phase.get("verify_cmd"):
                result = subprocess.run(
                    phase["verify_cmd"],
                    shell=True,
                    cwd=self.repo_root,
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

    def _task_log_path(self, task_id: int, attempt: int, agent: str) -> Path:
        logs_root = self.repo_root / ".yeehaw" / "logs" / f"task-{task_id}"
        return logs_root / f"attempt-{attempt:02d}-{agent}.log"

    def _latest_task_log_path(self, task_id: int) -> Path | None:
        logs_root = self.repo_root / ".yeehaw" / "logs" / f"task-{task_id}"
        if not logs_root.exists():
            return None
        candidates = sorted(logs_root.glob("attempt-*.log"))
        if not candidates:
            return None
        return candidates[-1]

    def _write_pane_snapshot(self, task_id: int, pane_text: str, kind: str) -> Path:
        logs_root = self.repo_root / ".yeehaw" / "logs" / f"task-{task_id}"
        logs_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = logs_root / f"{kind}-pane-{timestamp}.txt"
        snapshot_path.write_text(pane_text)
        return snapshot_path

    def _write_pid_file(self) -> None:
        """Ensure only one orchestrator process owns this repo."""
        pid_path = self.repo_root / ".yeehaw" / "orchestrator.pid"
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
        pid_path = self.repo_root / ".yeehaw" / "orchestrator.pid"
        pid_path.unlink(missing_ok=True)

    def _install_signal_handlers(self) -> None:
        """Stop run loop on SIGINT/SIGTERM."""
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
