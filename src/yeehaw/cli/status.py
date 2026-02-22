"""Status and alerts display commands."""

from __future__ import annotations

import json as json_module
import subprocess
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

TITLE_WIDTH = 35
BRANCH_WIDTH = 8
BRANCH_NA = "n/a"
BRANCH_AHEAD = "ahead"
BRANCH_DIVERGED = "diverged"
BRANCH_MERGED = "merged"
MAIN_BRANCH = "main"


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
    return db_path.parent.parent


def _resolve_branch_state(repo_root: Path, branch_name: str) -> str:
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
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{MAIN_BRANCH}"],
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
            f"refs/heads/{MAIN_BRANCH}...refs/heads/{branch_name}",
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
    cache: dict[tuple[str, str], str] = {}
    for task in tasks:
        branch_name = task.get("branch_name")
        if not isinstance(branch_name, str) or not branch_name:
            task["branch_state"] = BRANCH_NA
            continue
        repo_root = _task_repo_root(task, db_path)
        key = (str(repo_root), branch_name)
        if key not in cache:
            cache[key] = _resolve_branch_state(repo_root, branch_name)
        task["branch_state"] = cache[key]


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

        if args.as_json:
            print(json_module.dumps(tasks, indent=2, default=str))
            return

        if not tasks:
            print("No tasks.")
            return

        header = (
            f"{'ID':<6} {'Task':<10} {'Title':<{TITLE_WIDTH}} "
            f"{'Status':<14} {'Agent':<10} {'Branch':<{BRANCH_WIDTH}}"
        )
        print(header)
        print("-" * len(header))
        for task in tasks:
            agent = task.get("assigned_agent") or ""
            title = _truncate_for_column(task["title"], TITLE_WIDTH)
            branch_state = task.get("branch_state") or BRANCH_NA
            print(
                f"{task['id']:<6} {task['task_number']:<10} {title:<{TITLE_WIDTH}} "
                f"{task['status']:<14} {agent:<10} {branch_state:<{BRANCH_WIDTH}}"
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
