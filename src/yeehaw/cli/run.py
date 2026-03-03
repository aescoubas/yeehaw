"""Orchestrator run command."""

from __future__ import annotations

import argparse

from pathlib import Path
from typing import Any

from yeehaw.orchestrator.engine import Orchestrator
from yeehaw.store.store import Store


def handle_run(args: argparse.Namespace, db_path: Path) -> None:
    """Launch orchestrator loop."""
    store = Store(db_path)
    repo_root = Path.cwd()
    runtime_root = db_path.parent

    project_id = None
    if args.project:
        project = store.get_project(args.project)
        if not project:
            print(f"Error: Project '{args.project}' not found.")
            store.close()
            return
        project_id = project["id"]

    default_agent = getattr(args, "agent", None)
    print("Starting orchestrator... (Ctrl+C to stop)")
    try:
        orchestrator = Orchestrator(
            store,
            repo_root,
            runtime_root=runtime_root,
            default_agent=default_agent,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        store.close()
        return
    try:
        orchestrator.run(project_id=project_id)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        store.close()
