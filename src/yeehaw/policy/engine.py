"""Policy evaluation entrypoints for quality and safety constraints."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from yeehaw.policy.models import PolicyPack

_NETWORK_COMMAND_PREFIXES = (
    "curl",
    "wget",
    "http",
    "httpie",
    "nc",
    "ncat",
    "ssh",
    "scp",
    "rsync",
)


@dataclass(frozen=True)
class PolicyEvaluationInput:
    """Runtime data used to evaluate a policy pack."""

    executed_checks: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    diff_lines: int | None = None
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyViolation:
    """Single policy violation detail."""

    code: str
    message: str


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Result of evaluating a policy pack against runtime inputs."""

    allowed: bool
    violations: tuple[PolicyViolation, ...] = ()

    @property
    def ok(self) -> bool:
        """Alias used by callers that prefer `ok` semantics."""
        return self.allowed


def evaluate_policy(
    policy_pack: PolicyPack,
    evaluation_input: PolicyEvaluationInput,
) -> PolicyEvaluationResult:
    """Evaluate runtime input against policy constraints."""
    violations: list[PolicyViolation] = []

    executed_checks = set(evaluation_input.executed_checks)
    changed_files = tuple(str(path) for path in evaluation_input.changed_files)

    for required_check in policy_pack.quality.required_checks:
        if required_check not in executed_checks:
            violations.append(
                PolicyViolation(
                    code="quality.missing_required_check",
                    message=f"Required check '{required_check}' was not executed",
                )
            )

    max_files_changed = policy_pack.quality.max_files_changed
    if max_files_changed is not None and len(changed_files) > max_files_changed:
        violations.append(
            PolicyViolation(
                code="quality.max_files_exceeded",
                message=(
                    f"Changed {len(changed_files)} files, exceeds configured limit "
                    f"{max_files_changed}"
                ),
            )
        )

    max_diff_lines = policy_pack.quality.max_diff_lines
    if (
        max_diff_lines is not None
        and evaluation_input.diff_lines is not None
        and evaluation_input.diff_lines > max_diff_lines
    ):
        violations.append(
            PolicyViolation(
                code="quality.max_diff_exceeded",
                message=(
                    f"Diff size {evaluation_input.diff_lines} exceeds configured limit "
                    f"{max_diff_lines}"
                ),
            )
        )

    blocked_commands = policy_pack.safety.blocked_commands
    for command in evaluation_input.commands:
        for blocked_command in blocked_commands:
            if _matches_command_prefix(command, blocked_command):
                violations.append(
                    PolicyViolation(
                        code="safety.blocked_command",
                        message=f"Command '{command}' matches blocked command '{blocked_command}'",
                    )
                )
                break

    blocked_path_patterns = policy_pack.safety.blocked_paths
    for changed_file in changed_files:
        for pattern in blocked_path_patterns:
            if fnmatch.fnmatch(changed_file, pattern):
                violations.append(
                    PolicyViolation(
                        code="safety.blocked_path",
                        message=(
                            f"Changed file '{changed_file}' matches blocked path pattern '{pattern}'"
                        ),
                    )
                )
                break

    if not policy_pack.safety.allow_network:
        for command in evaluation_input.commands:
            if _is_network_command(command):
                violations.append(
                    PolicyViolation(
                        code="safety.network_disabled",
                        message=f"Network command '{command}' is not allowed by policy",
                    )
                )

    return PolicyEvaluationResult(
        allowed=not violations,
        violations=tuple(violations),
    )


def evaluate_policy_pack(
    policy_pack: PolicyPack,
    evaluation_input: PolicyEvaluationInput,
) -> PolicyEvaluationResult:
    """Backward-compatible alias for policy evaluation."""
    return evaluate_policy(policy_pack, evaluation_input)


def _matches_command_prefix(command: str, blocked_command: str) -> bool:
    normalized_command = command.strip()
    normalized_blocked = blocked_command.strip()
    return normalized_command == normalized_blocked or normalized_command.startswith(
        f"{normalized_blocked} "
    )


def _is_network_command(command: str) -> bool:
    normalized = command.strip()
    if not normalized:
        return False
    first_token = normalized.split()[0].lower()
    return first_token in _NETWORK_COMMAND_PREFIXES
