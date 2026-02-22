"""Roadmap management commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap
from yeehaw.store.store import Store


def handle_roadmap(args: Any, db_path: Path) -> None:
    """Handle `yeehaw roadmap` subcommands."""
    store = Store(db_path)
    try:
        if args.roadmap_command == "create":
            _create_roadmap(store, args)
        elif args.roadmap_command == "show":
            _show_roadmap(store, args)
        elif args.roadmap_command == "approve":
            _approve_roadmap(store, args)
        elif args.roadmap_command == "clear":
            _clear_roadmap(store, args)
    finally:
        store.close()


def _create_roadmap(store: Store, args: Any) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    markdown_path = Path(args.file)
    if not markdown_path.exists():
        print(f"Error: File '{args.file}' not found.")
        return

    raw_md = markdown_path.read_text()
    try:
        roadmap = parse_roadmap(raw_md)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    errors = validate_roadmap(roadmap)
    if errors:
        print("Validation errors:")
        for error in errors:
            print(f"  - {error}")
        return

    roadmap_id = store.create_roadmap(project["id"], raw_md)
    for phase in roadmap.phases:
        phase_id = store.create_phase(roadmap_id, phase.number, phase.title, phase.verify_cmd)
        for task in phase.tasks:
            store.create_task(roadmap_id, phase_id, task.number, task.title, task.description)

    total_tasks = sum(len(phase.tasks) for phase in roadmap.phases)
    print(
        f"Roadmap created (id={roadmap_id}): "
        f"{len(roadmap.phases)} phases, {total_tasks} tasks"
    )


def _show_roadmap(store: Store, args: Any) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        print("No active roadmap.")
        return

    print(f"Roadmap #{roadmap['id']} [{roadmap['status']}]")
    print()
    phases = store.list_phases(roadmap["id"])
    for phase in phases:
        print(f"  Phase {phase['phase_number']}: {phase['title']} [{phase['status']}]")
        if phase.get("verify_cmd"):
            print(f"    Verify: {phase['verify_cmd']}")
        tasks = store.list_tasks_by_phase(phase["id"])
        for task in tasks:
            icon = {
                "pending": " ",
                "queued": "~",
                "in-progress": ">",
                "done": "+",
                "failed": "!",
                "blocked": "#",
            }.get(task["status"], "?")
            agent = f" ({task['assigned_agent']})" if task.get("assigned_agent") else ""
            print(f"    [{icon}] Task {task['task_number']}: {task['title']}{agent}")
    print()


def _approve_roadmap(store: Store, args: Any) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        print("No active roadmap.")
        return
    if roadmap["status"] != "draft":
        print(f"Roadmap is '{roadmap['status']}', not 'draft'.")
        return

    store.update_roadmap_status(roadmap["id"], "approved")

    phases = store.list_phases(roadmap["id"])
    queued = 0
    if phases:
        phase_1 = phases[0]
        tasks = store.list_tasks_by_phase(phase_1["id"])
        for task in tasks:
            store.queue_task(task["id"])
            queued += 1
        store.update_phase_status(phase_1["id"], "executing")

    store.update_roadmap_status(roadmap["id"], "executing")
    print(f"Roadmap approved. {queued} tasks queued for Phase 1.")


def _clear_roadmap(store: Store, args: Any) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    first = store.get_active_roadmap(project["id"])
    if not first:
        print("No active roadmap.")
        return

    removed_roadmaps = 0
    phases_total = 0
    tasks_total = 0
    last_cleared_id = first["id"]

    while True:
        roadmap = store.get_active_roadmap(project["id"])
        if not roadmap:
            break
        phases = store.list_phases(roadmap["id"])
        phases_total += len(phases)
        tasks_total += sum(len(store.list_tasks_by_phase(phase["id"])) for phase in phases)
        if not store.delete_roadmap(roadmap["id"]):
            break
        removed_roadmaps += 1
        last_cleared_id = roadmap["id"]

    if removed_roadmaps == 0:
        print("No roadmap cleared.")
        return

    print(
        f"Cleared {removed_roadmaps} roadmap(s) for project '{args.project}' "
        f"(latest #{last_cleared_id}, {phases_total} phases, {tasks_total} tasks removed)."
    )
