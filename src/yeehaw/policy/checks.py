"""Built-in policy checks used by done-accept and pre-merge gates."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yeehaw.policy.models import PolicyPack

PolicyCheckStage = Literal["done_accept", "pre_merge"]


@dataclass(frozen=True)
class BuiltInPolicyInput:
    """Git-derived input consumed by built-in policy checks."""

    changed_files: tuple[str, ...] = ()
    commit_messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuiltInPolicyViolation:
    """Single built-in policy violation."""

    code: str
    message: str


@dataclass(frozen=True)
class BuiltInPolicyResult:
    """Result of evaluating one built-in policy check stage."""

    allowed: bool
    violations: tuple[BuiltInPolicyViolation, ...] = ()

    @property
    def ok(self) -> bool:
        """Alias for callers that prefer `ok` semantics."""
        return self.allowed


def has_active_builtin_checks(policy_pack: PolicyPack, *, stage: PolicyCheckStage) -> bool:
    """Return True when the selected stage has at least one active built-in check."""
    _validate_stage(stage)
    if stage == "done_accept":
        return (
            policy_pack.quality.required_commit_message_regex is not None
            or policy_pack.quality.max_files_changed is not None
        )
    return bool(
        policy_pack.safety.allowed_path_prefixes
        or policy_pack.safety.blocked_paths
    )


def evaluate_builtin_policy_checks(
    policy_pack: PolicyPack,
    policy_input: BuiltInPolicyInput,
    *,
    stage: PolicyCheckStage,
) -> BuiltInPolicyResult:
    """Evaluate one stage of built-in policy checks."""
    _validate_stage(stage)
    violations: list[BuiltInPolicyViolation] = []
    changed_files = tuple(
        _normalize_path(path)
        for path in policy_input.changed_files
        if path.strip()
    )

    if stage == "done_accept":
        commit_messages = tuple(
            message.strip()
            for message in policy_input.commit_messages
            if message.strip()
        )
        _evaluate_commit_message_regex(
            policy_pack=policy_pack,
            commit_messages=commit_messages,
            violations=violations,
        )
        _evaluate_max_changed_files(
            policy_pack=policy_pack,
            changed_files=changed_files,
            violations=violations,
        )
    else:
        _evaluate_allowed_path_prefixes(
            policy_pack=policy_pack,
            changed_files=changed_files,
            violations=violations,
        )
        _evaluate_forbidden_path_patterns(
            policy_pack=policy_pack,
            changed_files=changed_files,
            violations=violations,
        )

    return BuiltInPolicyResult(allowed=not violations, violations=tuple(violations))


def collect_builtin_policy_input(
    *,
    repo_root: Path,
    source_branch: str,
    target_branch: str,
) -> BuiltInPolicyInput:
    """Collect changed files and commit messages from git for policy checks."""
    source_ref = f"refs/heads/{source_branch}"
    target_ref = f"refs/heads/{target_branch}"

    changed_files = _run_git_lines(
        repo_root=repo_root,
        args=[
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            f"{target_ref}...{source_ref}",
        ],
        purpose=(
            f"list changed files for branch comparison {source_branch} against {target_branch}"
        ),
    )
    commit_messages = _run_git_lines(
        repo_root=repo_root,
        args=["log", "--format=%s", f"{target_ref}..{source_ref}"],
        purpose=(
            f"list task commit messages for branch comparison {source_branch} against {target_branch}"
        ),
    )

    return BuiltInPolicyInput(
        changed_files=changed_files,
        commit_messages=commit_messages,
    )


def _evaluate_commit_message_regex(
    *,
    policy_pack: PolicyPack,
    commit_messages: tuple[str, ...],
    violations: list[BuiltInPolicyViolation],
) -> None:
    required_regex = policy_pack.quality.required_commit_message_regex
    if required_regex is None:
        return

    try:
        pattern = re.compile(required_regex)
    except re.error as exc:
        violations.append(
            BuiltInPolicyViolation(
                code="policy.invalid_commit_message_regex",
                message=(
                    "Configured required commit message regex is invalid: "
                    f"{required_regex!r} ({exc})"
                ),
            )
        )
        return

    if not commit_messages:
        violations.append(
            BuiltInPolicyViolation(
                code="policy.required_commit_message_regex",
                message=(
                    "No task commits were found for done-accept policy validation, "
                    "so commit message format could not be verified"
                ),
            )
        )
        return

    for message in commit_messages:
        if pattern.search(message) is not None:
            continue
        violations.append(
            BuiltInPolicyViolation(
                code="policy.required_commit_message_regex",
                message=(
                    f"Commit message {message!r} does not match required pattern "
                    f"{required_regex!r}"
                ),
            )
        )


def _evaluate_max_changed_files(
    *,
    policy_pack: PolicyPack,
    changed_files: tuple[str, ...],
    violations: list[BuiltInPolicyViolation],
) -> None:
    max_changed_files = policy_pack.quality.max_files_changed
    if max_changed_files is None:
        return
    if len(changed_files) <= max_changed_files:
        return
    violations.append(
        BuiltInPolicyViolation(
            code="policy.max_changed_files",
            message=(
                f"Changed {len(changed_files)} files, which exceeds max_changed_files "
                f"limit {max_changed_files}"
            ),
        )
    )


def _evaluate_allowed_path_prefixes(
    *,
    policy_pack: PolicyPack,
    changed_files: tuple[str, ...],
    violations: list[BuiltInPolicyViolation],
) -> None:
    allowed_prefixes = tuple(
        normalized
        for normalized in (
            _normalize_prefix(prefix)
            for prefix in policy_pack.safety.allowed_path_prefixes
        )
        if normalized
    )
    if not allowed_prefixes:
        return

    rendered_prefixes = ", ".join(allowed_prefixes)
    for changed_file in changed_files:
        if _path_matches_any_prefix(changed_file, allowed_prefixes):
            continue
        violations.append(
            BuiltInPolicyViolation(
                code="policy.allowed_path_prefixes",
                message=(
                    f"Changed file {changed_file!r} is outside allowed path prefixes: "
                    f"{rendered_prefixes}"
                ),
            )
        )


def _evaluate_forbidden_path_patterns(
    *,
    policy_pack: PolicyPack,
    changed_files: tuple[str, ...],
    violations: list[BuiltInPolicyViolation],
) -> None:
    for changed_file in changed_files:
        for pattern in policy_pack.safety.blocked_paths:
            if not fnmatch.fnmatch(changed_file, pattern):
                continue
            violations.append(
                BuiltInPolicyViolation(
                    code="policy.forbidden_path_pattern",
                    message=(
                        f"Changed file {changed_file!r} matches forbidden path pattern {pattern!r}"
                    ),
                )
            )
            break


def _validate_stage(stage: str) -> None:
    if stage in {"done_accept", "pre_merge"}:
        return
    raise ValueError(f"Unsupported policy check stage: {stage}")


def _path_matches_any_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def _normalize_prefix(prefix: str) -> str:
    normalized = prefix.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _run_git_lines(
    *,
    repo_root: Path,
    args: list[str],
    purpose: str,
) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ValueError(f"Unable to {purpose}: {detail}")

    lines: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return tuple(lines)
