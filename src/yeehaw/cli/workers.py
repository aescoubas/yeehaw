"""Worker runtime configuration display command."""

from __future__ import annotations

import argparse

import json
from pathlib import Path
from typing import Any

from yeehaw.agent.profiles import AGENT_REGISTRY
from yeehaw.agent.runtime_config import default_no_mcp_args, resolve_worker_launch_config


def handle_workers(args: argparse.Namespace, db_path: Path) -> None:
    """Handle `yeehaw workers` subcommands."""
    if args.workers_command == "show":
        _show_workers(db_path.parent)


def _show_workers(runtime_root: Path) -> None:
    """Show effective worker launch configuration per agent."""
    config_path = runtime_root / "workers.json"
    status = "found" if config_path.exists() else "not found"

    print("Worker Configuration:")
    print(f"  Config file: {config_path} ({status})")

    for agent_name in AGENT_REGISTRY:
        try:
            cfg = resolve_worker_launch_config(runtime_root, agent_name)
        except ValueError as exc:
            print(f"Error: {exc}")
            return

        mcp_args = default_no_mcp_args(agent_name) if cfg.disable_default_mcp else []

        print()
        print(f"[{agent_name}]")
        print(f"  disable_default_mcp: {str(cfg.disable_default_mcp).lower()}")
        if mcp_args:
            print(f"  default_no_mcp_args: {' '.join(mcp_args)}")
        else:
            print("  default_no_mcp_args: (none)")
        if cfg.extra_args:
            print(f"  extra_args: {' '.join(cfg.extra_args)}")
        else:
            print("  extra_args: (none)")
        if cfg.env:
            print(f"  env: {json.dumps(cfg.env, sort_keys=True)}")
        else:
            print("  env: {}")
