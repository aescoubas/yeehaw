"""Roadmap management commands."""

from __future__ import annotations

import argparse

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from yeehaw.config.loader import load_feature_flags
from yeehaw.planner.generate import generate_roadmap_from_prompt
from yeehaw.roadmap.parser import parse_roadmap, validate_roadmap
from yeehaw.scm import (
    GitHubSCMAdapter,
    LocalGitSCMAdapter,
    RoadmapPhaseSummary,
    RoadmapPRPublishRequest,
    RoadmapPRPublishResult,
    RoadmapPublishResult,
    RoadmapTaskSummary,
    SCMAdapterError,
)
from yeehaw.store.store import Store

DEFAULT_BASE_BRANCH = "main"
GITHUB_OWNER_ENV = "YEEHAW_GITHUB_OWNER"
GITHUB_REPO_ENV = "YEEHAW_GITHUB_REPO"
GITHUB_TOKEN_ENV = "YEEHAW_GITHUB_TOKEN"
GITHUB_API_BASE_URL_ENV = "YEEHAW_GITHUB_API_BASE_URL"


@dataclass(frozen=True)
class RoadmapPublishOutcome:
    """Combined local publish summary and optional PR publication output."""

    publish_result: RoadmapPublishResult
    pr_result: RoadmapPRPublishResult | None = None


def handle_roadmap(args: argparse.Namespace, db_path: Path) -> None:
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
        elif args.roadmap_command == "publish":
            _handle_publish_roadmap(store, args, db_path.parent)
        elif args.roadmap_command == "generate":
            _generate_roadmap(store, args, db_path)
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

    try:
        store.apply_roadmap_dependencies(roadmap_id, roadmap)
        store.apply_roadmap_file_targets(roadmap_id, roadmap)
    except ValueError as exc:
        store.delete_roadmap(roadmap_id)
        print(f"Error: {exc}")
        return

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
                "paused": "=",
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


def _handle_publish_roadmap(store: Store, args: Any, runtime_root: Path) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    roadmap = store.get_active_roadmap(project["id"])
    if not roadmap:
        print("No active roadmap.")
        return

    try:
        publish_outcome = _publish_active_roadmap(
            store=store,
            project=project,
            roadmap=roadmap,
            runtime_root=runtime_root,
        )
    except (SCMAdapterError, ValueError) as exc:
        print(f"Error: {exc}")
        return

    _print_publish_outcome(publish_outcome)


def _publish_active_roadmap(
    *,
    store: Store,
    project: dict[str, Any],
    roadmap: dict[str, Any],
    runtime_root: Path,
) -> RoadmapPublishOutcome:
    integration_branch = roadmap.get("integration_branch")
    if not isinstance(integration_branch, str) or not integration_branch:
        raise ValueError(
            "Active roadmap has no integration branch yet. "
            "Run and complete at least one task before publishing."
        )

    roadmap_id = int(roadmap["id"])
    project_repo_root = Path(str(project["repo_root"]))
    local_publish_result = LocalGitSCMAdapter().publish_roadmap_integration(
        repo_root=project_repo_root,
        roadmap_id=roadmap_id,
        integration_branch=integration_branch,
        base_branch=DEFAULT_BASE_BRANCH,
    )

    pr_enabled = _load_pr_automation_flag(runtime_root)
    pr_result = _publish_github_pull_request(
        roadmap_id=roadmap_id,
        integration_branch=integration_branch,
        project_repo_root=project_repo_root,
        publish_result=local_publish_result,
        phase_summaries=_build_phase_summaries(store, roadmap_id),
        pr_enabled=pr_enabled,
    )
    return RoadmapPublishOutcome(
        publish_result=local_publish_result,
        pr_result=pr_result,
    )


def _load_pr_automation_flag(runtime_root: Path) -> bool:
    config_path = runtime_root / "config" / "runtime.json"
    try:
        feature_flags = load_feature_flags(config_path)
    except ValueError as exc:
        print(f"Warning: {exc}. GitHub PR publish disabled for this run.")
        return False
    return feature_flags.pr_automation


def _build_phase_summaries(store: Store, roadmap_id: int) -> tuple[RoadmapPhaseSummary, ...]:
    phase_summaries: list[RoadmapPhaseSummary] = []
    for phase in store.list_phases(roadmap_id):
        task_summaries = tuple(
            RoadmapTaskSummary(
                task_number=str(task["task_number"]),
                title=str(task["title"]),
                status=str(task["status"]),
                summary=str(task["last_failure"]) if task.get("last_failure") else None,
            )
            for task in store.list_tasks_by_phase(int(phase["id"]))
        )
        phase_summaries.append(
            RoadmapPhaseSummary(
                phase_number=int(phase["phase_number"]),
                title=str(phase["title"]),
                status=str(phase["status"]),
                tasks=task_summaries,
            )
        )
    return tuple(phase_summaries)


def _publish_github_pull_request(
    *,
    roadmap_id: int,
    integration_branch: str,
    project_repo_root: Path,
    publish_result: RoadmapPublishResult,
    phase_summaries: tuple[RoadmapPhaseSummary, ...],
    pr_enabled: bool,
) -> RoadmapPRPublishResult | None:
    github_adapter = _github_adapter_from_env()
    if github_adapter is None:
        return None
    publish_request = RoadmapPRPublishRequest(
        repo_root=project_repo_root,
        roadmap_id=roadmap_id,
        integration_branch=integration_branch,
        base_branch=DEFAULT_BASE_BRANCH,
        enabled=pr_enabled,
        summary=publish_result.summary,
        phase_summaries=phase_summaries,
    )
    return github_adapter.publish_roadmap_pull_request(publish_request)


def _github_adapter_from_env() -> GitHubSCMAdapter | None:
    owner = os.environ.get(GITHUB_OWNER_ENV, "").strip()
    repo = os.environ.get(GITHUB_REPO_ENV, "").strip()
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    api_base_url = os.environ.get(GITHUB_API_BASE_URL_ENV, "").strip()

    if not owner and not repo and not token:
        return None
    if not owner or not repo or not token:
        raise ValueError(
            "Incomplete GitHub adapter configuration. "
            f"Set {GITHUB_OWNER_ENV}, {GITHUB_REPO_ENV}, and {GITHUB_TOKEN_ENV}."
        )

    return GitHubSCMAdapter(
        owner=owner,
        repo=repo,
        token=token,
        enabled=True,
        api_base_url=api_base_url or "https://api.github.com",
    )


def _print_publish_outcome(outcome: RoadmapPublishOutcome) -> None:
    summary = outcome.publish_result.summary
    branch = outcome.publish_result.branch
    print(
        f"Roadmap published (id={summary.roadmap_id}): "
        f"{summary.integration_branch} -> {summary.base_branch}"
    )
    print(f"  Provider: {branch.provider}")
    print(f"  Branch: {branch.branch_name}")
    print(f"  Head SHA: {summary.head_sha}")
    print(f"  Commits ahead: {summary.commits_ahead}")

    pr_result = outcome.pr_result
    if pr_result is None:
        print("  Pull request: not configured")
        return

    publication = pr_result.pull_request
    if publication is not None:
        print(f"  Pull request: #{publication.number} ({pr_result.action})")
        print(f"  URL: {publication.html_url}")
        return

    if pr_result.error:
        print(f"  Pull request: {pr_result.action} ({pr_result.error})")
        return

    event_message = pr_result.events[0].message if pr_result.events else pr_result.action
    print(f"  Pull request: {pr_result.action} ({event_message})")


def _generate_roadmap(store: Store, args: Any, db_path: Path) -> None:
    project = store.get_project(args.project)
    if not project:
        print(f"Error: Project '{args.project}' not found.")
        return

    prompt_text = args.prompt
    if args.file:
        prompt_file = Path(args.file)
        if not prompt_file.exists():
            print(f"Error: File '{args.file}' not found.")
            return
        prompt_text = prompt_file.read_text()

    assert prompt_text is not None
    result = generate_roadmap_from_prompt(db_path, args.project, prompt_text, args.agent)

    if not result.success:
        print(f"Roadmap generation failed: {result.message}")
        if result.stderr:
            tail = result.stderr.strip().splitlines()[-10:]
            if tail:
                print("Agent stderr (tail):")
                for line in tail:
                    print(f"  {line}")
        return

    assert result.roadmap_id is not None
    print(
        f"Roadmap generated (id={result.roadmap_id}): "
        f"{result.phases} phases, {result.tasks} tasks (agent={args.agent})"
    )

    if args.approve:
        _approve_roadmap(store, args)
