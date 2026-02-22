"""Task log inspection command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

FOLLOW_POLL_INTERVAL_SEC = 1.0


def handle_logs(args: Any, db_path: Path) -> None:
    """Show recorded agent output for a task attempt."""
    store = Store(db_path)
    try:
        task = store.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found.")
            return

        logs_dir = db_path.parent / "logs" / f"task-{task['id']}"
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
        follow = bool(getattr(args, "follow", False))
        shown_lines = lines[-tail:]

        print(f"Log file: {log_path}")
        print(f"Showing last {len(shown_lines)} line(s)")
        if follow:
            print("Following live output... (Ctrl+C to stop)")
        print()
        if shown_lines:
            print("\n".join(shown_lines))

        if follow:
            _follow_log_file(log_path, last_seen_line_count=len(lines))
    finally:
        store.close()


def _follow_log_file(log_path: Path, last_seen_line_count: int) -> None:
    """Stream appended lines from a log file until interrupted."""
    try:
        while True:
            time.sleep(FOLLOW_POLL_INTERVAL_SEC)
            try:
                current_lines = log_path.read_text(errors="replace").splitlines()
            except OSError:
                continue

            if len(current_lines) < last_seen_line_count:
                last_seen_line_count = 0

            if len(current_lines) == last_seen_line_count:
                continue

            new_lines = current_lines[last_seen_line_count:]
            if new_lines:
                print("\n".join(new_lines))
            last_seen_line_count = len(current_lines)
    except KeyboardInterrupt:
        print("\nStopped following.")
