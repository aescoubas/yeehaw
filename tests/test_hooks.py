"""Tests for hook discovery and metadata validation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from yeehaw.hooks.loader import discover_hooks, load_hooks


def _write_executable(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 0\n")
    path.chmod(0o755)


def _write_hook_metadata(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload))


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
