from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .coach import start_project_coach, start_roadmap_coach
from .git_repo import GitRepoError, GitRepoInfo, detect_repo
from .importer import import_projects
from .orchestrator import (
    GlobalScheduler,
    create_batch_from_task_list,
    replan_batch_from_roadmap,
)
from .roadmap import RoadmapValidationError, load_roadmap
from .runner import run_roadmap
from .tui import run_tui


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def cmd_init_db(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    conn.close()
    print(f"DB initialized at {db.default_db_path() if not args.db else Path(args.db).resolve()}")
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    root_input = Path(args.root).resolve()
    guidelines = _read_text(args.guidelines_file)

    repo_info = None
    if args.allow_non_git:
        try:
            repo_info = detect_repo(root_input)
        except GitRepoError:
            repo_info = None
    else:
        try:
            repo_info = detect_repo(root_input)
        except GitRepoError as exc:
            print(f"Project root must be a git repository: {exc}", file=sys.stderr)
            return 2

    project_root = repo_info.root_path if repo_info else str(root_input)
    project_name = args.name or Path(project_root).name
    project_id = db.create_project(
        conn,
        project_name,
        project_root,
        guidelines,
        git_remote_url=repo_info.remote_url if repo_info else None,
        default_branch=repo_info.default_branch if repo_info else None,
        head_sha=repo_info.head_sha if repo_info else None,
    )
    print(f"Project upserted: id={project_id} name={project_name}")
    print(f"  root={project_root}")
    if repo_info:
        if repo_info.remote_url:
            print(f"  remote={repo_info.remote_url}")
        if repo_info.default_branch:
            print(f"  default_branch={repo_info.default_branch}")
        if repo_info.head_sha:
            print(f"  head_sha={repo_info.head_sha}")
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    projects = db.list_projects(conn)
    if not projects:
        print("No projects found")
        return 0

    for row in projects:
        remote = row["git_remote_url"] or "-"
        branch = row["default_branch"] or "-"
        print(f"{row['id']:>3}  {row['name']:<20}  root={row['root_path']}  branch={branch}  remote={remote}")
    return 0


def cmd_project_coach(args: argparse.Namespace) -> int:
    root_input = Path(args.root).resolve()
    try:
        repo = detect_repo(root_input)
    except GitRepoError as exc:
        if not args.allow_non_git:
            print(f"Project root must be a git repository: {exc}", file=sys.stderr)
            return 2
        repo = GitRepoInfo(
            root_path=str(root_input),
            remote_url=None,
            default_branch=None,
            head_sha=None,
        )

    session = start_project_coach(
        repo=repo,
        agent=args.agent,
        guidelines_output=args.guidelines_output,
        name_hint=args.name_hint,
        session_prefix=args.session_prefix,
        attach=not args.no_attach,
        command_override=args.command,
        allow_non_git=args.allow_non_git,
    )
    print(f"Project coach session: {session}")
    if args.no_attach:
        print(f"Attach with: tmux attach -t {session}")
    return 0


def cmd_project_import(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    roots = [Path(root).resolve() for root in args.roots]
    guidelines = _read_text(args.guidelines_file)

    result = import_projects(
        conn,
        roots=roots,
        max_depth=args.max_depth,
        default_guidelines=guidelines,
        dry_run=args.dry_run,
    )
    for line in result.details:
        print(line)

    print(
        f"Summary: created={result.created} updated={result.updated} "
        f"skipped={result.skipped} failed={result.failed} dry_run={args.dry_run}"
    )
    return 0 if result.failed == 0 else 1


def cmd_roadmap_validate(args: argparse.Namespace) -> int:
    try:
        roadmap = load_roadmap(args.path, default_agent=args.default_agent)
    except RoadmapValidationError as exc:
        print(f"Invalid roadmap: {exc}", file=sys.stderr)
        return 2

    print(f"Valid roadmap: {roadmap.name} (v{roadmap.version})")
    for track in roadmap.tracks:
        print(f"- track={track.id} agent={track.agent} stages={len(track.stages)}")
    return 0


def cmd_roadmap_template(args: argparse.Namespace) -> int:
    ext = "md" if args.format == "markdown" else "yaml"
    template = Path(__file__).resolve().parent.parent / "templates" / f"roadmap.example.{ext}"
    output = Path(args.output)
    output.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Template written to {output}")
    return 0


def cmd_roadmap_coach(args: argparse.Namespace) -> int:
    session = start_roadmap_coach(
        project_name=args.project,
        output_path=args.output,
        agent=args.agent,
        db_path=args.db,
        session_prefix=args.session_prefix,
        attach=not args.no_attach,
        command_override=args.command,
    )
    print(f"Roadmap coach session: {session}")
    if args.no_attach:
        print(f"Attach with: tmux attach -t {session}")
    return 0


def cmd_run_start(args: argparse.Namespace) -> int:
    run_id = run_roadmap(
        project_name=args.project,
        roadmap_path=args.roadmap,
        db_path=args.db,
        default_agent=args.default_agent,
        poll_seconds=args.poll_seconds,
        session_prefix=args.session_prefix,
    )
    print(f"Run finished/paused: id={run_id}")
    return 0


def cmd_run_status(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    if args.run_id is None:
        runs = db.latest_runs(conn, limit=20)
        if not runs:
            print("No runs found")
            return 0
        for row in runs:
            print(
                f"#{row['id']} [{row['status']}] project={row['project_name']} roadmap={row['roadmap_name']} "
                f"session={row['tmux_session']}"
            )
        return 0

    run_row = db.get_run(conn, args.run_id)
    if run_row is None:
        print(f"Run #{args.run_id} not found", file=sys.stderr)
        return 2

    print(
        f"Run #{run_row['id']} [{run_row['status']}] project={run_row['project_name']} "
        f"session={run_row['tmux_session']}"
    )
    tracks = db.run_tracks(conn, args.run_id)
    for tr in tracks:
        print(
            f"- {tr['track_id']} [{tr['status']}] stage_index={tr['current_stage_index']} "
            f"agent={tr['agent']} window={tr['window_name']}"
        )
        if tr["waiting_question"]:
            print(f"  question: {tr['waiting_question']}")

    print("Events:")
    for event in db.run_events(conn, args.run_id, limit=20):
        print(f"  {event['created_at']} [{event['level']}] {event['message']}")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    run_tui(db_path=args.db, refresh_seconds=args.refresh_seconds)
    return 0


def cmd_batch_create(args: argparse.Namespace) -> int:
    if args.tasks_file:
        task_text = Path(args.tasks_file).read_text(encoding="utf-8")
    else:
        task_text = args.tasks
    if not task_text or not task_text.strip():
        print("Task list cannot be empty", file=sys.stderr)
        return 2

    batch_id = create_batch_from_task_list(
        project_name=args.project,
        batch_name=args.name,
        task_list_text=task_text,
        planner_agent=args.planner_agent,
        db_path=args.db,
        timeout_minutes=args.timeout_minutes,
    )
    print(f"Batch created and queued: id={batch_id}")
    return 0


def cmd_batch_replan(args: argparse.Namespace) -> int:
    replan_batch_from_roadmap(
        batch_id=args.batch_id,
        roadmap_path=args.roadmap,
        db_path=args.db,
    )
    print(f"Batch replanned: id={args.batch_id}")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    status = args.status if args.status else None
    project_id = None
    if args.project:
        project = db.get_project(conn, args.project)
        if project is None:
            print(f"Project '{args.project}' not found", file=sys.stderr)
            return 2
        project_id = int(project["id"])

    tasks = db.list_tasks(conn, status=status, project_id=project_id, limit=args.limit)
    if not tasks:
        print("No tasks found")
        return 0
    for row in tasks:
        print(
            f"#{row['id']} [{row['status']}] p={row['project_name']} "
            f"prio={row['priority']} agent={row['assigned_agent'] or row['preferred_agent'] or '-'} "
            f"title={row['title']}"
        )
        if row["blocked_question"]:
            print(f"  question: {row['blocked_question']}")
    return 0


def cmd_task_reply(args: argparse.Namespace) -> int:
    scheduler = GlobalScheduler(db_path=args.db)
    scheduler.reply_to_task(args.task_id, args.answer)
    print(f"Reply sent to task #{args.task_id}")
    return 0


def cmd_task_pause(args: argparse.Namespace) -> int:
    scheduler = GlobalScheduler(db_path=args.db)
    scheduler.pause_task(args.task_id)
    print(f"Task paused/requeued: #{args.task_id}")
    return 0


def cmd_scheduler_start(args: argparse.Namespace) -> int:
    scheduler = GlobalScheduler(db_path=args.db, poll_seconds=args.poll_seconds, max_attempts=args.max_attempts)
    scheduler.run_forever()
    return 0


def cmd_scheduler_tick(args: argparse.Namespace) -> int:
    scheduler = GlobalScheduler(db_path=args.db, poll_seconds=args.poll_seconds, max_attempts=args.max_attempts)
    stats = scheduler.tick()
    print(
        "tick:"
        f" dispatched={stats.dispatched}"
        f" completed={stats.completed}"
        f" awaiting_input={stats.awaiting_input}"
        f" reassigned={stats.reassigned}"
        f" failed={stats.failed}"
    )
    return 0


def cmd_scheduler_config(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    if args.set:
        db.update_scheduler_config(
            conn,
            max_global_sessions=args.max_global,
            max_project_sessions=args.max_project,
            default_stuck_minutes=args.stuck_minutes,
            auto_reassign=args.auto_reassign,
            preemption_enabled=args.preemption_enabled,
        )

    cfg = db.scheduler_config(conn)
    print(
        "scheduler-config "
        f"max_global={cfg['max_global_sessions']} "
        f"max_project={cfg['max_project_sessions']} "
        f"stuck_minutes={cfg['default_stuck_minutes']} "
        f"auto_reassign={cfg['auto_reassign']} "
        f"preemption_enabled={cfg['preemption_enabled']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yeehaw", description="tmux-based CLI agent harness")
    parser.add_argument("--db", help="sqlite db path (default: ./.yeehaw/yeehaw.db)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="initialize sqlite schema")
    p_init.set_defaults(func=cmd_init_db)

    p_project = sub.add_parser("project", help="project operations")
    sub_project = p_project.add_subparsers(dest="project_command", required=True)

    p_project_create = sub_project.add_parser("create", help="create or update a project")
    p_project_create.add_argument("--name", help="project name (default: repository folder name)")
    p_project_create.add_argument("--root", default=".", help="repository path (default: current directory)")
    p_project_create.add_argument("--guidelines-file")
    p_project_create.add_argument("--allow-non-git", action="store_true", help="allow non-git paths")
    p_project_create.set_defaults(func=cmd_project_create)

    p_project_list = sub_project.add_parser("list", help="list projects")
    p_project_list.set_defaults(func=cmd_project_list)

    p_project_coach = sub_project.add_parser("coach", help="talk to an agent to define a project")
    p_project_coach.add_argument("--root", default=".", help="repository path (default: current directory)")
    p_project_coach.add_argument("--name-hint", help="optional project name hint")
    p_project_coach.add_argument("--guidelines-output", default=".yeehaw/project-guidelines.md")
    p_project_coach.add_argument("--agent", default="codex")
    p_project_coach.add_argument("--command", help="override agent launch command")
    p_project_coach.add_argument("--session-prefix", default="yeehaw-project-coach")
    p_project_coach.add_argument("--allow-non-git", action="store_true")
    p_project_coach.add_argument("--no-attach", action="store_true")
    p_project_coach.set_defaults(func=cmd_project_coach)

    p_project_import = sub_project.add_parser("import", help="bulk import projects from git roots")
    p_project_import.add_argument(
        "--roots",
        nargs="+",
        required=True,
        help="one or more filesystem roots to scan for git repositories",
    )
    p_project_import.add_argument("--max-depth", type=int, default=5)
    p_project_import.add_argument("--guidelines-file", help="default guidelines file for newly created projects")
    p_project_import.add_argument("--dry-run", action="store_true")
    p_project_import.set_defaults(func=cmd_project_import)

    p_roadmap = sub.add_parser("roadmap", help="roadmap operations")
    sub_roadmap = p_roadmap.add_subparsers(dest="roadmap_command", required=True)

    p_roadmap_validate = sub_roadmap.add_parser("validate", help="validate roadmap yaml/markdown")
    p_roadmap_validate.add_argument("path")
    p_roadmap_validate.add_argument("--default-agent", default="codex")
    p_roadmap_validate.set_defaults(func=cmd_roadmap_validate)

    p_roadmap_template = sub_roadmap.add_parser("template", help="write roadmap template")
    p_roadmap_template.add_argument("--output", required=True)
    p_roadmap_template.add_argument("--format", choices=["yaml", "markdown"], default="yaml")
    p_roadmap_template.set_defaults(func=cmd_roadmap_template)

    p_roadmap_coach = sub_roadmap.add_parser("coach", help="talk to an agent to author a roadmap")
    p_roadmap_coach.add_argument("--project", required=True)
    p_roadmap_coach.add_argument("--output", required=True)
    p_roadmap_coach.add_argument("--agent", default="codex")
    p_roadmap_coach.add_argument("--command", help="override agent launch command")
    p_roadmap_coach.add_argument("--session-prefix", default="yeehaw-coach")
    p_roadmap_coach.add_argument("--no-attach", action="store_true")
    p_roadmap_coach.set_defaults(func=cmd_roadmap_coach)

    p_run = sub.add_parser("run", help="run operations")
    sub_run = p_run.add_subparsers(dest="run_command", required=True)

    p_run_start = sub_run.add_parser("start", help="start a roadmap run")
    p_run_start.add_argument("--project", required=True)
    p_run_start.add_argument("--roadmap", required=True)
    p_run_start.add_argument("--default-agent", default="codex")
    p_run_start.add_argument("--session-prefix", default="yeehaw")
    p_run_start.add_argument("--poll-seconds", type=float, default=2.0)
    p_run_start.set_defaults(func=cmd_run_start)

    p_run_status = sub_run.add_parser("status", help="show run status")
    p_run_status.add_argument("--run-id", type=int)
    p_run_status.set_defaults(func=cmd_run_status)

    p_tui = sub.add_parser("tui", help="open monitoring TUI")
    p_tui.add_argument("--refresh-seconds", type=float, default=1.0)
    p_tui.set_defaults(func=cmd_tui)

    p_batch = sub.add_parser("batch", help="task batch operations")
    sub_batch = p_batch.add_subparsers(dest="batch_command", required=True)

    p_batch_create = sub_batch.add_parser("create", help="create a batch from free-form task list using planner agent")
    p_batch_create.add_argument("--project", required=True)
    p_batch_create.add_argument("--name", required=True)
    p_batch_create.add_argument("--tasks", help="free-form task list text")
    p_batch_create.add_argument("--tasks-file", help="path to free-form task list text file")
    p_batch_create.add_argument("--planner-agent", default="codex")
    p_batch_create.add_argument("--timeout-minutes", type=int, default=20)
    p_batch_create.set_defaults(func=cmd_batch_create)

    p_batch_replan = sub_batch.add_parser("replan", help="apply edited roadmap to an existing batch")
    p_batch_replan.add_argument("--batch-id", required=True, type=int)
    p_batch_replan.add_argument("--roadmap", required=True)
    p_batch_replan.set_defaults(func=cmd_batch_replan)

    p_task = sub.add_parser("task", help="task operations")
    sub_task = p_task.add_subparsers(dest="task_command", required=True)

    p_task_list = sub_task.add_parser("list", help="list tasks")
    p_task_list.add_argument("--project")
    p_task_list.add_argument("--status")
    p_task_list.add_argument("--limit", type=int, default=200)
    p_task_list.set_defaults(func=cmd_task_list)

    p_task_reply = sub_task.add_parser("reply", help="answer a blocked task and resume it")
    p_task_reply.add_argument("--task-id", required=True, type=int)
    p_task_reply.add_argument("--answer", required=True)
    p_task_reply.set_defaults(func=cmd_task_reply)

    p_task_pause = sub_task.add_parser("pause", help="preempt and requeue a task")
    p_task_pause.add_argument("--task-id", required=True, type=int)
    p_task_pause.set_defaults(func=cmd_task_pause)

    p_scheduler = sub.add_parser("scheduler", help="global scheduler operations")
    sub_scheduler = p_scheduler.add_subparsers(dest="scheduler_command", required=True)

    p_scheduler_start = sub_scheduler.add_parser("start", help="run the global scheduler loop")
    p_scheduler_start.add_argument("--poll-seconds", type=float, default=2.0)
    p_scheduler_start.add_argument("--max-attempts", type=int, default=4)
    p_scheduler_start.set_defaults(func=cmd_scheduler_start)

    p_scheduler_tick = sub_scheduler.add_parser("tick", help="run one scheduler tick")
    p_scheduler_tick.add_argument("--poll-seconds", type=float, default=2.0)
    p_scheduler_tick.add_argument("--max-attempts", type=int, default=4)
    p_scheduler_tick.set_defaults(func=cmd_scheduler_tick)

    p_scheduler_config = sub_scheduler.add_parser("config", help="show or update scheduler config")
    p_scheduler_config.add_argument("--set", action="store_true")
    p_scheduler_config.add_argument("--max-global", type=int)
    p_scheduler_config.add_argument("--max-project", type=int)
    p_scheduler_config.add_argument("--stuck-minutes", type=int)
    p_scheduler_config.add_argument("--auto-reassign", action=argparse.BooleanOptionalAction, default=None)
    p_scheduler_config.add_argument("--preemption-enabled", action=argparse.BooleanOptionalAction, default=None)
    p_scheduler_config.set_defaults(func=cmd_scheduler_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
