from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ControlPlaneConfig
from .control_plane import ControlPlane
from .db import connect as connect_db
from .editor_bridge import EditorError
from .models import RuntimeKind
from .roadmap_workflow import edit_roadmap_for_project, validate_roadmap
from .store import (
    add_dispatcher_decision,
    add_usage_record,
    apply_dispatcher_decision,
    create_project,
    list_projects,
    queue_demo_task_for_project,
    usage_summary,
)
from .tui import run_tui


def cmd_init_db(args: argparse.Namespace) -> int:
    connect_db(args.db).close()
    print(f"initialized v2 db: {Path(args.db).expanduser().resolve()}")
    return 0


def cmd_project_add(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    guidelines = ""
    if args.guidelines_file:
        guidelines = Path(args.guidelines_file).read_text(encoding="utf-8")
    project_id = create_project(conn, name=args.name, root_path=args.root, guidelines=guidelines)
    print(f"project upserted: id={project_id} name={args.name}")
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    rows = list_projects(conn)
    if not rows:
        print("no projects")
        return 0
    for row in rows:
        print(f"#{row['id']} {row['name']} root={row['root_path']}")
    return 0


def cmd_roadmap_edit(args: argparse.Namespace) -> int:
    try:
        roadmap_id, revision_id = edit_roadmap_for_project(
            db_path=args.db,
            project_name=args.project,
            roadmap_path=args.path,
            roadmap_name=args.name,
            editor=args.editor,
        )
    except (ValueError, EditorError) as exc:
        print(f"roadmap edit failed: {exc}")
        return 2
    print(f"roadmap edited: roadmap_id={roadmap_id} revision_id={revision_id}")
    return 0


def cmd_roadmap_validate(args: argparse.Namespace) -> int:
    ok, msg = validate_roadmap(args.path, default_agent=args.default_agent)
    if not ok:
        print(f"invalid roadmap: {msg}")
        return 2
    print(msg)
    return 0


def cmd_task_queue(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    runtime_kind = RuntimeKind(args.runtime)
    task_id = queue_demo_task_for_project(
        conn,
        project_name=args.project,
        title=args.title,
        description=args.description,
        runtime_kind=runtime_kind,
        preferred_agent=args.agent,
    )
    print(f"queued task: id={task_id}")
    return 0


def cmd_scheduler_tick(args: argparse.Namespace) -> int:
    cp = ControlPlane(ControlPlaneConfig(db_path=Path(args.db), poll_seconds=args.poll_seconds))
    stats = cp.tick()
    print(
        "tick:"
        f" dispatched={stats.dispatched}"
        f" completed={stats.completed}"
        f" failed={stats.failed}"
        f" stuck={stats.stuck}"
    )
    return 0


def cmd_scheduler_start(args: argparse.Namespace) -> int:
    cp = ControlPlane(ControlPlaneConfig(db_path=Path(args.db), poll_seconds=args.poll_seconds))
    cp.run_forever()
    return 0


def cmd_dispatcher_propose(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    try:
        proposal = json.loads(args.proposal_json)
    except json.JSONDecodeError as exc:
        print(f"invalid proposal json: {exc}")
        return 2
    decision_id = add_dispatcher_decision(
        conn,
        proposal=proposal,
        task_id=args.task_id,
        rationale=args.rationale or "",
        confidence=args.confidence,
    )
    print(f"dispatcher decision recorded: id={decision_id}")
    return 0


def cmd_dispatcher_apply(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    try:
        apply_dispatcher_decision(conn, args.decision_id)
    except ValueError as exc:
        print(str(exc))
        return 2
    print(f"dispatcher decision applied: id={args.decision_id}")
    return 0


def cmd_usage_record(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    record_id = add_usage_record(
        conn,
        provider=args.provider,
        model=args.model,
        input_tokens=args.input_tokens,
        output_tokens=args.output_tokens,
        cost_usd=args.cost_usd,
        source=args.source,
        session_id=args.session_id,
        task_id=args.task_id,
    )
    print(f"usage record inserted: id={record_id}")
    return 0


def cmd_usage_summary(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    rows = usage_summary(conn, project_id=args.project_id)
    if not rows:
        print("no usage records")
        return 0
    for row in rows:
        print(
            f"provider={row['provider']} model={row['model']} "
            f"in={row['input_tokens']} out={row['output_tokens']} "
            f"cost_usd={float(row['cost_usd']):.6f} records={row['records']}"
        )
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    run_tui(db_path=args.db, refresh_seconds=args.refresh_seconds, poll_seconds=args.poll_seconds)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yeehaw-v2", description="yeehaw v2 control-plane scaffold")
    parser.add_argument("--db", default=".yeehaw/yeehaw_v2.db", help="v2 sqlite path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-db", help="initialize v2 database")
    p_init.set_defaults(func=cmd_init_db)

    p_project = sub.add_parser("project", help="project operations")
    sub_project = p_project.add_subparsers(dest="project_cmd", required=True)
    p_add = sub_project.add_parser("add", help="add or update project")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--root", required=True)
    p_add.add_argument("--guidelines-file")
    p_add.set_defaults(func=cmd_project_add)
    p_list = sub_project.add_parser("list", help="list projects")
    p_list.set_defaults(func=cmd_project_list)

    p_roadmap = sub.add_parser("roadmap", help="roadmap workflows")
    sub_roadmap = p_roadmap.add_subparsers(dest="roadmap_cmd", required=True)
    p_edit = sub_roadmap.add_parser("edit", help="edit roadmap via $EDITOR")
    p_edit.add_argument("--project", required=True)
    p_edit.add_argument("--path", default="roadmap.md")
    p_edit.add_argument("--name", default="roadmap")
    p_edit.add_argument("--editor")
    p_edit.set_defaults(func=cmd_roadmap_edit)
    p_validate = sub_roadmap.add_parser("validate", help="validate roadmap file")
    p_validate.add_argument("--path", required=True)
    p_validate.add_argument("--default-agent", default="codex")
    p_validate.set_defaults(func=cmd_roadmap_validate)

    p_task = sub.add_parser("task", help="task utilities")
    sub_task = p_task.add_subparsers(dest="task_cmd", required=True)
    p_queue = sub_task.add_parser("queue", help="queue ad-hoc task for project")
    p_queue.add_argument("--project", required=True)
    p_queue.add_argument("--title", required=True)
    p_queue.add_argument("--description", default="")
    p_queue.add_argument("--runtime", choices=[RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value], default="tmux")
    p_queue.add_argument("--agent", help="preferred agent command")
    p_queue.set_defaults(func=cmd_task_queue)

    p_scheduler = sub.add_parser("scheduler", help="run scheduler")
    sub_scheduler = p_scheduler.add_subparsers(dest="scheduler_cmd", required=True)
    p_tick = sub_scheduler.add_parser("tick", help="single scheduler tick")
    p_tick.add_argument("--poll-seconds", type=float, default=1.0)
    p_tick.set_defaults(func=cmd_scheduler_tick)
    p_start = sub_scheduler.add_parser("start", help="run scheduler loop")
    p_start.add_argument("--poll-seconds", type=float, default=1.0)
    p_start.set_defaults(func=cmd_scheduler_start)

    p_dispatcher = sub.add_parser("dispatcher", help="dispatcher decision operations")
    sub_dispatcher = p_dispatcher.add_subparsers(dest="dispatcher_cmd", required=True)
    p_propose = sub_dispatcher.add_parser("propose", help="store dispatcher proposal")
    p_propose.add_argument("--task-id", type=int, required=True)
    p_propose.add_argument("--proposal-json", required=True)
    p_propose.add_argument("--rationale")
    p_propose.add_argument("--confidence", type=float)
    p_propose.set_defaults(func=cmd_dispatcher_propose)
    p_apply = sub_dispatcher.add_parser("apply", help="apply dispatcher proposal to task")
    p_apply.add_argument("--decision-id", type=int, required=True)
    p_apply.set_defaults(func=cmd_dispatcher_apply)

    p_usage = sub.add_parser("usage", help="usage accounting")
    sub_usage = p_usage.add_subparsers(dest="usage_cmd", required=True)
    p_usage_record = sub_usage.add_parser("record", help="record usage row")
    p_usage_record.add_argument("--provider", required=True)
    p_usage_record.add_argument("--model", required=True)
    p_usage_record.add_argument("--input-tokens", type=int, required=True)
    p_usage_record.add_argument("--output-tokens", type=int, required=True)
    p_usage_record.add_argument("--cost-usd", type=float, required=True)
    p_usage_record.add_argument("--source", default="adapter")
    p_usage_record.add_argument("--session-id", type=int)
    p_usage_record.add_argument("--task-id", type=int)
    p_usage_record.set_defaults(func=cmd_usage_record)
    p_usage_summary = sub_usage.add_parser("summary", help="show usage summary")
    p_usage_summary.add_argument("--project-id", type=int)
    p_usage_summary.set_defaults(func=cmd_usage_summary)

    p_tui = sub.add_parser("tui", help="open v2 ops/workspace TUI")
    p_tui.add_argument("--refresh-seconds", type=float, default=1.0)
    p_tui.add_argument("--poll-seconds", type=float, default=1.0)
    p_tui.set_defaults(func=cmd_tui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
