"""Project management commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yeehaw.store.store import Store


def handle_init(db_path: Path) -> None:
    """Initialize yeehaw metadata directory and database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    store.close()
    print(f"Initialized yeehaw at {db_path.parent}")


def handle_project(args: Any, db_path: Path) -> None:
    """Handle `yeehaw project` subcommands."""
    store = Store(db_path)
    try:
        if args.project_command == "add":
            repo_root = str(Path(args.repo).resolve())
            project_id = store.create_project(args.name, repo_root)
            print(f"Project '{args.name}' created (id={project_id})")

        elif args.project_command == "list":
            projects = store.list_projects()
            if not projects:
                print("No projects.")
                return
            print(f"{'ID':<6} {'Name':<30} {'Repo Root'}")
            print("-" * 70)
            for project in projects:
                print(f"{project['id']:<6} {project['name']:<30} {project['repo_root']}")

        elif args.project_command == "remove":
            if store.delete_project(args.name):
                print(f"Project '{args.name}' removed.")
            else:
                print(f"Project '{args.name}' not found.")
    finally:
        store.close()
