"""Stop running tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yeehaw.git.worktree import cleanup_worktree
from yeehaw.store.store import Store
from yeehaw.tmux.session import has_session, kill_session


def handle_stop(args: Any, db_path: Path) -> None:
    """Stop one or all in-progress tasks and clean resources."""
    store = Store(db_path)
    repo_root = db_path.parent.parent

    try:
        if args.all:
            tasks = store.list_tasks(status="in-progress")
        elif args.task_id:
            task = store.get_task(args.task_id)
            tasks = [task] if task else []
        else:
            print("Specify a task ID or --all.")
            return

        for task in tasks:
            if not task:
                continue
            session = f"yeehaw-task-{task['id']}"
            if has_session(session):
                kill_session(session)
            if task.get("worktree_path"):
                cleanup_worktree(repo_root, Path(task["worktree_path"]))
            store.fail_task(task["id"], "Manually stopped")
            store.log_event(
                "task_stopped",
                f"Task {task['id']} stopped by user",
                task_id=task["id"],
            )
            print(f"Stopped task {task['id']}: {task['title']}")

        if not tasks:
            print("No matching tasks found.")
    finally:
        store.close()
