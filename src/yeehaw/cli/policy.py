"""Policy inspection commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yeehaw.policy.checks import (
    BuiltInPolicyInput,
    BuiltInPolicyResult,
    PolicyCheckStage,
    collect_builtin_policy_input,
    evaluate_builtin_policy_checks,
)
from yeehaw.policy.loader import load_policy_pack
from yeehaw.policy.models import PolicyPack
from yeehaw.store.store import Store

DEFAULT_TARGET_BRANCH = "main"
POLICY_EVENT_KIND = "task_policy_violation"
POLICY_EVENT_SCAN_LIMIT = 500
STAGE_ORDER: tuple[PolicyCheckStage, ...] = ("done_accept", "pre_merge")


@dataclass(frozen=True)
class BuiltInCheckSpec:
    """Display metadata for one built-in policy check."""

    code: str
    details: str
    failure_codes: tuple[str, ...]


def handle_policy(args: Any, db_path: Path) -> None:
    """Handle `yeehaw policy` subcommands."""
    if args.policy_command == "lint":
        _handle_lint(project_name=str(args.project), db_path=db_path)
    elif args.policy_command == "explain":
        _handle_explain(task_id=int(args.task), db_path=db_path)


def _handle_lint(*, project_name: str, db_path: Path) -> None:
    """Validate and summarize the effective policy pack for one project."""
    runtime_root = db_path.parent
    try:
        policy_pack = load_policy_pack(project_name, runtime_root=runtime_root)
    except ValueError as exc:
        print(f"Policy lint failed for project '{project_name}': {exc}")
        return

    print(f"Policy lint passed for project '{project_name}'.")
    print(f"Runtime root: {runtime_root}")
    print("Configured built-in checks:")
    _print_policy_check_configuration(policy_pack)


def _handle_explain(*, task_id: int, db_path: Path) -> None:
    """Show policy evaluation context and outcomes for one task."""
    store = Store(db_path)
    try:
        task = store.get_task(task_id)
        if task is None:
            print(f"Error: Task {task_id} not found.")
            return

        project_name = str(task.get("project_name") or "").strip()
        if not project_name:
            print(f"Error: Task {task_id} is missing project metadata.")
            return

        print(f"Policy explanation for task {task_id} (project={project_name})")
        print(f"Task status: {task.get('status')}")

        failure_message = _normalize_non_empty_text(task.get("last_failure"))
        if failure_message is not None:
            print(f"Recorded failure: {failure_message}")

        latest_event = _latest_policy_event_message(store, task_id=task_id)
        if latest_event is not None:
            print(f"Latest policy event: {latest_event}")

        runtime_root = db_path.parent
        try:
            policy_pack = load_policy_pack(project_name, runtime_root=runtime_root)
        except ValueError as exc:
            print(f"Unable to load policy pack: {exc}")
            return

        source_branch = _normalize_non_empty_text(task.get("branch_name"))
        target_branch = _normalize_non_empty_text(task.get("roadmap_integration_branch"))
        if target_branch is None:
            target_branch = DEFAULT_TARGET_BRANCH

        print(f"Source branch: {source_branch or '<missing>'}")
        print(f"Target branch: {target_branch}")

        if not any(_stage_check_specs(policy_pack, stage=stage) for stage in STAGE_ORDER):
            print("No active built-in checks configured for this project.")
            return

        policy_input, input_error = _collect_policy_input(
            task,
            source_branch=source_branch,
            target_branch=target_branch,
        )
        if input_error is not None:
            print(f"Input collection: {input_error}")
        elif policy_input is not None:
            _print_policy_input(policy_input)

        print("Check outcomes:")
        for stage in STAGE_ORDER:
            _print_stage_outcome(
                stage=stage,
                policy_pack=policy_pack,
                policy_input=policy_input,
                input_error=input_error,
            )
    finally:
        store.close()


def _print_policy_check_configuration(policy_pack: PolicyPack) -> None:
    """Render configured built-in checks by stage."""
    for stage in STAGE_ORDER:
        checks = _stage_check_specs(policy_pack, stage=stage)
        if not checks:
            print(f"- {stage}: (none)")
            continue
        print(f"- {stage}:")
        for check in checks:
            print(f"  - {check.code}: {check.details}")


def _print_policy_input(policy_input: BuiltInPolicyInput) -> None:
    """Print summarized git-derived policy input."""
    print("Collected git input:")
    print(
        f"- changed_files ({len(policy_input.changed_files)}): "
        f"{_preview_values(policy_input.changed_files)}"
    )
    print(
        f"- commit_messages ({len(policy_input.commit_messages)}): "
        f"{_preview_values(policy_input.commit_messages)}"
    )


def _print_stage_outcome(
    *,
    stage: PolicyCheckStage,
    policy_pack: PolicyPack,
    policy_input: BuiltInPolicyInput | None,
    input_error: str | None,
) -> None:
    """Print pass/fail outcomes for one policy stage."""
    checks = _stage_check_specs(policy_pack, stage=stage)
    if not checks:
        print(f"- {stage}: (inactive)")
        return

    print(f"- {stage}:")

    if input_error is not None:
        print(f"  - unable to evaluate: {input_error}")
        for check in checks:
            print(f"  - {check.code}: UNKNOWN")
        return

    if policy_input is None:
        print("  - unable to evaluate: missing policy input")
        for check in checks:
            print(f"  - {check.code}: UNKNOWN")
        return

    result = evaluate_builtin_policy_checks(policy_pack, policy_input, stage=stage)
    for check in checks:
        violation_messages = _violation_messages(result, failure_codes=check.failure_codes)
        if violation_messages:
            print(f"  - {check.code}: FAIL")
            for message in violation_messages:
                print(f"    - {message}")
            continue
        print(f"  - {check.code}: PASS")

    stage_outcome = "allowed" if result.allowed else "blocked"
    print(f"  Outcome: {stage_outcome}")


def _collect_policy_input(
    task: dict[str, Any],
    *,
    source_branch: str | None,
    target_branch: str,
) -> tuple[BuiltInPolicyInput | None, str | None]:
    """Collect git input needed by built-in policy checks."""
    if source_branch is None:
        return None, "task branch is missing; unable to collect git policy inputs"

    repo_root = _task_repo_root(task)
    try:
        policy_input = collect_builtin_policy_input(
            repo_root=repo_root,
            source_branch=source_branch,
            target_branch=target_branch,
        )
    except ValueError as exc:
        return None, str(exc)

    return policy_input, None


def _stage_check_specs(policy_pack: PolicyPack, *, stage: PolicyCheckStage) -> tuple[BuiltInCheckSpec, ...]:
    """Return configured built-in checks for a stage."""
    checks: list[BuiltInCheckSpec] = []
    if stage == "done_accept":
        required_regex = policy_pack.quality.required_commit_message_regex
        if required_regex is not None:
            checks.append(
                BuiltInCheckSpec(
                    code="policy.required_commit_message_regex",
                    details=f"required pattern {required_regex!r}",
                    failure_codes=(
                        "policy.required_commit_message_regex",
                        "policy.invalid_commit_message_regex",
                    ),
                )
            )

        max_changed_files = policy_pack.quality.max_files_changed
        if max_changed_files is not None:
            checks.append(
                BuiltInCheckSpec(
                    code="policy.max_changed_files",
                    details=f"max {max_changed_files} changed file(s)",
                    failure_codes=("policy.max_changed_files",),
                )
            )

        return tuple(checks)

    allowed_prefixes = policy_pack.safety.allowed_path_prefixes
    if allowed_prefixes:
        checks.append(
            BuiltInCheckSpec(
                code="policy.allowed_path_prefixes",
                details=f"allowed prefixes: {', '.join(allowed_prefixes)}",
                failure_codes=("policy.allowed_path_prefixes",),
            )
        )

    blocked_paths = policy_pack.safety.blocked_paths
    if blocked_paths:
        checks.append(
            BuiltInCheckSpec(
                code="policy.forbidden_path_pattern",
                details=f"blocked patterns: {', '.join(blocked_paths)}",
                failure_codes=("policy.forbidden_path_pattern",),
            )
        )

    return tuple(checks)


def _violation_messages(
    result: BuiltInPolicyResult,
    *,
    failure_codes: tuple[str, ...],
) -> tuple[str, ...]:
    """Collect unique violation messages for one logical policy check."""
    seen: set[str] = set()
    messages: list[str] = []
    for violation in result.violations:
        if violation.code not in failure_codes:
            continue
        if violation.message in seen:
            continue
        messages.append(violation.message)
        seen.add(violation.message)
    return tuple(messages)


def _latest_policy_event_message(store: Store, *, task_id: int) -> str | None:
    """Return the latest task policy violation event message."""
    events = store.list_events(limit=POLICY_EVENT_SCAN_LIMIT)
    for event in events:
        event_task_id = _as_int(event.get("task_id"))
        if event_task_id != task_id:
            continue
        if str(event.get("kind") or "") != POLICY_EVENT_KIND:
            continue
        return _normalize_non_empty_text(event.get("message"))
    return None


def _task_repo_root(task: dict[str, Any]) -> Path:
    """Resolve repository root for policy git checks."""
    candidate = task.get("project_repo_root")
    if isinstance(candidate, str) and candidate:
        return Path(candidate)
    return Path.cwd()


def _preview_values(values: tuple[str, ...], *, limit: int = 4) -> str:
    """Render compact preview list for policy input values."""
    if not values:
        return "(none)"

    preview = ", ".join(repr(value) for value in values[:limit])
    remaining = len(values) - limit
    if remaining <= 0:
        return preview
    return f"{preview}, ... (+{remaining} more)"


def _normalize_non_empty_text(value: Any) -> str | None:
    """Return stripped text when value is a non-empty string."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _as_int(value: Any) -> int | None:
    """Parse integer ids from SQLite row values."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
