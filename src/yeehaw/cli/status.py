"""Status and alerts display commands."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

TITLE_WIDTH = 35


def _truncate_for_column(value: str, width: int) -> str:
    """Truncate text to fixed column width, adding ellipsis when needed."""
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."


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

        if args.as_json:
            print(json_module.dumps(tasks, indent=2, default=str))
            return

        if not tasks:
            print("No tasks.")
            return

        print(f"{'ID':<6} {'Task':<10} {'Title':<{TITLE_WIDTH}} {'Status':<14} {'Agent':<10}")
        print("-" * 80)
        for task in tasks:
            agent = task.get("assigned_agent") or ""
            title = _truncate_for_column(task["title"], TITLE_WIDTH)
            print(
                f"{task['id']:<6} {task['task_number']:<10} {title:<{TITLE_WIDTH}} "
                f"{task['status']:<14} {agent:<10}"
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
