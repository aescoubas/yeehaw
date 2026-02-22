"""Status and alerts display commands."""

from __future__ import annotations

import json as json_module
import re
import subprocess
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

TITLE_WIDTH = 35
BRANCH_WIDTH = 8
ATTEMPTS_WIDTH = 8
TOKENS_WIDTH = 12
BRANCH_NA = "n/a"
BRANCH_AHEAD = "ahead"
BRANCH_DIVERGED = "diverged"
BRANCH_MERGED = "merged"
MAIN_BRANCH = "main"
TOKENS_NA = "n/a"
TOKEN_SCAN_WINDOW_LINES = 400
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
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

        if args.as_json:
            print(json_module.dumps(tasks, indent=2, default=str))
            return

        if not tasks:
            print("No tasks.")
            return

        header = (
            f"{'ID':<6} {'Task':<10} {'Title':<{TITLE_WIDTH}} "
            f"{'Status':<14} {'Agent':<10} {'Branch':<{BRANCH_WIDTH}} "
            f"{'Attempts':<{ATTEMPTS_WIDTH}} {'Tokens':<{TOKENS_WIDTH}}"
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
            print(
                f"{task['id']:<6} {task['task_number']:<10} {title:<{TITLE_WIDTH}} "
                f"{task['status']:<14} {agent:<10} {branch_state:<{BRANCH_WIDTH}} "
                f"{attempts_display:<{ATTEMPTS_WIDTH}} {tokens_display:<{TOKENS_WIDTH}}"
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
