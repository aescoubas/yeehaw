"""Tests for hook discovery, execution, and protocol validation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from yeehaw.hooks.errors import (
    HookExecutionError,
    HookPayloadTooLargeError,
    HookResponseParseError,
    HookResponseValidationError,
    HookTimeoutError,
)
from yeehaw.hooks.loader import discover_hooks, load_hooks
from yeehaw.hooks.models import HookDefinition, HookRequest
from yeehaw.hooks.runner import run_hook, run_hooks


def _write_executable(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 0\n")
    path.chmod(0o755)


def _write_hook_metadata(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload))


def _write_python_hook(path: Path, source: str) -> None:
    path.write_text(f"#!/usr/bin/env python3\n{source.lstrip()}")
    path.chmod(0o755)


def _hook_definition(entrypoint: Path, *, timeout_ms: int = 2000, name: str = "example") -> HookDefinition:
    return HookDefinition(
        name=name,
        entrypoint=entrypoint.resolve(),
        events=("task.state.changed",),
        source="runtime",
        metadata_path=entrypoint.parent / f"{name}.json",
        timeout_ms=timeout_ms,
    )


def _hook_request(event_id: str = "event-123") -> HookRequest:
    return HookRequest(
        schema_version=1,
        event_name="task.state.changed",
        event_id=event_id,
        emitted_at="2026-03-02T12:00:00Z",
        source={"component": "test", "yeehaw_version": "0.1"},
        context={"from_status": "queued", "to_status": "in-progress"},
        project={"id": 1, "name": "demo", "repo_root": "/tmp/demo"},
        roadmap={"id": 1, "status": "executing", "integration_branch": "yeehaw/roadmap-1"},
        task={"id": 1, "task_number": "1.2", "title": "Run hook", "status": "in-progress"},
        attempt={"current": 1, "max": 4, "timeout_minutes": 60},
    )


def test_discover_hooks_from_runtime_directory(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    hooks_dir = runtime_root / "hooks"
    hooks_dir.mkdir(parents=True)

    entrypoint = hooks_dir / "notify.sh"
    _write_executable(entrypoint)
    _write_hook_metadata(
        hooks_dir / "notify.json",
        {
            "name": "notify",
            "entrypoint": "notify.sh",
            "events": ["task.state.changed"],
            "timeout_ms": 1500,
        },
    )

    discovered = load_hooks(runtime_root=runtime_root)

    assert len(discovered) == 1
    hook = discovered[0]
    assert hook.name == "notify"
    assert hook.source == "runtime"
    assert hook.entrypoint == entrypoint.resolve()
    assert hook.events == ("task.state.changed",)
    assert hook.timeout_ms == 1500


def test_discover_hooks_project_scope_is_opt_in_and_deterministic(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_hooks_dir = runtime_root / "hooks"
    runtime_hooks_dir.mkdir(parents=True)

    runtime_dupe = runtime_hooks_dir / "dupe-runtime.sh"
    _write_executable(runtime_dupe)
    _write_hook_metadata(
        runtime_hooks_dir / "dupe.json",
        {
            "name": "duplicate-hook",
            "entrypoint": "dupe-runtime.sh",
            "events": "task.dispatch.before",
        },
    )

    runtime_only = runtime_hooks_dir / "runtime-only.sh"
    _write_executable(runtime_only)
    _write_hook_metadata(
        runtime_hooks_dir / "runtime-only.json",
        {
            "name": "runtime-only",
            "entrypoint": "runtime-only.sh",
            "events": "task.dispatch.after",
        },
    )

    project_root = tmp_path / "project"
    project_hooks_dir = project_root / ".yeehaw" / "hooks"
    project_hooks_dir.mkdir(parents=True)

    project_dupe = project_hooks_dir / "dupe-project.sh"
    _write_executable(project_dupe)
    _write_hook_metadata(
        project_hooks_dir / "dupe.json",
        {
            "name": "duplicate-hook",
            "entrypoint": "dupe-project.sh",
            "events": ["task.dispatch.before", "task.state.changed"],
        },
    )

    without_project = discover_hooks(
        runtime_root=runtime_root,
        project_root=project_root,
        include_project_hooks=False,
    )
    assert [hook.name for hook in without_project] == ["duplicate-hook", "runtime-only"]
    assert without_project[0].entrypoint == runtime_dupe.resolve()
    assert without_project[0].source == "runtime"

    with_project = discover_hooks(
        runtime_root=runtime_root,
        project_root=project_root,
        include_project_hooks=True,
    )
    assert [hook.name for hook in with_project] == ["duplicate-hook", "runtime-only"]
    assert with_project[0].entrypoint == project_dupe.resolve()
    assert with_project[0].source == "project"


def test_discover_hooks_requires_project_root_for_project_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="project_root is required"):
        discover_hooks(runtime_root=tmp_path, include_project_hooks=True)


def test_discover_hooks_invalid_metadata_reports_actionable_error(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    hooks_dir = runtime_root / "hooks"
    hooks_dir.mkdir(parents=True)

    entrypoint = hooks_dir / "valid.sh"
    _write_executable(entrypoint)
    metadata_path = hooks_dir / "invalid.json"
    _write_hook_metadata(
        metadata_path,
        {
            "name": "invalid",
            "entrypoint": "valid.sh",
            "events": [123],
        },
    )

    with pytest.raises(ValueError, match=re.escape(str(metadata_path))) as exc:
        discover_hooks(runtime_root=runtime_root)

    assert "'events' must contain only non-empty strings" in str(exc.value)


def test_run_hook_success_parses_structured_response(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-success.py"
    _write_python_hook(
        hook_script,
        """
import json
import sys

request = json.load(sys.stdin)
response = {
    "schema_version": request["schema_version"],
    "event_id": request["event_id"],
    "extension": "example-extension",
    "status": "ok",
    "summary": "hook handled event",
    "actions": [
        {
            "type": "log_event",
            "kind": "extension.example.note",
            "message": "observed transition",
        }
    ],
    "metrics": {"duration_ms": 27},
}
sys.stdout.write(json.dumps(response))
""",
    )

    result = run_hook(_hook_definition(hook_script), _hook_request())

    assert result.ok is True
    assert result.error is None
    assert result.response is not None
    assert result.response.extension == "example-extension"
    assert result.response.status == "ok"
    assert result.response.actions[0].type == "log_event"
    assert result.response.actions[0].payload["kind"] == "extension.example.note"


def test_run_hook_timeout_is_captured_without_raising_by_default(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-timeout.py"
    _write_python_hook(
        hook_script,
        """
import time

time.sleep(0.2)
""",
    )

    result = run_hook(_hook_definition(hook_script, timeout_ms=50), _hook_request())

    assert result.ok is False
    assert result.response is None
    assert isinstance(result.error, HookTimeoutError)
    assert result.returncode == 124


def test_run_hook_non_zero_exit_is_classified(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-fail.py"
    _write_python_hook(
        hook_script,
        """
import sys

sys.stderr.write("boom\\n")
raise SystemExit(7)
""",
    )

    result = run_hook(_hook_definition(hook_script), _hook_request())

    assert result.ok is False
    assert isinstance(result.error, HookExecutionError)
    assert result.returncode == 7
    assert result.error.returncode == 7
    assert "boom" in result.stderr


def test_run_hook_strict_mode_raises_on_failure(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-fail-strict.py"
    _write_python_hook(
        hook_script,
        """
raise SystemExit(3)
""",
    )

    with pytest.raises(HookExecutionError, match="exited with code 3"):
        run_hook(_hook_definition(hook_script), _hook_request(), strict=True)


def test_run_hook_invalid_json_is_classified(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-invalid-json.py"
    _write_python_hook(
        hook_script,
        """
import sys

sys.stdout.write("not-json")
""",
    )

    result = run_hook(_hook_definition(hook_script), _hook_request())

    assert result.ok is False
    assert isinstance(result.error, HookResponseParseError)


def test_run_hook_schema_mismatch_is_classified(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-schema-mismatch.py"
    _write_python_hook(
        hook_script,
        """
import json
import sys

request = json.load(sys.stdin)
response = {
    "schema_version": request["schema_version"],
    "event_id": "different-event-id",
    "extension": "example-extension",
    "status": "ok",
}
sys.stdout.write(json.dumps(response))
""",
    )

    result = run_hook(_hook_definition(hook_script), _hook_request())

    assert result.ok is False
    assert isinstance(result.error, HookResponseValidationError)
    assert "does not match request event_id" in str(result.error)


def test_run_hook_output_payload_limit_is_enforced(tmp_path: Path) -> None:
    hook_script = tmp_path / "hook-large-output.py"
    _write_python_hook(
        hook_script,
        """
import json
import sys

request = json.load(sys.stdin)
response = {
    "schema_version": request["schema_version"],
    "event_id": request["event_id"],
    "extension": "example-extension",
    "status": "ok",
    "summary": "x" * 3000,
}
sys.stdout.write(json.dumps(response))
""",
    )

    result = run_hook(
        _hook_definition(hook_script),
        _hook_request(),
        payload_limit_bytes=512,
    )

    assert result.ok is False
    assert isinstance(result.error, HookPayloadTooLargeError)
    assert result.error.stream == "stdout"
    assert result.error.max_bytes == 512


def test_run_hooks_continues_when_single_hook_fails(tmp_path: Path) -> None:
    invalid_hook = tmp_path / "hook-invalid.py"
    valid_hook = tmp_path / "hook-valid.py"
    _write_python_hook(
        invalid_hook,
        """
import sys

sys.stdout.write("not-json")
""",
    )
    _write_python_hook(
        valid_hook,
        """
import json
import sys

request = json.load(sys.stdin)
sys.stdout.write(
    json.dumps(
        {
            "schema_version": request["schema_version"],
            "event_id": request["event_id"],
            "extension": "valid-hook",
            "status": "ok",
        }
    )
)
""",
    )

    results = run_hooks(
        [_hook_definition(invalid_hook, name="invalid"), _hook_definition(valid_hook, name="valid")],
        _hook_request(),
    )

    assert len(results) == 2
    assert isinstance(results[0].error, HookResponseParseError)
    assert results[1].ok is True
