"""AI planning session command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yeehaw.planner.session import start_planner_session


def handle_plan(args: Any, db_path: Path) -> None:
    """Launch planner agent session backed by yeehaw MCP tools."""
    briefing = Path(args.briefing) if args.briefing else None
    if briefing and not briefing.exists():
        print(f"Error: Briefing file '{args.briefing}' not found.")
        return

    print(f"Starting planner session (agent={args.agent})...")
    start_planner_session(db_path, briefing_file=briefing, agent=args.agent)
