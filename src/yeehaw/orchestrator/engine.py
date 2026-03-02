"""Orchestrator engine - dispatch/monitor tick loop."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from yeehaw.agent.launcher import build_task_prompt, write_launcher
from yeehaw.agent.profiles import resolve_profile
from yeehaw.agent.runtime_config import default_no_mcp_args, resolve_worker_launch_config
from yeehaw.config.loader import load_feature_flags
from yeehaw.context.loader import load_project_memory_pack
from yeehaw.git.worktree import branch_name, cleanup_worktree, prepare_worktree
from yeehaw.hooks import HookDefinition, HookRequest, HookRunResult, load_hooks, run_hooks
from yeehaw.notify import NotificationDispatcher, load_notification_config
from yeehaw.orchestrator.merge_resolver import TrivialConflictAutoResolver, TrivialConflictResolution
from yeehaw.policy.checks import (
    collect_builtin_policy_input,
    evaluate_builtin_policy_checks,
    has_active_builtin_checks,
)
from yeehaw.policy.loader import load_policy_pack
from yeehaw.scm import (
    GitHubSCMAdapter,
    LocalGitSCMAdapter,
    RoadmapPhaseSummary,
    RoadmapPRPublishRequest,
    RoadmapTaskSummary,
    SCMAdapterError,
)
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
HOOK_SCHEMA_VERSION = 1
NOTIFICATIONS_CONFIG_DIR = "config"
NOTIFICATIONS_RUNTIME_CONFIG = "runtime.json"
NOTIFICATIONS_SINK_CONFIG = "notifications.json"

NOTIFICATION_EVENT_TASK_BLOCKED = "task_blocked"
NOTIFICATION_EVENT_TASK_RETRIES_EXHAUSTED = "task_retries_exhausted"
NOTIFICATION_EVENT_PHASE_COMPLETED = "phase_completed"
NOTIFICATION_EVENT_ROADMAP_COMPLETED = "roadmap_completed"
NOTIFICATION_EVENT_DAEMON_FAILURE = "daemon_failure"
TOKEN_SCAN_WINDOW_LINES = 400
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GITHUB_OWNER_ENV = "YEEHAW_GITHUB_OWNER"
GITHUB_REPO_ENV = "YEEHAW_GITHUB_REPO"
GITHUB_TOKEN_ENV = "YEEHAW_GITHUB_TOKEN"
GITHUB_API_BASE_URL_ENV = "YEEHAW_GITHUB_API_BASE_URL"
TOTAL_TOKEN_PATTERNS = (
    re.compile(r"\btokens?\s+used\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btotal\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btokens?\s+total\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btoken\s+usage\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"totalTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"totalTokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"total_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
INPUT_TOKEN_PATTERNS = (
    re.compile(r"\binput\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bprompt\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"inputTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"promptTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"input_tokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"prompt_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
OUTPUT_TOKEN_PATTERNS = (
    re.compile(r"\boutput\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bcompletion\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bcandidate(?:s)?\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"outputTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"completionTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"candidatesTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"output_tokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"completion_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
TOKEN_LINE_RE = re.compile(r"^\s*([0-9][0-9,]*)\s*$")


@dataclass(frozen=True)
class RebaseResult:
    """Structured result from rebasing a task branch onto integration."""

    error: str | None
    source_sha_after: str | None
    conflict_type: str | None = None
    conflict_files: tuple[str, ...] = ()


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
        self._hooks_by_event = self._load_hooks_by_event()
        self._notification_dispatcher = self._load_notification_dispatcher()

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
        except Exception as exc:
            failure_reason = f"Orchestrator daemon failed: {exc}"
            self.store.log_event("orchestrator_failure", failure_reason, project_id=project_id)
            self.store.create_alert("error", failure_reason, project_id=project_id)
            self._emit_notification(
                NOTIFICATION_EVENT_DAEMON_FAILURE,
                self._notification_payload(
                    project_id=project_id,
                    reason=failure_reason,
                    extra={"failure_kind": "orchestrator_exception"},
                ),
            )
            raise
        finally:
            self.signal_watcher.stop()
            if self._notification_dispatcher is not None:
                self._notification_dispatcher.close(wait=True)
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

            runtime_violation = self._runtime_budget_violation(task)
            if runtime_violation is not None:
                max_runtime_min, elapsed_seconds = runtime_violation
                self._handle_runtime_budget_exceeded(
                    task,
                    session,
                    max_runtime_min=max_runtime_min,
                    elapsed_seconds=elapsed_seconds,
                )
                continue

            if self._is_timed_out(task):
                self._handle_timeout(task, session)
                continue

            token_violation = self._token_budget_violation(task)
            if token_violation is not None:
                max_tokens, observed_tokens = token_violation
                self._handle_token_budget_exceeded(
                    task,
                    session,
                    max_tokens=max_tokens,
                    observed_tokens=observed_tokens,
                )
                continue

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
                failed_task = self.store.get_task(int(task["id"])) or task
                self._emit_on_fail(failed_task, reason=cleanliness_error, stage="done_validation")
                self._maybe_retry(task, failure_reason=cleanliness_error)
            else:
                done_policy_error = self._enforce_builtin_policy_checks(
                    task,
                    stage="done_accept",
                )
                if done_policy_error:
                    self.store.fail_task(task["id"], done_policy_error)
                    failed_task = self.store.get_task(int(task["id"])) or task
                    self._emit_on_fail(
                        failed_task,
                        reason=done_policy_error,
                        stage="done_accept",
                    )
                    self._maybe_retry(task, failure_reason=done_policy_error)
                else:
                    self._emit_hook_event(
                        "pre_merge",
                        task=task,
                        context={"signal_status": "done"},
                    )
                    merge_error = self._merge_done_task_branch(task)
                    self._emit_hook_event(
                        "post_merge",
                        task=task,
                        context={
                            "result": "failed" if merge_error else "merged",
                            "error": merge_error,
                        },
                    )
                    if merge_error:
                        self.store.fail_task(task["id"], merge_error)
                        failed_task = self.store.get_task(int(task["id"])) or task
                        self._emit_on_fail(failed_task, reason=merge_error, stage="merge")
                        self._maybe_retry(task, failure_reason=merge_error)
                    else:
                        # Phase verify commands are phase-level gates and should run only
                        # once all tasks in the phase report done.
                        self.store.complete_task(task["id"], "done")
                        self.store.log_event("task_done", data.get("summary", ""), task_id=task["id"])

        elif data["status"] == "failed":
            failure_reason = data.get("summary", "Unknown failure")
            self.store.fail_task(task["id"], failure_reason)
            failed_task = self.store.get_task(int(task["id"])) or task
            self._emit_on_fail(failed_task, reason=failure_reason, stage="signal_failed")
            self._maybe_retry(task, failure_reason=failure_reason)

        elif data["status"] == "blocked":
            blocked_reason = str(data.get("summary", "Unknown blocker"))
            self.store.complete_task(task["id"], "blocked")
            self.store.create_alert(
                "warn",
                f"Task {task['id']} blocked: {blocked_reason}",
                task_id=task["id"],
            )
            blocked_task = self.store.get_task(int(task["id"])) or task
            self._emit_notification(
                NOTIFICATION_EVENT_TASK_BLOCKED,
                self._notification_payload(
                    task=blocked_task,
                    reason=blocked_reason,
                    extra={"signal_status": "blocked"},
                ),
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
            if self.store.has_in_progress_overlap_conflict(int(task["id"])):
                continue
            self._emit_hook_event(
                "pre_dispatch",
                task=task,
                context={
                    "global_active": self.store.count_active_tasks(),
                    "project_active": project_active,
                    "max_global_tasks": self.config["max_global_tasks"],
                    "max_per_project": self.config["max_per_project"],
                },
            )
            launch_error = self._launch_task(task)
            refreshed = self.store.get_task(int(task["id"])) or task
            self._emit_hook_event(
                "post_dispatch",
                task=refreshed,
                context={
                    "result": "failed" if launch_error else "launched",
                    "error": launch_error,
                },
            )
            if launch_error is not None:
                self._emit_on_fail(refreshed, reason=launch_error, stage="dispatch")

    def _launch_task(self, task: dict[str, Any]) -> str | None:
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
            project_context = self._resolve_task_project_context(task)
            prompt = build_task_prompt(
                task,
                str(signal_dir),
                task.get("last_failure"),
                prompt_file=str(prompt_path),
                project_context=project_context,
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
            return None

        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            self.store.fail_task(task["id"], str(exc))
            self.store.create_alert(
                "error",
                f"Failed to launch task {task['id']}: {exc}",
                task_id=task["id"],
            )
            return str(exc)

    def _load_hooks_by_event(self) -> dict[str, tuple[HookDefinition, ...]]:
        """Load configured hooks and group them by subscribed event."""
        try:
            hooks = load_hooks(runtime_root=self.runtime_root)
        except ValueError as exc:
            message = f"Failed to load hooks: {exc}"
            self.store.log_event("hook_configuration_error", message)
            self.store.create_alert("warn", message)
            return {}

        grouped: dict[str, list[HookDefinition]] = {}
        for hook in hooks:
            for event_name in hook.events:
                grouped.setdefault(event_name, []).append(hook)
        return {
            event_name: tuple(subscribers)
            for event_name, subscribers in grouped.items()
        }

    def _load_notification_dispatcher(self) -> NotificationDispatcher | None:
        """Initialize optional notification dispatcher when feature flag is enabled."""
        config_dir = self.runtime_root / NOTIFICATIONS_CONFIG_DIR
        runtime_config = config_dir / NOTIFICATIONS_RUNTIME_CONFIG
        sink_config = config_dir / NOTIFICATIONS_SINK_CONFIG

        try:
            flags = load_feature_flags(runtime_config)
        except ValueError as exc:
            message = f"Failed to load notification feature flags: {exc}"
            self.store.log_event("notification_configuration_error", message)
            self.store.create_alert("warn", message)
            return None

        if not flags.notifications:
            return None

        try:
            notification_config = load_notification_config(sink_config)
            return NotificationDispatcher(notification_config)
        except ValueError as exc:
            message = f"Failed to load notification config: {exc}"
            self.store.log_event("notification_configuration_error", message)
            self.store.create_alert("warn", message)
            return None
        except Exception as exc:
            message = f"Failed to initialize notification dispatcher: {exc}"
            self.store.log_event("notification_configuration_error", message)
            self.store.create_alert("warn", message)
            return None

    def _emit_notification(self, event_name: str, payload: dict[str, Any]) -> None:
        """Dispatch one notification event; failures are logged and ignored."""
        dispatcher = self._notification_dispatcher
        if dispatcher is None:
            return

        project_id = self._as_int(payload.get("project_id"))
        task_id = self._as_int(payload.get("task_id"))
        try:
            dispatcher.dispatch(event_name, payload)
        except Exception as exc:
            message = f"Failed to dispatch notification '{event_name}': {exc}"
            self.store.log_event(
                "notification_dispatch_failed",
                message,
                project_id=project_id,
                task_id=task_id,
            )
            self.store.create_alert(
                "warn",
                message,
                project_id=project_id,
                task_id=task_id,
            )

    def _notification_payload(
        self,
        *,
        reason: str,
        task: dict[str, Any] | None = None,
        project_id: int | None = None,
        roadmap_id: int | None = None,
        phase_id: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build normalized notification payload with stable identifiers."""
        resolved_project_id = project_id
        resolved_roadmap_id = roadmap_id
        resolved_phase_id = phase_id
        resolved_task_id: int | None = None
        project_name: str | None = None
        task_number: str | None = None
        task_status: str | None = None

        if task is not None:
            if resolved_project_id is None:
                resolved_project_id = self._as_int(task.get("project_id"))
            if resolved_roadmap_id is None:
                resolved_roadmap_id = self._as_int(task.get("roadmap_id"))
            if resolved_phase_id is None:
                resolved_phase_id = self._as_int(task.get("phase_id"))
            resolved_task_id = self._as_int(task.get("id"))
            raw_project_name = task.get("project_name")
            if isinstance(raw_project_name, str) and raw_project_name:
                project_name = raw_project_name
            raw_task_number = task.get("task_number")
            if isinstance(raw_task_number, str) and raw_task_number:
                task_number = raw_task_number
            raw_task_status = task.get("status")
            if isinstance(raw_task_status, str) and raw_task_status:
                task_status = raw_task_status

        payload: dict[str, Any] = {
            "project_id": resolved_project_id,
            "project_name": project_name,
            "roadmap_id": resolved_roadmap_id,
            "phase_id": resolved_phase_id,
            "task_id": resolved_task_id,
            "task_number": task_number,
            "task_status": task_status,
            "reason": reason,
        }
        if extra:
            payload.update(extra)
        return payload

    def _emit_hook_event(
        self,
        event_name: str,
        *,
        task: dict[str, Any] | None = None,
        project_id: int | None = None,
        roadmap_id: int | None = None,
        phase_id: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit one lifecycle hook event and persist hook run telemetry."""
        hooks = self._hooks_by_event.get(event_name, ())
        if not hooks:
            return

        resolved_project_id = project_id
        resolved_roadmap_id = roadmap_id
        resolved_phase_id = phase_id
        resolved_task_id: int | None = None
        if task is not None:
            if resolved_project_id is None:
                resolved_project_id = self._as_int(task.get("project_id"))
            if resolved_roadmap_id is None:
                resolved_roadmap_id = self._as_int(task.get("roadmap_id"))
            if resolved_phase_id is None:
                resolved_phase_id = self._as_int(task.get("phase_id"))
            resolved_task_id = self._as_int(task.get("id"))

        project_payload: dict[str, Any] | None = None
        if resolved_project_id is not None:
            project_payload = {"id": resolved_project_id}
            if task is not None:
                project_name = task.get("project_name")
                if isinstance(project_name, str) and project_name:
                    project_payload["name"] = project_name
                project_repo_root = task.get("project_repo_root")
                if isinstance(project_repo_root, str) and project_repo_root:
                    project_payload["repo_root"] = project_repo_root

        roadmap_payload: dict[str, Any] | None = None
        if resolved_roadmap_id is not None:
            roadmap_payload = {"id": resolved_roadmap_id}
            if task is not None:
                roadmap_status = task.get("roadmap_status")
                if isinstance(roadmap_status, str) and roadmap_status:
                    roadmap_payload["status"] = roadmap_status
                integration_branch = task.get("roadmap_integration_branch")
                if isinstance(integration_branch, str) and integration_branch:
                    roadmap_payload["integration_branch"] = integration_branch

        task_payload: dict[str, Any] | None = None
        attempt_payload: dict[str, Any] | None = None
        if task is not None and resolved_task_id is not None:
            task_payload = {
                "id": resolved_task_id,
                "task_number": task.get("task_number"),
                "title": task.get("title"),
                "status": task.get("status"),
                "assigned_agent": task.get("assigned_agent"),
                "branch_name": task.get("branch_name"),
            }
            current_attempt = self._as_int(task.get("attempts")) or 0
            max_attempts = self._as_int(task.get("max_attempts")) or 0
            attempt_payload = {
                "current": current_attempt,
                "max": max_attempts,
                "timeout_minutes": int(self.config["task_timeout_min"]),
            }

        request = HookRequest(
            schema_version=HOOK_SCHEMA_VERSION,
            event_name=event_name,
            event_id=str(uuid4()),
            emitted_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            source={"component": "orchestrator"},
            context=dict(context or {}),
            project=project_payload,
            roadmap=roadmap_payload,
            task=task_payload,
            attempt=attempt_payload,
        )

        try:
            results = run_hooks(hooks, request)
        except Exception as exc:
            failure_message = f"Hook invocation failed for event '{event_name}': {exc}"
            self.store.log_event(
                "hook_invocation_failed",
                failure_message,
                project_id=resolved_project_id,
                task_id=resolved_task_id,
            )
            self.store.create_alert(
                "warn",
                failure_message,
                project_id=resolved_project_id,
                task_id=resolved_task_id,
            )
            return

        for result in results:
            status, summary, error = self._hook_result_fields(result)
            self.store.create_hook_run(
                project_id=resolved_project_id,
                roadmap_id=resolved_roadmap_id,
                phase_id=resolved_phase_id,
                task_id=resolved_task_id,
                event_name=event_name,
                event_id=request.event_id,
                hook_name=result.hook.name,
                status=status,
                duration_ms=result.duration_ms,
                summary=summary,
                error=error,
                returncode=result.returncode,
            )
            if result.error is None and status != "error":
                continue

            failure_message = self._hook_failure_message(result)
            self.store.log_event(
                "hook_invocation_failed",
                failure_message,
                project_id=resolved_project_id,
                task_id=resolved_task_id,
            )
            self.store.create_alert(
                "warn",
                failure_message,
                project_id=resolved_project_id,
                task_id=resolved_task_id,
            )

    def _emit_on_fail(self, task: dict[str, Any], *, reason: str, stage: str) -> None:
        """Emit standardized failure hook event payload."""
        self._emit_hook_event(
            "on_fail",
            task=task,
            context={"stage": stage, "reason": reason},
        )

    @staticmethod
    def _hook_result_fields(result: HookRunResult) -> tuple[str, str | None, str | None]:
        """Normalize hook runner output for persisted telemetry rows."""
        if result.response is not None:
            summary = result.response.summary
            if summary is None and result.response.status == "error":
                summary = f"Hook '{result.hook.name}' returned error status"
            return (result.response.status, summary, None)

        error = str(result.error) if result.error is not None else None
        return ("failed", error, error)

    @staticmethod
    def _hook_failure_message(result: HookRunResult) -> str:
        """Format a concise operational diagnostic for failed hook invocations."""
        prefix = f"Hook '{result.hook.name}' failed for event '{result.request.event_name}'"
        if result.error is not None:
            return f"{prefix}: {result.error}"
        if result.response is not None and result.response.summary:
            return f"{prefix}: {result.response.summary}"
        return f"{prefix}: status={result.response.status if result.response else 'unknown'}"

    @staticmethod
    def _as_int(value: Any) -> int | None:
        """Best-effort conversion to int for optional metadata fields."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def _phase_task_context(self, phase_id: int) -> dict[str, Any] | None:
        """Return one joined task row to anchor phase/roadmap hook payloads."""
        phase_tasks = self.store.list_tasks_by_phase(phase_id)
        if not phase_tasks:
            return None
        first_task_id = self._as_int(phase_tasks[0].get("id"))
        if first_task_id is None:
            return None
        return self.store.get_task(first_task_id)

    def _resolve_task_project_context(self, task: dict[str, Any]) -> str | None:
        """Resolve optional memory-pack markdown to prepend to task prompts."""
        if not self._memory_pack_feature_enabled():
            return None

        project_name = task.get("project_name")
        if not isinstance(project_name, str) or not project_name.strip():
            return None

        memory_pack = load_project_memory_pack(project_name, runtime_root=self.runtime_root)
        if memory_pack.is_empty:
            return None
        return memory_pack.markdown

    def _memory_pack_feature_enabled(self) -> bool:
        """Return True when runtime feature flag enables memory pack injection."""
        config_path = self.runtime_root / "config" / "runtime.json"
        feature_flags = load_feature_flags(config_path)
        return feature_flags.memory_packs

    def _roadmap_auto_publish_feature_enabled(self) -> bool:
        """Return True when runtime feature flag enables completion auto-publish."""
        config_path = self.runtime_root / "config" / "runtime.json"
        try:
            feature_flags = load_feature_flags(config_path)
        except ValueError as exc:
            message = f"Invalid runtime config; skipping roadmap auto-publish: {exc}"
            self.store.log_event("roadmap_publish_skipped", message)
            self.store.create_alert("warn", message)
            return False
        return feature_flags.pr_automation

    def _trivial_conflict_resolver_enabled(self) -> bool:
        """Return True when runtime feature flag enables trivial conflict auto-resolution."""
        config_path = self.runtime_root / "config" / "runtime.json"
        try:
            feature_flags = load_feature_flags(config_path)
        except ValueError as exc:
            message = f"Invalid runtime config; skipping trivial conflict resolver: {exc}"
            self.store.log_event("task_conflict_auto_resolver_skipped", message)
            self.store.create_alert("warn", message)
            return False
        return feature_flags.trivial_conflict_resolver

    def _run_trivial_conflict_resolver(
        self,
        *,
        worktree_path: Path,
        conflict_type: str,
        conflict_files: list[str],
        task_id: int,
        source_branch: str,
        target_branch: str,
    ) -> TrivialConflictResolution | None:
        """Attempt optional trivial conflict auto-resolution and log one outcome event."""
        if not self._trivial_conflict_resolver_enabled():
            return None

        outcome = TrivialConflictAutoResolver(worktree_path).resolve(
            conflict_type=conflict_type,
            conflict_files=conflict_files,
        )
        conflict_class = outcome.conflict_class or "unknown"
        if outcome.attempted and outcome.resolved:
            self.store.log_event(
                "task_conflict_auto_resolver_succeeded",
                (
                    f"Auto-resolved {conflict_class} while rebasing "
                    f"{source_branch} onto {target_branch}"
                ),
                task_id=task_id,
            )
        elif outcome.attempted:
            self.store.log_event(
                "task_conflict_auto_resolver_failed",
                (
                    f"Failed to auto-resolve {conflict_class} while rebasing "
                    f"{source_branch} onto {target_branch}: {outcome.reason}"
                ),
                task_id=task_id,
            )
        else:
            self.store.log_event(
                "task_conflict_auto_resolver_skipped",
                (
                    f"Skipped trivial resolver while rebasing {source_branch} onto {target_branch}: "
                    f"{outcome.reason}"
                ),
                task_id=task_id,
            )
        return outcome

    def _completed_roadmap_phase_summaries(
        self,
        roadmap_id: int,
    ) -> tuple[RoadmapPhaseSummary, ...]:
        """Return phase/task summaries for PR publication payloads."""
        phase_summaries: list[RoadmapPhaseSummary] = []
        for phase in self.store.list_phases(roadmap_id):
            task_summaries = tuple(
                RoadmapTaskSummary(
                    task_number=str(task["task_number"]),
                    title=str(task["title"]),
                    status=str(task["status"]),
                    summary=str(task["last_failure"]) if task.get("last_failure") else None,
                )
                for task in self.store.list_tasks_by_phase(int(phase["id"]))
            )
            phase_summaries.append(
                RoadmapPhaseSummary(
                    phase_number=int(phase["phase_number"]),
                    title=str(phase["title"]),
                    status=str(phase["status"]),
                    tasks=task_summaries,
                )
            )
        return tuple(phase_summaries)

    def _github_adapter_from_env(self) -> GitHubSCMAdapter | None:
        """Build optional GitHub adapter from environment configuration."""
        owner = os.environ.get(GITHUB_OWNER_ENV, "").strip()
        repo = os.environ.get(GITHUB_REPO_ENV, "").strip()
        token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
        api_base_url = os.environ.get(GITHUB_API_BASE_URL_ENV, "").strip()

        if not owner and not repo and not token:
            return None
        if not owner or not repo or not token:
            raise ValueError(
                "Incomplete GitHub adapter configuration. "
                f"Set {GITHUB_OWNER_ENV}, {GITHUB_REPO_ENV}, and {GITHUB_TOKEN_ENV}."
            )

        return GitHubSCMAdapter(
            owner=owner,
            repo=repo,
            token=token,
            enabled=True,
            api_base_url=api_base_url or "https://api.github.com",
        )

    def _auto_publish_completed_roadmap(
        self,
        *,
        roadmap_id: int,
        project_id: int,
        project_repo_root: Path,
        integration_branch: str,
    ) -> None:
        """Publish completed roadmap branch and optional PR metadata."""
        try:
            publish_result = LocalGitSCMAdapter().publish_roadmap_integration(
                repo_root=project_repo_root,
                roadmap_id=roadmap_id,
                integration_branch=integration_branch,
                base_branch=MAIN_BRANCH,
            )
        except SCMAdapterError as exc:
            message = f"Roadmap {roadmap_id} auto-publish failed: {exc}"
            self.store.log_event("roadmap_publish_failed", message, project_id=project_id)
            self.store.create_alert("warn", message, project_id=project_id)
            return

        self.store.log_event(
            "roadmap_published",
            (
                f"Roadmap {roadmap_id} published at "
                f"{publish_result.summary.integration_branch}@{publish_result.summary.head_sha}"
            ),
            project_id=project_id,
        )

        try:
            github_adapter = self._github_adapter_from_env()
        except ValueError as exc:
            message = f"Roadmap {roadmap_id} auto PR publish skipped: {exc}"
            self.store.log_event("roadmap_publish_skipped", message, project_id=project_id)
            self.store.create_alert("warn", message, project_id=project_id)
            return

        if github_adapter is None:
            return

        publish_request = RoadmapPRPublishRequest(
            repo_root=project_repo_root,
            roadmap_id=roadmap_id,
            integration_branch=integration_branch,
            base_branch=MAIN_BRANCH,
            enabled=True,
            summary=publish_result.summary,
            phase_summaries=self._completed_roadmap_phase_summaries(roadmap_id),
        )
        pr_result = github_adapter.publish_roadmap_pull_request(publish_request)

        for event in pr_result.events:
            self.store.log_event(event.kind, event.message, project_id=project_id)
        for alert in pr_result.alerts:
            self.store.create_alert(alert.severity, alert.message, project_id=project_id)

        publication = pr_result.pull_request
        if publication is not None:
            self.store.log_event(
                "roadmap_pr_trace",
                f"Roadmap {roadmap_id} PR #{publication.number}: {publication.html_url}",
                project_id=project_id,
            )
        elif pr_result.error:
            self.store.log_event(
                "roadmap_publish_failed",
                f"Roadmap {roadmap_id} auto PR publish failed: {pr_result.error}",
                project_id=project_id,
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
        elapsed = self._elapsed_runtime_seconds(task)
        if elapsed is None:
            return False
        return elapsed > self.config["task_timeout_min"] * 60

    def _runtime_budget_violation(self, task: dict[str, Any]) -> tuple[int, float] | None:
        """Return runtime budget breach details when configured and exceeded."""
        max_runtime_min = self._as_int(task.get("max_runtime_min"))
        if max_runtime_min is None or max_runtime_min < 1:
            return None
        elapsed_seconds = self._elapsed_runtime_seconds(task)
        if elapsed_seconds is None:
            return None
        if elapsed_seconds <= max_runtime_min * 60:
            return None
        return (max_runtime_min, elapsed_seconds)

    def _token_budget_violation(self, task: dict[str, Any]) -> tuple[int, int] | None:
        """Return token budget breach details when configured and exceeded."""
        max_tokens = self._as_int(task.get("max_tokens"))
        if max_tokens is None or max_tokens < 1:
            return None
        latest_log = self._latest_task_log_path(int(task["id"]))
        if latest_log is None:
            return None
        try:
            content = latest_log.read_text(errors="replace")
        except OSError:
            return None
        observed_tokens = self._parse_tokens_used(content)
        if observed_tokens is None:
            return None
        if observed_tokens <= max_tokens:
            return None
        return (max_tokens, observed_tokens)

    def _elapsed_runtime_seconds(self, task: dict[str, Any]) -> float | None:
        """Return elapsed runtime seconds for a started task."""
        started_at = task.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            return None
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return max(0.0, elapsed)

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
        failed_task = self.store.get_task(int(task["id"])) or task
        self._emit_on_fail(failed_task, reason=failure_msg, stage="timeout")
        self.store.log_event("task_timeout", failure_msg, task_id=task["id"])
        self._emit_notification(
            NOTIFICATION_EVENT_DAEMON_FAILURE,
            self._notification_payload(
                task=failed_task,
                reason=failure_msg,
                extra={"failure_kind": "task_timeout"},
            ),
        )
        self._maybe_retry(task, failure_reason=failure_msg)
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _handle_runtime_budget_exceeded(
        self,
        task: dict[str, Any],
        session: str,
        *,
        max_runtime_min: int,
        elapsed_seconds: float,
    ) -> None:
        """Fail a task immediately when its runtime budget is exceeded."""
        pane_text = ""
        try:
            pane_text = capture_pane(session)
        except OSError:
            pane_text = ""
        kill_session(session)

        elapsed_minutes = elapsed_seconds / 60.0
        failure_msg = (
            f"Runtime budget exceeded: elapsed {elapsed_minutes:.1f} min > limit {max_runtime_min} min"
        )
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        if pane_text.strip():
            snapshot_path = self._write_pane_snapshot(task["id"], pane_text, "runtime-budget")
            failure_msg = f"{failure_msg}. Pane snapshot: {snapshot_path}"

        self.store.fail_task(task["id"], failure_msg)
        failed_task = self.store.get_task(int(task["id"])) or task
        self._emit_on_fail(failed_task, reason=failure_msg, stage="runtime_budget")
        self.store.log_event("task_budget_exceeded", failure_msg, task_id=task["id"])
        self.store.create_alert(
            "error",
            f"Task {task['id']} runtime budget breached. {failure_msg}",
            task_id=task["id"],
        )
        # Budget breaches are terminal to prevent deterministic runaway retries.
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _handle_token_budget_exceeded(
        self,
        task: dict[str, Any],
        session: str,
        *,
        max_tokens: int,
        observed_tokens: int,
    ) -> None:
        """Fail a task immediately when its token budget is exceeded."""
        pane_text = ""
        try:
            pane_text = capture_pane(session)
        except OSError:
            pane_text = ""
        kill_session(session)

        failure_msg = (
            f"Token budget exceeded: used {observed_tokens:,} tokens > limit {max_tokens:,} tokens"
        )
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        if pane_text.strip():
            snapshot_path = self._write_pane_snapshot(task["id"], pane_text, "token-budget")
            failure_msg = f"{failure_msg}. Pane snapshot: {snapshot_path}"

        self.store.fail_task(task["id"], failure_msg)
        failed_task = self.store.get_task(int(task["id"])) or task
        self._emit_on_fail(failed_task, reason=failure_msg, stage="token_budget")
        self.store.log_event("task_budget_exceeded", failure_msg, task_id=task["id"])
        self.store.create_alert(
            "error",
            f"Task {task['id']} token budget breached. {failure_msg}",
            task_id=task["id"],
        )
        # Budget breaches are terminal to prevent deterministic runaway retries.
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _handle_crash(self, task: dict[str, Any]) -> None:
        failure_msg = "Tmux session lost"
        latest_log = self._latest_task_log_path(task["id"])
        if latest_log is not None:
            failure_msg = f"{failure_msg}. Check log: {latest_log}"
        self.store.fail_task(task["id"], failure_msg)
        failed_task = self.store.get_task(int(task["id"])) or task
        self._emit_on_fail(failed_task, reason=failure_msg, stage="session_lost")
        self.store.log_event("session_lost", failure_msg, task_id=task["id"])
        self._emit_notification(
            NOTIFICATION_EVENT_DAEMON_FAILURE,
            self._notification_payload(
                task=failed_task,
                reason=failure_msg,
                extra={"failure_kind": "session_lost"},
            ),
        )
        self._maybe_retry(task, failure_reason=failure_msg)
        if task.get("worktree_path"):
            cleanup_worktree(self._task_repo_root(task), Path(task["worktree_path"]))

    def _maybe_retry(self, task: dict[str, Any], *, failure_reason: str | None = None) -> None:
        attempts = self._as_int(task.get("attempts")) or 0
        max_attempts = self._as_int(task.get("max_attempts")) or 0
        task_id = int(task["id"])

        if attempts < max_attempts:
            self.store.queue_task(task_id)
            self.store.log_event(
                "task_retry",
                f"Attempt {attempts + 1}",
                task_id=task_id,
            )
            return

        exhausted_task = self.store.get_task(task_id) or task
        exhausted_reason = f"Task {task_id} exhausted {max_attempts} retries"
        self._emit_notification(
            NOTIFICATION_EVENT_TASK_RETRIES_EXHAUSTED,
            self._notification_payload(
                task=exhausted_task,
                reason=exhausted_reason,
                extra={
                    "attempts_used": attempts,
                    "max_attempts": max_attempts,
                },
            ),
        )

        if self._is_reconcile_task(exhausted_task):
            self.store.create_alert(
                "error",
                exhausted_reason,
                task_id=task_id,
            )
            return

        failure_messages = [
            str(message).strip()
            for message in (
                task.get("last_failure"),
                exhausted_task.get("last_failure"),
                failure_reason,
            )
            if isinstance(message, str) and message.strip()
        ]
        reconcile_task_id = self.store.create_linked_reconcile_task(
            failed_task_id=task_id,
            failure_threshold=max_attempts,
            observed_attempts=attempts,
            failure_messages=failure_messages,
        )
        if reconcile_task_id is None:
            self.store.create_alert(
                "error",
                f"Task {task_id} exhausted {max_attempts} retries and reconcile task creation failed",
                task_id=task_id,
            )
            return

        self.store.queue_task(reconcile_task_id)
        self.store.log_event(
            "task_reconcile_queued",
            (
                f"Created reconcile task {reconcile_task_id} for task {task_id} "
                f"after {attempts} failed attempts"
            ),
            task_id=task_id,
        )
        self.store.create_alert(
            "warn",
            (
                f"Task {task_id} hit failure threshold ({attempts}/{max_attempts}); "
                f"queued reconcile task {reconcile_task_id}"
            ),
            task_id=task_id,
        )

    @staticmethod
    def _is_reconcile_task(task: dict[str, Any]) -> bool:
        """Return True when task description marks it as auto-generated reconcile work."""
        description = task.get("description")
        if not isinstance(description, str):
            return False
        return "**Reconcile Source Task ID:**" in description

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
                completed_phase = self.store.get_phase(phase_id)
                if completed_phase is not None:
                    phase_task = self._phase_task_context(phase_id)
                    phase_reason = f"Phase {completed_phase['phase_number']} completed"
                    self._emit_hook_event(
                        "on_phase_complete",
                        task=phase_task,
                        roadmap_id=int(completed_phase["roadmap_id"]),
                        phase_id=phase_id,
                        context={
                            "phase_number": int(completed_phase["phase_number"]),
                            "phase_title": str(completed_phase["title"]),
                            "status": "completed",
                        },
                    )
                    self._emit_notification(
                        NOTIFICATION_EVENT_PHASE_COMPLETED,
                        self._notification_payload(
                            task=phase_task,
                            roadmap_id=int(completed_phase["roadmap_id"]),
                            phase_id=phase_id,
                            reason=phase_reason,
                            extra={
                                "phase_number": int(completed_phase["phase_number"]),
                                "phase_title": str(completed_phase["title"]),
                            },
                        ),
                    )
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
            roadmap_id = int(phase["roadmap_id"])
            self.store.update_roadmap_status(roadmap_id, "completed")
            self.store.log_event(
                "roadmap_completed",
                f"Roadmap {roadmap_id} finished",
            )
            phase_context = self._phase_task_context(completed_phase_id)
            roadmap_reason = f"Roadmap {roadmap_id} completed"
            self._emit_hook_event(
                "on_roadmap_complete",
                task=phase_context,
                roadmap_id=roadmap_id,
                phase_id=completed_phase_id,
                context={"roadmap_id": roadmap_id},
            )
            self._emit_notification(
                NOTIFICATION_EVENT_ROADMAP_COMPLETED,
                self._notification_payload(
                    task=phase_context,
                    roadmap_id=roadmap_id,
                    phase_id=completed_phase_id,
                    reason=roadmap_reason,
                    extra={"roadmap_status": "completed"},
                ),
            )

            if not self._roadmap_auto_publish_feature_enabled():
                return

            roadmap = self.store.get_roadmap(roadmap_id)
            project_id = self._as_int(roadmap.get("project_id")) if roadmap is not None else None
            integration_branch = roadmap.get("integration_branch") if roadmap is not None else None
            project_repo_root = phase_context.get("project_repo_root") if phase_context else None

            if project_id is None or not isinstance(project_repo_root, str) or not project_repo_root:
                message = (
                    f"Roadmap {roadmap_id} completed but project context is missing; "
                    "auto-publish skipped"
                )
                self.store.log_event("roadmap_publish_skipped", message, project_id=project_id)
                return
            if not isinstance(integration_branch, str) or not integration_branch:
                message = (
                    f"Roadmap {roadmap_id} completed without integration branch; "
                    "auto-publish skipped"
                )
                self.store.log_event("roadmap_publish_skipped", message, project_id=project_id)
                return

            self._auto_publish_completed_roadmap(
                roadmap_id=roadmap_id,
                project_id=project_id,
                project_repo_root=Path(project_repo_root),
                integration_branch=integration_branch,
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

    def _finalize_merge_attempt(
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
        """Persist terminal state for one merge attempt telemetry row."""
        self.store.update_task_merge_attempt(
            merge_attempt_id,
            status=status,
            source_sha_after=source_sha_after,
            target_sha_after=target_sha_after,
            conflict_type=conflict_type,
            conflict_files=conflict_files,
            error_detail=error_detail,
        )

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

        task_id = int(task["id"])
        attempt_number = max(1, self._as_int(task.get("attempts")) or 0)
        source_sha_before = self._git_ref(repo_root, source_branch)
        target_sha_before = self._git_ref(repo_root, target_branch)
        merge_attempt_id = self.store.create_task_merge_attempt(
            task_id=task_id,
            attempt_number=attempt_number,
            status="running",
            source_branch=source_branch,
            target_branch=target_branch,
            source_sha_before=source_sha_before,
            target_sha_before=target_sha_before,
        )

        if source_sha_before is None:
            error = f"Task branch '{source_branch}' is missing"
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="failed",
                target_sha_after=target_sha_before,
                error_detail=error,
            )
            return error
        if target_sha_before is None:
            error = f"Merge target branch '{target_branch}' is missing"
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="failed",
                source_sha_after=source_sha_before,
                error_detail=error,
            )
            return error

        if self._git_is_ancestor(repo_root, source_branch, target_branch):
            message = f"Task branch {source_branch} already merged into {target_branch}"
            self.store.log_event(
                "task_merge_skipped",
                message,
                task_id=task_id,
            )
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="skipped",
                source_sha_after=source_sha_before,
                target_sha_after=target_sha_before,
                error_detail=message,
            )
            return None

        pre_merge_policy_error = self._enforce_builtin_policy_checks(
            task,
            stage="pre_merge",
            source_branch=source_branch,
            target_branch=target_branch,
        )
        if pre_merge_policy_error:
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="failed",
                source_sha_after=source_sha_before,
                target_sha_after=target_sha_before,
                error_detail=pre_merge_policy_error,
            )
            return pre_merge_policy_error

        rebase_error = self._rebase_branch_onto_target(
            repo_root=repo_root,
            source_branch=source_branch,
            target_branch=target_branch,
            task_id=task_id,
        )
        source_sha_after = rebase_error.source_sha_after
        if source_sha_after is None:
            source_sha_after = self._git_ref(repo_root, source_branch)
        if rebase_error.error:
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="failed",
                source_sha_after=source_sha_after,
                target_sha_after=target_sha_before,
                conflict_type=rebase_error.conflict_type,
                conflict_files=list(rebase_error.conflict_files),
                error_detail=rebase_error.error,
            )
            return rebase_error.error

        merge_root = self.runtime_root / MERGE_WORKTREE_DIR
        merge_root.mkdir(parents=True, exist_ok=True)
        merge_worktree = merge_root / f"task-{task_id}"
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
            error = f"Failed to prepare merge worktree: {detail}"
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="failed",
                source_sha_after=source_sha_after,
                target_sha_after=target_sha_before,
                error_detail=error,
            )
            return error

        try:
            target_before = self._git_ref(repo_root, target_branch)
            if target_before is None:
                error = f"Merge target branch '{target_branch}' is missing"
                self._finalize_merge_attempt(
                    merge_attempt_id,
                    status="failed",
                    source_sha_after=source_sha_after,
                    error_detail=error,
                )
                return error

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
                detail = self._git_command_error(merge_result, fallback="unknown merge error")
                conflict_type = self._classify_conflict(detail)
                conflict_files = self._git_conflicted_files(merge_worktree)
                conflict_detail = self._format_conflict_detail(
                    detail=detail,
                    conflict_type=conflict_type,
                    files=conflict_files,
                )
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=merge_worktree,
                    capture_output=True,
                    text=True,
                )
                error = (
                    f"Failed to merge {source_branch} into {target_branch}: {conflict_detail}. "
                    "Task will be retried against latest integration branch."
                )
                self._finalize_merge_attempt(
                    merge_attempt_id,
                    status="failed",
                    source_sha_after=source_sha_after,
                    target_sha_after=target_before,
                    conflict_type=conflict_type,
                    conflict_files=conflict_files,
                    error_detail=error,
                )
                return error

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
                error = f"Failed to update integration branch '{target_branch}': {detail}"
                self._finalize_merge_attempt(
                    merge_attempt_id,
                    status="failed",
                    source_sha_after=source_sha_after,
                    target_sha_after=target_before,
                    error_detail=error,
                )
                return error

            self.store.log_event(
                "task_merged",
                f"Merged {source_branch} into {target_branch}",
                task_id=task_id,
            )
            self._finalize_merge_attempt(
                merge_attempt_id,
                status="succeeded",
                source_sha_after=source_sha_after,
                target_sha_after=merged_head,
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
    ) -> RebaseResult:
        """Rebase source branch onto target branch before merge."""
        source_before = self._git_ref(repo_root, source_branch)
        if source_before is None:
            return RebaseResult(
                error=f"Task branch '{source_branch}' is missing",
                source_sha_after=None,
            )

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
            return RebaseResult(
                error=f"Failed to prepare rebase worktree: {detail}",
                source_sha_after=source_before,
            )

        try:
            rebase_result = subprocess.run(
                ["git", "rebase", f"refs/heads/{target_branch}"],
                cwd=rebase_worktree,
                capture_output=True,
                text=True,
            )
            if rebase_result.returncode != 0:
                detail = self._git_command_error(rebase_result, fallback="unknown rebase error")
                conflict_type = self._classify_conflict(detail)
                conflict_files = self._git_conflicted_files(rebase_worktree)
                resolver_outcome = self._run_trivial_conflict_resolver(
                    worktree_path=rebase_worktree,
                    conflict_type=conflict_type,
                    conflict_files=conflict_files,
                    task_id=task_id,
                    source_branch=source_branch,
                    target_branch=target_branch,
                )
                if resolver_outcome is not None:
                    if resolver_outcome.attempted and resolver_outcome.resolved:
                        continue_env = dict(os.environ)
                        continue_env.setdefault("GIT_EDITOR", "true")
                        rebase_result = subprocess.run(
                            ["git", "rebase", "--continue"],
                            cwd=rebase_worktree,
                            capture_output=True,
                            text=True,
                            env=continue_env,
                        )
                        if rebase_result.returncode != 0:
                            detail = self._git_command_error(
                                rebase_result,
                                fallback="unknown rebase error",
                            )
                            conflict_type = self._classify_conflict(detail)
                            conflict_files = self._git_conflicted_files(rebase_worktree)
                    elif resolver_outcome.attempted:
                        detail = f"{detail}; trivial resolver failed: {resolver_outcome.reason}"
                if rebase_result.returncode != 0:
                    conflict_detail = self._format_conflict_detail(
                        detail=detail,
                        conflict_type=conflict_type,
                        files=conflict_files,
                    )
                    subprocess.run(
                        ["git", "rebase", "--abort"],
                        cwd=rebase_worktree,
                        capture_output=True,
                        text=True,
                    )
                    return RebaseResult(
                        error=(
                            f"Failed to rebase {source_branch} onto {target_branch}: {conflict_detail}. "
                            "Task will be retried against latest integration branch."
                        ),
                        source_sha_after=source_before,
                        conflict_type=conflict_type,
                        conflict_files=tuple(conflict_files),
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
                return RebaseResult(
                    error=f"Failed to update rebased task branch '{source_branch}': {detail}",
                    source_sha_after=source_before,
                )

            self.store.log_event(
                "task_rebased",
                f"Rebased {source_branch} onto {target_branch}",
                task_id=task_id,
            )
            return RebaseResult(
                error=None,
                source_sha_after=rebased_head,
            )
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

    def _format_conflict_detail(
        self,
        *,
        detail: str,
        conflict_type: str,
        files: list[str],
    ) -> str:
        """Format conflict classification and file list for retry guidance."""
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

    @staticmethod
    def _parse_tokens_used(text: str) -> int | None:
        """Parse token usage from task log output."""
        clean = ANSI_ESCAPE_RE.sub("", text)
        lines = clean.splitlines()[-TOKEN_SCAN_WINDOW_LINES:]
        tail = "\n".join(lines)

        total = Orchestrator._last_pattern_value(tail, TOTAL_TOKEN_PATTERNS)
        if total is not None:
            return total

        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if "tokens used" not in line.lower():
                continue
            for next_idx in range(idx + 1, min(idx + 4, len(lines))):
                match = TOKEN_LINE_RE.match(lines[next_idx])
                if match is None:
                    continue
                parsed = Orchestrator._parse_int_token(match.group(1))
                if parsed is not None:
                    return parsed

        input_tokens = Orchestrator._last_pattern_value(tail, INPUT_TOKEN_PATTERNS)
        output_tokens = Orchestrator._last_pattern_value(tail, OUTPUT_TOKEN_PATTERNS)
        if input_tokens is not None and output_tokens is not None:
            return input_tokens + output_tokens
        return None

    @staticmethod
    def _parse_int_token(value: str) -> int | None:
        normalized = value.replace(",", "").replace("_", "").strip()
        if not normalized.isdigit():
            return None
        return int(normalized)

    @staticmethod
    def _last_pattern_value(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
        best: tuple[int, int] | None = None
        for pattern in patterns:
            for match in pattern.finditer(text):
                parsed = Orchestrator._parse_int_token(match.group(1))
                if parsed is None:
                    continue
                if best is None or match.start() > best[0]:
                    best = (match.start(), parsed)
        return None if best is None else best[1]

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

    def _enforce_builtin_policy_checks(
        self,
        task: dict[str, Any],
        *,
        stage: str,
        source_branch: str | None = None,
        target_branch: str | None = None,
    ) -> str | None:
        """Evaluate built-in policy checks and return a failure reason when blocked."""
        project_name = task.get("project_name")
        if not isinstance(project_name, str) or not project_name.strip():
            return None

        try:
            policy_pack = load_policy_pack(project_name, runtime_root=self.runtime_root)
        except ValueError as exc:
            return self._record_policy_violation(
                task,
                stage=stage,
                source_branch=source_branch,
                target_branch=target_branch,
                detail=f"Unable to load project policy configuration: {exc}",
            )

        if not has_active_builtin_checks(policy_pack, stage=stage):
            return None

        resolved_source_branch = source_branch
        if not isinstance(resolved_source_branch, str) or not resolved_source_branch:
            candidate_source = task.get("branch_name")
            if isinstance(candidate_source, str) and candidate_source:
                resolved_source_branch = candidate_source

        if not isinstance(resolved_source_branch, str) or not resolved_source_branch:
            return self._record_policy_violation(
                task,
                stage=stage,
                source_branch=source_branch,
                target_branch=target_branch,
                detail="Task branch is missing, unable to evaluate policy checks",
            )

        resolved_target_branch = target_branch
        if not isinstance(resolved_target_branch, str) or not resolved_target_branch:
            resolved_target_branch = self._resolve_merge_target_branch(task)

        repo_root = self._task_repo_root(task)
        try:
            policy_input = collect_builtin_policy_input(
                repo_root=repo_root,
                source_branch=resolved_source_branch,
                target_branch=resolved_target_branch,
            )
        except ValueError as exc:
            return self._record_policy_violation(
                task,
                stage=stage,
                source_branch=resolved_source_branch,
                target_branch=resolved_target_branch,
                detail=f"Unable to evaluate policy checks: {exc}",
            )

        result = evaluate_builtin_policy_checks(
            policy_pack,
            policy_input,
            stage=stage,
        )
        if result.allowed:
            return None

        violation_text = "; ".join(
            f"{violation.code}: {violation.message}"
            for violation in result.violations[:3]
        )
        if len(result.violations) > 3:
            violation_text = f"{violation_text}; ... ({len(result.violations)} total violations)"

        return self._record_policy_violation(
            task,
            stage=stage,
            source_branch=resolved_source_branch,
            target_branch=resolved_target_branch,
            detail=violation_text,
        )

    def _record_policy_violation(
        self,
        task: dict[str, Any],
        *,
        stage: str,
        source_branch: str | None,
        target_branch: str | None,
        detail: str,
    ) -> str:
        """Persist policy violation diagnostics and return failure reason."""
        source = source_branch or "<unknown>"
        target = target_branch or "<unknown>"
        message = (
            f"Task policy violation at {stage} "
            f"(source={source}, target={target}): {detail}"
        )

        task_id = self._as_int(task.get("id"))
        project_id = self._as_int(task.get("project_id"))
        self.store.log_event(
            "task_policy_violation",
            message,
            project_id=project_id,
            task_id=task_id,
        )
        self.store.create_alert(
            "warn",
            message,
            project_id=project_id,
            task_id=task_id,
        )
        return message

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
