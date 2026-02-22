"""Task log inspection command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yeehaw.store.store import Store


def handle_logs(args: Any, db_path: Path) -> None:
    """Show recorded agent output for a task attempt."""
    store = Store(db_path)
    try:
        task = store.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found.")
            return

        logs_dir = db_path.parent.parent / ".yeehaw" / "logs" / f"task-{task['id']}"
        if not logs_dir.exists():
            print(f"No logs found for task {task['id']}.")
            return

        if args.attempt is not None:
            pattern = f"attempt-{args.attempt:02d}-*.log"
        else:
            pattern = "attempt-*.log"

        candidates = sorted(logs_dir.glob(pattern))
        if not candidates:
            if args.attempt is None:
                print(f"No logs found for task {task['id']}.")
            else:
                print(f"No logs found for task {task['id']} attempt {args.attempt}.")
            return

        log_path = candidates[-1]
        content = log_path.read_text(errors="replace")
        lines = content.splitlines()

        tail = max(1, int(args.tail))
        shown_lines = lines[-tail:]

        print(f"Log file: {log_path}")
        print(f"Showing last {len(shown_lines)} line(s)")
        print()
        if shown_lines:
            print("\n".join(shown_lines))
    finally:
        store.close()
