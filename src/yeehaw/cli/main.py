"""Yeehaw CLI - Multi-agent coding orchestrator."""

from __future__ import annotations

import argparse
from pathlib import Path

from yeehaw.runtime import default_db_path


def _get_db_path() -> Path:
    """Resolve default database path in Yeehaw runtime directory."""
    return default_db_path()


def main(argv: list[str] | None = None) -> None:
    """Parse CLI args and dispatch command handlers."""
    parser = argparse.ArgumentParser(
        prog="yeehaw",
        description="Multi-agent coding orchestrator",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize yeehaw in the current directory")

    project_parser = subparsers.add_parser("project", help="Manage projects")
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)

    add_project = project_sub.add_parser("add", help="Register a project")
    add_project.add_argument("name", help="Project name")
    add_project.add_argument(
        "--repo",
        default=".",
        help="Repository root (default: current dir)",
    )

    project_sub.add_parser("list", help="List projects")

    remove_project = project_sub.add_parser("remove", help="Remove a project")
    remove_project.add_argument("name", help="Project name to remove")

    roadmap_parser = subparsers.add_parser("roadmap", help="Manage roadmaps")
    roadmap_sub = roadmap_parser.add_subparsers(dest="roadmap_command", required=True)

    create_roadmap = roadmap_sub.add_parser(
        "create",
        help="Create roadmap from markdown file",
    )
    create_roadmap.add_argument("file", help="Markdown file path")
    create_roadmap.add_argument("--project", required=True, help="Project name")

    show_roadmap = roadmap_sub.add_parser("show", help="Show active roadmap")
    show_roadmap.add_argument("--project", required=True, help="Project name")

    approve_roadmap = roadmap_sub.add_parser("approve", help="Approve roadmap for execution")
    approve_roadmap.add_argument("--project", required=True, help="Project name")

    clear_roadmap = roadmap_sub.add_parser("clear", help="Clear active roadmap")
    clear_roadmap.add_argument("--project", required=True, help="Project name")

    generate_roadmap = roadmap_sub.add_parser(
        "generate",
        help="Generate roadmap from natural-language text",
    )
    generate_roadmap.add_argument("--project", required=True, help="Project name")
    source_group = generate_roadmap.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--prompt",
        help="Natural-language prompt describing project goals and constraints",
    )
    source_group.add_argument(
        "--file",
        help="Path to text/markdown briefing file",
    )
    generate_roadmap.add_argument(
        "--agent",
        default="codex",
        choices=["claude", "gemini", "codex"],
        help="Planner agent for roadmap generation (default: codex)",
    )
    generate_roadmap.add_argument(
        "--approve",
        action="store_true",
        help="Approve generated roadmap immediately",
    )

    plan_parser = subparsers.add_parser("plan", help="Start interactive AI planning session")
    plan_parser.add_argument("briefing", nargs="?", help="Briefing file (optional)")
    plan_parser.add_argument("--project", help="Existing project name to plan for")
    plan_parser.add_argument(
        "--agent",
        default="codex",
        choices=["claude", "gemini", "codex"],
        help="Planner agent (default: codex)",
    )

    run_parser = subparsers.add_parser("run", help="Start the orchestrator")
    run_parser.add_argument("--project", help="Limit to a specific project")
    run_parser.add_argument(
        "--agent",
        choices=["claude", "gemini", "codex"],
        help="Default worker agent for unassigned tasks",
    )

    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Manage persistent orchestrator daemon (systemd user service)",
    )
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_command", required=True)

    install_daemon = daemon_sub.add_parser("install", help="Install user systemd service")
    install_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )
    install_daemon.add_argument(
        "--agent",
        choices=["claude", "gemini", "codex"],
        help="Default worker agent for unassigned tasks",
    )
    install_daemon.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing unit file",
    )
    install_daemon.add_argument(
        "--no-enable",
        action="store_true",
        help="Do not enable the service",
    )
    install_daemon.add_argument(
        "--no-start",
        action="store_true",
        help="Do not start the service after install",
    )

    uninstall_daemon = daemon_sub.add_parser(
        "uninstall",
        help="Stop/disable and remove user systemd service",
    )
    uninstall_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )

    start_daemon = daemon_sub.add_parser("start", help="Start user systemd service")
    start_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )

    stop_daemon = daemon_sub.add_parser("stop", help="Stop user systemd service")
    stop_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )

    restart_daemon = daemon_sub.add_parser("restart", help="Restart user systemd service")
    restart_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )

    status_daemon = daemon_sub.add_parser("status", help="Show user systemd service status")
    status_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )

    logs_daemon = daemon_sub.add_parser("logs", help="Show daemon journal logs")
    logs_daemon.add_argument(
        "--service-name",
        default="yeehaw-orchestrator",
        help="Systemd user service name (default: yeehaw-orchestrator)",
    )
    logs_daemon.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Number of log lines to show (default: 200)",
    )
    logs_daemon.add_argument(
        "--follow",
        action="store_true",
        help="Follow daemon logs",
    )

    status_parser = subparsers.add_parser("status", help="Show task status")
    status_parser.add_argument("--project", help="Filter by project")
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )

    attach_parser = subparsers.add_parser(
        "attach",
        help="Attach to a worker's tmux session",
    )
    attach_parser.add_argument("task_id", type=int, help="Task ID")

    stop_parser = subparsers.add_parser("stop", help="Stop a running task")
    stop_parser.add_argument("task_id", nargs="?", type=int, help="Task ID")
    stop_parser.add_argument("--all", action="store_true", help="Stop all tasks")

    logs_parser = subparsers.add_parser("logs", help="Show task execution logs")
    logs_parser.add_argument("task_id", type=int, help="Task ID")
    logs_parser.add_argument(
        "--attempt",
        type=int,
        help="Specific attempt number (default: latest)",
    )
    logs_parser.add_argument(
        "--tail",
        type=int,
        default=200,
        help="Number of trailing log lines to show (default: 200)",
    )
    logs_parser.add_argument(
        "--follow",
        action="store_true",
        help="Follow log output live",
    )

    scheduler_parser = subparsers.add_parser("scheduler", help="Manage scheduler config")
    scheduler_sub = scheduler_parser.add_subparsers(
        dest="scheduler_command",
        required=True,
    )

    scheduler_sub.add_parser("show", help="Show scheduler configuration")

    config_parser = scheduler_sub.add_parser(
        "config",
        help="Update scheduler configuration",
    )
    config_parser.add_argument("--max-global", type=int, help="Max concurrent tasks globally")
    config_parser.add_argument("--max-project", type=int, help="Max concurrent tasks per project")
    config_parser.add_argument("--tick", type=int, help="Tick interval in seconds")
    config_parser.add_argument("--timeout", type=int, help="Task timeout in minutes")

    alerts_parser = subparsers.add_parser("alerts", help="Show alerts")
    alerts_parser.add_argument(
        "--ack",
        type=int,
        metavar="ID",
        help="Acknowledge alert by ID",
    )

    workers_parser = subparsers.add_parser("workers", help="Show worker runtime configuration")
    workers_sub = workers_parser.add_subparsers(dest="workers_command", required=True)
    workers_sub.add_parser("show", help="Show effective worker launch configuration")

    args = parser.parse_args(argv)

    if args.command == "init":
        from yeehaw.cli.project import handle_init

        handle_init(_get_db_path())

    elif args.command == "project":
        from yeehaw.cli.project import handle_project

        handle_project(args, _get_db_path())

    elif args.command == "roadmap":
        from yeehaw.cli.roadmap import handle_roadmap

        handle_roadmap(args, _get_db_path())

    elif args.command == "plan":
        from yeehaw.cli.plan import handle_plan

        handle_plan(args, _get_db_path())

    elif args.command == "run":
        from yeehaw.cli.run import handle_run

        handle_run(args, _get_db_path())

    elif args.command == "daemon":
        from yeehaw.cli.daemon import handle_daemon

        handle_daemon(args, _get_db_path())

    elif args.command == "status":
        from yeehaw.cli.status import handle_status

        handle_status(args, _get_db_path())

    elif args.command == "attach":
        from yeehaw.cli.attach import handle_attach

        handle_attach(args, _get_db_path())

    elif args.command == "stop":
        from yeehaw.cli.stop import handle_stop

        handle_stop(args, _get_db_path())

    elif args.command == "logs":
        from yeehaw.cli.logs import handle_logs

        handle_logs(args, _get_db_path())

    elif args.command == "scheduler":
        from yeehaw.cli.scheduler import handle_scheduler

        handle_scheduler(args, _get_db_path())

    elif args.command == "alerts":
        from yeehaw.cli.status import handle_alerts

        handle_alerts(args, _get_db_path())

    elif args.command == "workers":
        from yeehaw.cli.workers import handle_workers

        handle_workers(args, _get_db_path())
