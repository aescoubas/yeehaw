"""Scheduler configuration commands."""

from __future__ import annotations

import argparse

from pathlib import Path
from typing import Any

from yeehaw.store.store import Store


def handle_scheduler(args: argparse.Namespace, db_path: Path) -> None:
    """Handle scheduler config show/update commands."""
    store = Store(db_path)
    try:
        if args.scheduler_command == "show":
            config = store.get_scheduler_config()
            print("Scheduler Configuration:")
            print(f"  Max global tasks:   {config['max_global_tasks']}")
            print(f"  Max per project:    {config['max_per_project']}")
            print(f"  Tick interval:      {config['tick_interval_sec']}s")
            print(f"  Task timeout:       {config['task_timeout_min']} min")

        elif args.scheduler_command == "config":
            updates: dict[str, int] = {}
            if args.max_global is not None:
                updates["max_global_tasks"] = args.max_global
            if args.max_project is not None:
                updates["max_per_project"] = args.max_project
            if args.tick is not None:
                updates["tick_interval_sec"] = args.tick
            if args.timeout is not None:
                updates["task_timeout_min"] = args.timeout

            if not updates:
                print("No changes specified.")
                return

            store.update_scheduler_config(**updates)
            print("Scheduler config updated:")
            for key, value in updates.items():
                print(f"  {key} = {value}")
    finally:
        store.close()
