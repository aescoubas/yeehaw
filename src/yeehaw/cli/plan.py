"""AI planning session command."""

from __future__ import annotations

import argparse

from pathlib import Path
from typing import Any

from yeehaw.planner.session import start_planner_session
from yeehaw.store.store import Store


def handle_plan(args: argparse.Namespace, db_path: Path) -> None:
    """Launch planner agent session backed by yeehaw MCP tools."""
    briefing_arg = getattr(args, "briefing", None)
    project_name = getattr(args, "project", None)
    agent_name = getattr(args, "agent", "codex")

    briefing = Path(briefing_arg) if briefing_arg else None
    if briefing and not briefing.exists():
        print(f"Error: Briefing file '{briefing_arg}' not found.")
        return

    if project_name:
        store = Store(db_path)
        try:
            project = store.get_project(project_name)
            if not project:
                print(f"Error: Project '{project_name}' not found.")
                return
        finally:
            store.close()

    project_segment = f", project={project_name}" if project_name else ""
    print(
        f"Starting interactive planner session (agent={agent_name}{project_segment})...",
        flush=True,
    )
    try:
        start_planner_session(
            db_path,
            briefing_file=briefing,
            agent=agent_name,
            project_name=project_name,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}")
