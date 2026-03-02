"""Task log inspection command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from yeehaw.store.store import Store

FOLLOW_POLL_INTERVAL_SEC = 1.0
MERGE_HISTORY_DEFAULT_LIMIT = 20
MERGE_CONFLICT_FILE_PREVIEW = 5


def handle_logs(args: Any, db_path: Path) -> None:
    """Show recorded agent output for a task attempt."""
    store = Store(db_path)
    try:
        task = store.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found.")
            return

        if bool(getattr(args, "merge_history", False)):
            history_limit = max(
                1,
                int(getattr(args, "history_limit", MERGE_HISTORY_DEFAULT_LIMIT)),
            )
            _show_merge_history(
                store,
                task_id=int(task["id"]),
                limit=history_limit,
            )
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


def _show_merge_history(store: Store, *, task_id: int, limit: int) -> None:
    """Render merge/rebase attempt history for one task."""
    attempts = store.list_task_merge_attempts(task_id=task_id, limit=limit)
    if not attempts:
        print(f"No merge history found for task {task_id}.")
        return

    print(f"Merge history for task {task_id} (latest first)")
    for attempt in attempts:
        print(_format_merge_attempt_header(attempt))

        conflict_type = attempt.get("conflict_type")
        if isinstance(conflict_type, str) and conflict_type:
            print(f"  conflict: {conflict_type}")

        conflict_files = attempt.get("conflict_files")
        if isinstance(conflict_files, list) and conflict_files:
            preview = ", ".join(str(path) for path in conflict_files[:MERGE_CONFLICT_FILE_PREVIEW])
            if len(conflict_files) > MERGE_CONFLICT_FILE_PREVIEW:
                remaining = len(conflict_files) - MERGE_CONFLICT_FILE_PREVIEW
                preview = f"{preview} (+{remaining} more)"
            print(f"  files: {preview}")

        error_detail = attempt.get("error_detail")
        if isinstance(error_detail, str) and error_detail:
            print(f"  detail: {error_detail}")

        started_at = str(attempt.get("started_at") or "n/a")
        completed_at = str(attempt.get("completed_at") or "n/a")
        print(f"  started: {started_at}")
        print(f"  completed: {completed_at}")


def _format_merge_attempt_header(attempt: dict[str, Any]) -> str:
    """Format one merge attempt summary line."""
    attempt_number = attempt.get("attempt_number")
    number = str(attempt_number) if isinstance(attempt_number, int) else "?"
    status = str(attempt.get("status") or "unknown")
    source_branch = str(attempt.get("source_branch") or "n/a")
    target_branch = str(attempt.get("target_branch") or "n/a")
    return f"Attempt {number}: {status} ({source_branch} -> {target_branch})"
