"""Status and alerts display commands."""

from __future__ import annotations

import json as json_module
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

TITLE_WIDTH = 35
BRANCH_WIDTH = 8
ATTEMPTS_WIDTH = 8
TOKENS_WIDTH = 12
BUDGET_WIDTH = 24
RECONCILE_WIDTH = 24
HOLD_WIDTH = 38
MERGE_DIAGNOSTIC_WIDTH = 44
BRANCH_NA = "n/a"
BRANCH_AHEAD = "ahead"
BRANCH_DIVERGED = "diverged"
BRANCH_MERGED = "merged"
MAIN_BRANCH = "main"
TOKENS_NA = "n/a"
BUDGET_NA = "n/a"
BUDGET_PRESSURE_WARN_THRESHOLD = 0.8
RECONCILE_NA = "n/a"
RECONCILE_STATE_NONE = "none"
RECONCILE_STATE_TASK = "task"
RECONCILE_STATE_SOURCE_ACTIVE = "source_active"
RECONCILE_STATE_SOURCE_CLOSED = "source_closed"
RECONCILE_ACTIVE_STATUSES = frozenset({"queued", "in-progress", "paused"})
MERGE_DIAGNOSTIC_NA = "n/a"
HOLD_REASON_OVERLAP_CONFLICT = "conflict_in_progress_overlap"
TOKEN_SCAN_WINDOW_LINES = 400
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RECONCILE_SOURCE_TASK_ID_RE = re.compile(
    r"\*\*Reconcile Source Task ID:\*\*\s*([0-9]+)"
)
RECONCILE_SOURCE_TASK_NUMBER_RE = re.compile(
    r"\*\*Reconcile Source Task:\*\*\s*([Pp]?[0-9]+\.[0-9]+)\b"
)
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
MERGE_DIAGNOSTIC_WHITESPACE_RE = re.compile(r"\s+")
MERGE_CONFLICT_FILE_PREVIEW = 3


def _truncate_for_column(value: str, width: int) -> str:
    """Truncate text to fixed column width, adding ellipsis when needed."""
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."


def _task_repo_root(task: dict[str, Any], db_path: Path) -> Path:
    """Resolve git repo root for a task."""
    candidate = task.get("project_repo_root")
    if isinstance(candidate, str) and candidate:
        return Path(candidate)
    return Path.cwd()


def _resolve_branch_state(repo_root: Path, branch_name: str, target_branch: str) -> str:
    """Resolve branch state from git ancestry for one task branch."""
    try:
        rev_parse = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except OSError:
        return BRANCH_NA

    if rev_parse.returncode != 0:
        return BRANCH_NA

    main_parse = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{target_branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if main_parse.returncode != 0:
        return BRANCH_NA

    ancestry = subprocess.run(
        [
            "git",
            "rev-list",
            "--left-right",
            "--count",
            f"refs/heads/{target_branch}...refs/heads/{branch_name}",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0:
        return BRANCH_NA

    parts = ancestry.stdout.strip().split()
    if len(parts) != 2:
        return BRANCH_NA

    try:
        main_only, branch_only = (int(parts[0]), int(parts[1]))
    except ValueError:
        return BRANCH_NA

    if branch_only == 0:
        return BRANCH_MERGED
    if main_only == 0:
        return BRANCH_AHEAD
    return BRANCH_DIVERGED


def _annotate_branch_states(tasks: list[dict[str, Any]], db_path: Path) -> None:
    """Attach branch_state to each task for status rendering."""
    cache: dict[tuple[str, str, str], str] = {}
    for task in tasks:
        branch_name = task.get("branch_name")
        if not isinstance(branch_name, str) or not branch_name:
            task["branch_state"] = BRANCH_NA
            continue
        repo_root = _task_repo_root(task, db_path)
        target_branch = str(task.get("roadmap_integration_branch") or MAIN_BRANCH)
        key = (str(repo_root), target_branch, branch_name)
        if key not in cache:
            cache[key] = _resolve_branch_state(repo_root, branch_name, target_branch)
        task["branch_state"] = cache[key]


def _latest_task_log_path(task_id: int, db_path: Path) -> Path | None:
    """Return latest attempt log path for a task."""
    logs_root = db_path.parent / "logs" / f"task-{task_id}"
    if not logs_root.exists():
        return None
    candidates = sorted(logs_root.glob("attempt-*.log"))
    if not candidates:
        return None
    return candidates[-1]


def _parse_tokens_used(text: str) -> int | None:
    """Parse token usage from agent log text."""
    clean = ANSI_ESCAPE_RE.sub("", text)
    lines = clean.splitlines()[-TOKEN_SCAN_WINDOW_LINES:]
    tail = "\n".join(lines)

    total = _last_pattern_value(tail, TOTAL_TOKEN_PATTERNS)
    if total is not None:
        return total

    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if "tokens used" not in line.lower():
            continue
        for next_idx in range(idx + 1, min(idx + 4, len(lines))):
            match = TOKEN_LINE_RE.match(lines[next_idx])
            if match:
                parsed = _parse_int_token(match.group(1))
                if parsed is not None:
                    return parsed

    input_tokens = _last_pattern_value(tail, INPUT_TOKEN_PATTERNS)
    output_tokens = _last_pattern_value(tail, OUTPUT_TOKEN_PATTERNS)
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens

    return None


def _parse_int_token(value: str) -> int | None:
    """Parse integer token values with optional separators."""
    normalized = value.replace(",", "").replace("_", "").strip()
    if not normalized.isdigit():
        return None
    return int(normalized)


def _last_pattern_value(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    """Return the most recent numeric value matched by any regex in patterns."""
    best: tuple[int, int] | None = None
    for pattern in patterns:
        for match in pattern.finditer(text):
            parsed = _parse_int_token(match.group(1))
            if parsed is None:
                continue
            if best is None or match.start() > best[0]:
                best = (match.start(), parsed)
    return None if best is None else best[1]


def _resolve_tokens_used(task: dict[str, Any], db_path: Path) -> int | None:
    """Resolve observed token usage from latest task log."""
    if task.get("status") != "in-progress":
        return None
    log_path = _latest_task_log_path(int(task["id"]), db_path)
    if log_path is None:
        return None
    try:
        content = log_path.read_text(errors="replace")
    except OSError:
        return None
    return _parse_tokens_used(content)


def _annotate_token_usage(tasks: list[dict[str, Any]], db_path: Path) -> None:
    """Attach token usage metadata for status rendering."""
    for task in tasks:
        task["tokens_used"] = _resolve_tokens_used(task, db_path)


def _parse_started_at(value: Any) -> datetime | None:
    """Parse persisted task started_at timestamps."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _resolve_runtime_used_minutes(task: dict[str, Any]) -> float | None:
    """Resolve elapsed runtime minutes for in-progress tasks."""
    if task.get("status") != "in-progress":
        return None
    started_at = _parse_started_at(task.get("started_at"))
    if started_at is None:
        return None
    elapsed = (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds() / 60.0
    if elapsed < 0:
        return 0.0
    return elapsed


def _resolve_budget_metadata(task: dict[str, Any]) -> dict[str, Any]:
    """Resolve machine-readable budget pressure metadata for one task."""
    max_tokens = task.get("max_tokens")
    max_runtime_min = task.get("max_runtime_min")
    normalized_max_tokens = (
        int(max_tokens)
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0
        else None
    )
    normalized_max_runtime_min = (
        int(max_runtime_min)
        if (
            isinstance(max_runtime_min, int)
            and not isinstance(max_runtime_min, bool)
            and max_runtime_min > 0
        )
        else None
    )
    has_budget = normalized_max_tokens is not None or normalized_max_runtime_min is not None

    tokens_used = task.get("tokens_used")
    normalized_tokens_used = (
        int(tokens_used)
        if isinstance(tokens_used, int) and not isinstance(tokens_used, bool) and tokens_used >= 0
        else None
    )
    runtime_used_min = _resolve_runtime_used_minutes(task)

    token_ratio = (
        normalized_tokens_used / normalized_max_tokens
        if normalized_tokens_used is not None and normalized_max_tokens is not None
        else None
    )
    runtime_ratio = (
        runtime_used_min / normalized_max_runtime_min
        if runtime_used_min is not None and normalized_max_runtime_min is not None
        else None
    )

    pressure_ratio: float | None = None
    pressure_source = "none"
    pressure_level = "none"
    ratio_candidates: list[tuple[str, float]] = []
    if token_ratio is not None:
        ratio_candidates.append(("tokens", token_ratio))
    if runtime_ratio is not None:
        ratio_candidates.append(("runtime", runtime_ratio))

    if has_budget:
        if not ratio_candidates:
            pressure_level = "configured"
        else:
            pressure_source, pressure_ratio = max(ratio_candidates, key=lambda item: item[1])
            if pressure_ratio >= 1.0:
                pressure_level = "exceeded"
            elif pressure_ratio >= BUDGET_PRESSURE_WARN_THRESHOLD:
                pressure_level = "warn"
            else:
                pressure_level = "ok"

    return {
        "has_budget": has_budget,
        "max_tokens": normalized_max_tokens,
        "max_runtime_min": normalized_max_runtime_min,
        "tokens_used": normalized_tokens_used,
        "runtime_used_min": (
            round(runtime_used_min, 2)
            if isinstance(runtime_used_min, float)
            else None
        ),
        "token_ratio": round(token_ratio, 4) if isinstance(token_ratio, float) else None,
        "runtime_ratio": round(runtime_ratio, 4) if isinstance(runtime_ratio, float) else None,
        "pressure_level": pressure_level,
        "pressure_source": pressure_source,
        "pressure_ratio": round(pressure_ratio, 4) if isinstance(pressure_ratio, float) else None,
    }


def _annotate_budget_metadata(tasks: list[dict[str, Any]]) -> None:
    """Attach budget metadata for status rendering."""
    for task in tasks:
        budget = _resolve_budget_metadata(task)
        task["budget"] = budget
        task["budget_state"] = str(budget.get("pressure_level") or "none")
        task["budget_source"] = str(budget.get("pressure_source") or "none")


def _parse_reconcile_source(task: dict[str, Any]) -> dict[str, Any] | None:
    """Parse reconcile source metadata from auto-generated reconcile descriptions."""
    description = task.get("description")
    if not isinstance(description, str):
        return None
    if "**Reconcile Source Task ID:**" not in description:
        return None

    source_task_id: int | None = None
    source_task_number: str | None = None

    source_id_match = RECONCILE_SOURCE_TASK_ID_RE.search(description)
    if source_id_match is not None:
        source_task_id = int(source_id_match.group(1))

    source_number_match = RECONCILE_SOURCE_TASK_NUMBER_RE.search(description)
    if source_number_match is not None:
        raw_number = source_number_match.group(1).strip()
        if raw_number:
            source_task_number = raw_number[1:] if raw_number[0] in {"P", "p"} else raw_number

    return {
        "source_task_id": source_task_id,
        "source_task_number": source_task_number,
    }


def _annotate_reconcile_metadata(tasks: list[dict[str, Any]]) -> None:
    """Attach reconcile workflow metadata for status rendering."""
    reconcile_source_by_task_id: dict[int, dict[str, Any] | None] = {}
    linked_reconcile_tasks: dict[int, list[dict[str, Any]]] = {}

    for task in tasks:
        task_id = int(task["id"])
        reconcile_source = _parse_reconcile_source(task)
        reconcile_source_by_task_id[task_id] = reconcile_source
        if reconcile_source is None:
            continue

        source_task_id = reconcile_source.get("source_task_id")
        if not isinstance(source_task_id, int):
            continue
        linked_reconcile_tasks.setdefault(source_task_id, []).append(
            {
                "task_id": task_id,
                "task_number": str(task.get("task_number") or ""),
                "status": str(task.get("status") or ""),
            }
        )

    for source_task_id in linked_reconcile_tasks:
        linked_reconcile_tasks[source_task_id].sort(key=lambda linked: int(linked["task_id"]))

    for task in tasks:
        task_id = int(task["id"])
        reconcile_source = reconcile_source_by_task_id.get(task_id)
        linked = linked_reconcile_tasks.get(task_id, [])
        state = RECONCILE_STATE_NONE

        if reconcile_source is not None:
            state = RECONCILE_STATE_TASK
        elif linked:
            if any(
                str(linked_task.get("status") or "") in RECONCILE_ACTIVE_STATUSES
                for linked_task in linked
            ):
                state = RECONCILE_STATE_SOURCE_ACTIVE
            else:
                state = RECONCILE_STATE_SOURCE_CLOSED

        task["reconcile"] = {
            "state": state,
            "is_reconcile_task": reconcile_source is not None,
            "source_task_id": reconcile_source.get("source_task_id") if reconcile_source else None,
            "source_task_number": (
                reconcile_source.get("source_task_number") if reconcile_source else None
            ),
            "linked_tasks": linked,
        }
        task["reconcile_state"] = state


def _normalize_conflict_blockers(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize overlap conflict rows to stable JSON-friendly metadata."""
    blockers: list[dict[str, Any]] = []
    for conflict in conflicts:
        blockers.append(
            {
                "task_id": int(conflict["task_id"]),
                "task_number": str(conflict["task_number"]),
                "title": str(conflict["title"]),
                "target_paths": [str(path) for path in conflict.get("target_paths", [])],
            }
        )
    return blockers


def _resolve_hold_metadata(task: dict[str, Any], store: Store) -> dict[str, Any] | None:
    """Resolve scheduler hold metadata for one queued task."""
    if task.get("status") != "queued":
        return None
    conflicts = store.list_in_progress_overlap_conflicts(int(task["id"]))
    if not conflicts:
        return None
    return {
        "reason": HOLD_REASON_OVERLAP_CONFLICT,
        "blocking_tasks": _normalize_conflict_blockers(conflicts),
    }


def _annotate_hold_metadata(tasks: list[dict[str, Any]], store: Store) -> None:
    """Attach hold metadata for queued tasks."""
    for task in tasks:
        task["hold"] = _resolve_hold_metadata(task, store)


def _format_hold(task: dict[str, Any]) -> str:
    """Format hold metadata for fixed-width status table rendering."""
    hold = task.get("hold")
    if not isinstance(hold, dict):
        return ""
    reason = hold.get("reason")
    if reason != HOLD_REASON_OVERLAP_CONFLICT:
        return str(reason or "")
    blockers = hold.get("blocking_tasks")
    if not isinstance(blockers, list) or not blockers:
        return "conflict with in-progress task"
    first = blockers[0]
    task_number = str(first.get("task_number") or "unknown")
    target_paths = first.get("target_paths")
    path = ""
    if isinstance(target_paths, list) and target_paths:
        path = str(target_paths[0])
    summary = f"conflict with {task_number}"
    if path:
        summary += f" ({path})"
    if len(blockers) > 1:
        summary += f" +{len(blockers) - 1} more"
    return summary


def _format_budget(task: dict[str, Any]) -> str:
    """Format budget pressure metadata for fixed-width status table rendering."""
    budget = task.get("budget")
    if not isinstance(budget, dict):
        return BUDGET_NA

    pressure_level = str(budget.get("pressure_level") or "none")
    if pressure_level == "none":
        return BUDGET_NA

    if pressure_level == "configured":
        parts: list[str] = []
        max_tokens = budget.get("max_tokens")
        if isinstance(max_tokens, int):
            parts.append(f"tok<={max_tokens:,}")
        max_runtime = budget.get("max_runtime_min")
        if isinstance(max_runtime, int):
            parts.append(f"run<={max_runtime}m")
        return " ".join(parts) if parts else "set"

    ratio = budget.get("pressure_ratio")
    if not isinstance(ratio, (int, float)):
        return pressure_level

    pct = int(round(float(ratio) * 100))
    level_display = {
        "ok": "ok",
        "warn": "warn",
        "exceeded": "over",
    }.get(pressure_level, pressure_level)
    source_display = {
        "tokens": "tok",
        "runtime": "run",
        "mixed": "mix",
    }.get(str(budget.get("pressure_source") or ""), "")
    if source_display:
        return f"{level_display} {pct}% {source_display}"
    return f"{level_display} {pct}%"


def _format_reconcile(task: dict[str, Any]) -> str:
    """Format reconcile metadata for fixed-width status table rendering."""
    reconcile = task.get("reconcile")
    if not isinstance(reconcile, dict):
        return RECONCILE_NA

    state = str(reconcile.get("state") or RECONCILE_STATE_NONE)
    if state == RECONCILE_STATE_TASK:
        source_task_number = reconcile.get("source_task_number")
        if isinstance(source_task_number, str) and source_task_number:
            return f"task<-{source_task_number}"
        source_task_id = reconcile.get("source_task_id")
        if isinstance(source_task_id, int):
            return f"task<-#{source_task_id}"
        return "task<-unknown"

    linked = reconcile.get("linked_tasks")
    if not isinstance(linked, list) or not linked:
        return RECONCILE_NA

    active_linked = [
        linked_task
        for linked_task in linked
        if str(linked_task.get("status") or "") in RECONCILE_ACTIVE_STATUSES
    ]
    selected = active_linked if active_linked else linked
    first = selected[0]
    linked_number = str(first.get("task_number") or first.get("task_id") or "unknown")
    linked_status = str(first.get("status") or "unknown")
    label = "active->" if active_linked else "done->"
    suffix = f"+{len(selected) - 1}" if len(selected) > 1 else ""
    return f"{label}{linked_number}:{linked_status}{suffix}"


def _format_conflict_file_preview(conflict_files: list[Any]) -> str:
    """Format conflict file list for compact status output."""
    preview = ", ".join(str(path) for path in conflict_files[:MERGE_CONFLICT_FILE_PREVIEW])
    if len(conflict_files) > MERGE_CONFLICT_FILE_PREVIEW:
        preview = f"{preview}, +{len(conflict_files) - MERGE_CONFLICT_FILE_PREVIEW} more"
    return preview


def _summarize_merge_diagnostic(attempt: dict[str, Any]) -> str | None:
    """Render concise summary for one task merge attempt."""
    status = str(attempt.get("status") or "").strip().lower()
    if not status:
        return None
    if status == "succeeded":
        return None

    detail = attempt.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        clean_detail = MERGE_DIAGNOSTIC_WHITESPACE_RE.sub(" ", detail.strip())
        return f"{status}: {clean_detail}"

    conflict_files = attempt.get("conflict_files")
    has_conflict_files = isinstance(conflict_files, list) and bool(conflict_files)
    conflict_files_preview = _format_conflict_file_preview(conflict_files) if has_conflict_files else ""

    conflict_type = attempt.get("conflict_type")
    if isinstance(conflict_type, str) and conflict_type.strip():
        conflict_summary = conflict_type.strip()
        if has_conflict_files:
            return f"{status}: {conflict_summary} ({conflict_files_preview})"
        return f"{status}: {conflict_summary}"

    if has_conflict_files:
        return f"{status}: files {conflict_files_preview}"

    source_branch = str(attempt.get("source_branch") or "").strip()
    target_branch = str(attempt.get("target_branch") or "").strip()
    if source_branch and target_branch:
        return f"{status}: {source_branch} -> {target_branch}"

    return status


def _latest_merge_attempt_summary(store: Store, task_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """Return newest merge attempt row and display summary for one task."""
    attempts = store.list_task_merge_attempts(task_id=task_id, limit=1)
    if not attempts:
        return None, None
    latest = attempts[0]
    return latest, _summarize_merge_diagnostic(latest)


def _annotate_merge_diagnostics(tasks: list[dict[str, Any]], store: Store) -> None:
    """Attach latest merge attempt metadata to tasks."""
    for task in tasks:
        latest, summary = _latest_merge_attempt_summary(store, int(task["id"]))
        task["latest_merge_attempt"] = latest
        task["merge_diagnostic"] = summary


def _format_attempts(task: dict[str, Any]) -> str:
    """Format attempts as '<attempts>/<max_attempts>' for status output."""
    attempts = task.get("attempts")
    max_attempts = task.get("max_attempts")
    if not isinstance(attempts, int) or not isinstance(max_attempts, int):
        return "n/a"
    return f"{attempts}/{max_attempts}"


def handle_status(args: Any, db_path: Path) -> None:
    """Handle `yeehaw status` output."""
    store = Store(db_path)
    try:
        project_id = None
        if args.project:
            project = store.get_project(args.project)
            if not project:
                print(f"Error: Project '{args.project}' not found.")
                return
            project_id = project["id"]

        tasks = sorted(
            store.list_tasks(project_id=project_id),
            key=lambda task: int(task["id"]),
        )
        _annotate_branch_states(tasks, db_path)
        _annotate_token_usage(tasks, db_path)
        _annotate_budget_metadata(tasks)
        _annotate_hold_metadata(tasks, store)
        _annotate_reconcile_metadata(tasks)
        _annotate_merge_diagnostics(tasks, store)

        if args.as_json:
            print(json_module.dumps(tasks, indent=2, default=str))
            return

        if not tasks:
            print("No tasks.")
            return

        header = (
            f"{'ID':<6} {'Task':<10} {'Title':<{TITLE_WIDTH}} "
            f"{'Status':<14} {'Agent':<10} {'Branch':<{BRANCH_WIDTH}} "
            f"{'Attempts':<{ATTEMPTS_WIDTH}} {'Tokens':<{TOKENS_WIDTH}} "
            f"{'Budget':<{BUDGET_WIDTH}} "
            f"{'Hold':<{HOLD_WIDTH}} "
            f"{'Reconcile':<{RECONCILE_WIDTH}} "
            f"{'Merge':<{MERGE_DIAGNOSTIC_WIDTH}}"
        )
        print(header)
        print("-" * len(header))
        for task in tasks:
            agent = task.get("assigned_agent") or ""
            title = _truncate_for_column(task["title"], TITLE_WIDTH)
            branch_state = task.get("branch_state") or BRANCH_NA
            attempts_display = _format_attempts(task)
            tokens_used = task.get("tokens_used")
            tokens_display = (
                f"{int(tokens_used):,}"
                if isinstance(tokens_used, int)
                else TOKENS_NA
            )
            budget_display = _truncate_for_column(_format_budget(task), BUDGET_WIDTH)
            hold_display = _truncate_for_column(_format_hold(task), HOLD_WIDTH)
            reconcile_display = _truncate_for_column(_format_reconcile(task), RECONCILE_WIDTH)
            merge_diagnostic = task.get("merge_diagnostic")
            merge_display = (
                _truncate_for_column(str(merge_diagnostic), MERGE_DIAGNOSTIC_WIDTH)
                if isinstance(merge_diagnostic, str) and merge_diagnostic
                else MERGE_DIAGNOSTIC_NA
            )
            print(
                f"{task['id']:<6} {task['task_number']:<10} {title:<{TITLE_WIDTH}} "
                f"{task['status']:<14} {agent:<10} {branch_state:<{BRANCH_WIDTH}} "
                f"{attempts_display:<{ATTEMPTS_WIDTH}} {tokens_display:<{TOKENS_WIDTH}} "
                f"{budget_display:<{BUDGET_WIDTH}} "
                f"{hold_display:<{HOLD_WIDTH}} "
                f"{reconcile_display:<{RECONCILE_WIDTH}} "
                f"{merge_display:<{MERGE_DIAGNOSTIC_WIDTH}}"
            )

        by_status: dict[str, int] = {}
        for task in tasks:
            by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        parts = [f"{value} {status}" for status, value in sorted(by_status.items())]
        print(f"\nTotal: {len(tasks)} tasks ({', '.join(parts)})")

    finally:
        store.close()


def handle_alerts(args: Any, db_path: Path) -> None:
    """Handle `yeehaw alerts` output and acknowledgements."""
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

        for alert in alerts:
            print(
                f"[{alert['severity'].upper()}] "
                f"#{alert['id']} - {alert['message']} ({alert['created_at']})"
            )
    finally:
        store.close()
