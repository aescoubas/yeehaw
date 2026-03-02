"""Tests for policy pack loading, validation, and evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from yeehaw.policy.engine import PolicyEvaluationInput, evaluate_policy
from yeehaw.policy.loader import load_policy_pack
from yeehaw.policy.models import PolicyPack, QualityPolicy, SafetyPolicy


def test_load_policy_pack_defaults_when_files_missing(tmp_path: Path) -> None:
    policy_pack = load_policy_pack("demo-project", runtime_root=tmp_path)

    assert policy_pack == PolicyPack()


def test_load_policy_pack_merges_default_and_project_override(tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    (policies_dir / "projects").mkdir(parents=True)

    default_policy_path = policies_dir / "default.json"
    default_policy_path.write_text(
        json.dumps(
            {
                "quality": {
                    "required_checks": ["pytest -q", "ruff check ."],
                    "max_files_changed": 12,
                    "max_diff_lines": 1000,
                },
                "safety": {
                    "blocked_commands": ["git reset --hard"],
                    "allow_network": True,
                },
            }
        )
    )

    project_policy_path = policies_dir / "projects" / "demo-project.json"
    project_policy_path.write_text(
        json.dumps(
            {
                "quality": {
                    "max_files_changed": 3,
                },
                "safety": {
                    "blocked_paths": ["secrets/*", "*.pem"],
                },
            }
        )
    )

    policy_pack = load_policy_pack("demo-project", runtime_root=tmp_path)

    assert policy_pack.quality.required_checks == ("pytest -q", "ruff check .")
    assert policy_pack.quality.max_files_changed == 3
    assert policy_pack.quality.max_diff_lines == 1000
    assert policy_pack.safety.blocked_commands == ("git reset --hard",)
    assert policy_pack.safety.blocked_paths == ("secrets/*", "*.pem")
    assert policy_pack.safety.allow_network is True


def test_load_policy_pack_uses_configured_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("YEEHAW_HOME", str(tmp_path))
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir(parents=True)

    project_policy_path = policies_dir / "demo-project.policy.json"
    project_policy_path.write_text(
        json.dumps(
            {
                "quality": {
                    "required_checks": ["pytest -q"],
                },
                "safety": {
                    "allow_network": False,
                },
            }
        )
    )

    policy_pack = load_policy_pack("demo-project")

    assert policy_pack.quality.required_checks == ("pytest -q",)
    assert policy_pack.safety.allow_network is False


@pytest.mark.parametrize(
    ("relative_path", "payload", "expected_error_fragment"),
    [
        (
            "policies/default.json",
            {"quality": {"max_files_changed": "many"}},
            "quality.max_files_changed' must be an integer or null",
        ),
        (
            "policies/projects/demo-project.json",
            {"safety": {"unknown_field": True}},
            "unsupported keys in 'safety': unknown_field",
        ),
    ],
)
def test_load_policy_pack_invalid_schema_reports_actionable_error(
    tmp_path: Path,
    relative_path: str,
    payload: dict[str, object],
    expected_error_fragment: str,
) -> None:
    policy_path = tmp_path / relative_path
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=re.escape(str(policy_path))) as exc:
        load_policy_pack("demo-project", runtime_root=tmp_path)

    assert expected_error_fragment in str(exc.value)


def test_evaluate_policy_returns_violations_when_constraints_fail() -> None:
    policy_pack = PolicyPack(
        quality=QualityPolicy(
            required_checks=("pytest -q",),
            max_files_changed=1,
            max_diff_lines=40,
        ),
        safety=SafetyPolicy(
            blocked_commands=("git reset --hard",),
            blocked_paths=("secrets/*",),
            allow_network=False,
        ),
    )

    evaluation_input = PolicyEvaluationInput(
        executed_checks=("ruff check .",),
        changed_files=("src/main.py", "secrets/token.txt"),
        diff_lines=200,
        commands=("git reset --hard HEAD", "curl https://example.com"),
    )

    result = evaluate_policy(policy_pack, evaluation_input)

    assert result.allowed is False
    violation_codes = {violation.code for violation in result.violations}
    assert {
        "quality.missing_required_check",
        "quality.max_files_exceeded",
        "quality.max_diff_exceeded",
        "safety.blocked_command",
        "safety.blocked_path",
        "safety.network_disabled",
    }.issubset(violation_codes)


def test_evaluate_policy_allows_input_when_no_constraints_fail() -> None:
    policy_pack = PolicyPack(
        quality=QualityPolicy(
            required_checks=("pytest -q",),
            max_files_changed=4,
            max_diff_lines=250,
        ),
        safety=SafetyPolicy(
            blocked_commands=("git reset --hard",),
            blocked_paths=("secrets/*",),
            allow_network=True,
        ),
    )

    evaluation_input = PolicyEvaluationInput(
        executed_checks=("pytest -q", "ruff check ."),
        changed_files=("src/main.py",),
        diff_lines=120,
        commands=("pytest -q",),
    )

    result = evaluate_policy(policy_pack, evaluation_input)

    assert result.allowed is True
    assert result.violations == ()
