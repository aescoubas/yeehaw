"""Attach to a worker agent's tmux session."""

from __future__ import annotations

import argparse

from pathlib import Path
from typing import Any

from yeehaw.store.store import Store
from yeehaw.tmux.session import attach_session, has_session


def handle_attach(args: argparse.Namespace, db_path: Path) -> None:
    """Attach terminal to the selected task's tmux session."""
    store = Store(db_path)
    try:
        task = store.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found.")
            return

        session = f"yeehaw-task-{task['id']}"
        if not has_session(session):
            print(f"No active tmux session for task {task['id']}.")
            return

        print(f"Attaching to {session}... (Ctrl+b, d to detach)")
        attach_session(session)
    finally:
        store.close()
