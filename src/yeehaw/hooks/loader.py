"""Hook discovery and metadata validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from yeehaw.hooks.models import HookDefinition
from yeehaw.runtime import runtime_root as resolve_runtime_root

DEFAULT_HOOK_TIMEOUT_MS = 2000
MAX_HOOK_TIMEOUT_MS = 10_000

_ALLOWED_METADATA_KEYS = frozenset(
    {
        "name",
        "entrypoint",
        "events",
        "timeout_ms",
        "description",
    }
)


def load_hooks(
    runtime_root: Path | None = None,
    *,
    project_root: Path | None = None,
    include_project_hooks: bool = False,
) -> list[HookDefinition]:
    """Load and validate hooks from runtime and optional project scopes."""
    return discover_hooks(
        runtime_root=runtime_root,
        project_root=project_root,
        include_project_hooks=include_project_hooks,
    )


def discover_hooks(
    runtime_root: Path | None = None,
    *,
    project_root: Path | None = None,
    include_project_hooks: bool = False,
) -> list[HookDefinition]:
    """Discover hook metadata files and resolve executable entrypoints.

    Discovery order is deterministic:
    1. Runtime hooks at `<runtime_root>/hooks`
    2. Project hooks at `<project_root>/.yeehaw/hooks` (only when opt-in enabled)

    When duplicate hook names exist, the later source in that order wins.
    This means project hooks override runtime hooks when enabled.
    """
    resolved_runtime_root = runtime_root or resolve_runtime_root()
    source_dirs: list[tuple[str, Path]] = [("runtime", resolved_runtime_root / "hooks")]

    if include_project_hooks:
        if project_root is None:
            raise ValueError("project_root is required when include_project_hooks=True")
        source_dirs.append(("project", project_root / ".yeehaw" / "hooks"))

    discovered: dict[str, HookDefinition] = {}
    for source, hooks_dir in source_dirs:
        for hook in _discover_directory(hooks_dir, source):
            discovered[hook.name] = hook

    return sorted(discovered.values(), key=lambda hook: hook.name)


def _discover_directory(hooks_dir: Path, source: str) -> list[HookDefinition]:
    if not hooks_dir.exists():
        return []
    if not hooks_dir.is_dir():
        raise ValueError(f"Invalid hooks directory {hooks_dir}: expected a directory")

    hooks: list[HookDefinition] = []
    for metadata_path in _metadata_paths(hooks_dir):
        hooks.append(_load_hook_metadata(metadata_path, source))
    return hooks


def _metadata_paths(hooks_dir: Path) -> list[Path]:
    candidates: set[Path] = set()
    for pattern in ("*.hook.json", "*.json", "*/hook.json"):
        for path in hooks_dir.glob(pattern):
            if path.is_file():
                candidates.add(path)
    return sorted(candidates, key=lambda path: path.as_posix())


def _load_hook_metadata(metadata_path: Path, source: str) -> HookDefinition:
    try:
        payload = json.loads(metadata_path.read_text())
    except json.JSONDecodeError as exc:
        raise _metadata_error(metadata_path, f"invalid JSON: {exc}") from exc
    except OSError as exc:
        raise _metadata_error(metadata_path, f"unable to read file: {exc}") from exc

    if not isinstance(payload, dict):
        raise _metadata_error(metadata_path, "metadata must be a JSON object")

    unknown_keys = sorted(set(payload) - _ALLOWED_METADATA_KEYS)
    if unknown_keys:
        keys = ", ".join(unknown_keys)
        raise _metadata_error(metadata_path, f"unsupported keys: {keys}")

    name = _require_non_empty_string(payload, metadata_path, "name")
    events = _require_event_list(payload, metadata_path)
    timeout_ms = _require_timeout(payload, metadata_path)
    entrypoint = _require_entrypoint(payload, metadata_path)

    description_raw = payload.get("description")
    if description_raw is not None and not isinstance(description_raw, str):
        raise _metadata_error(metadata_path, "'description' must be a string")

    return HookDefinition(
        name=name,
        entrypoint=entrypoint,
        events=events,
        source=source,
        metadata_path=metadata_path,
        timeout_ms=timeout_ms,
        description=description_raw,
    )


def _require_non_empty_string(
    payload: dict[str, Any],
    metadata_path: Path,
    key: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _metadata_error(metadata_path, f"'{key}' must be a non-empty string")
    return value.strip()


def _require_event_list(payload: dict[str, Any], metadata_path: Path) -> tuple[str, ...]:
    value = payload.get("events")
    items: list[str]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise _metadata_error(
                metadata_path,
                "'events' must contain only non-empty strings",
            )
        items = value
    else:
        raise _metadata_error(
            metadata_path,
            "'events' must be a non-empty string or list of non-empty strings",
        )

    deduped = tuple(dict.fromkeys(item.strip() for item in items if item.strip()))
    if not deduped:
        raise _metadata_error(metadata_path, "'events' must include at least one event name")
    return deduped


def _require_timeout(payload: dict[str, Any], metadata_path: Path) -> int:
    timeout_ms = payload.get("timeout_ms", DEFAULT_HOOK_TIMEOUT_MS)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise _metadata_error(metadata_path, "'timeout_ms' must be an integer")
    if timeout_ms < 1 or timeout_ms > MAX_HOOK_TIMEOUT_MS:
        raise _metadata_error(
            metadata_path,
            f"'timeout_ms' must be between 1 and {MAX_HOOK_TIMEOUT_MS}",
        )
    return timeout_ms


def _require_entrypoint(payload: dict[str, Any], metadata_path: Path) -> Path:
    entrypoint_raw = _require_non_empty_string(payload, metadata_path, "entrypoint")
    entrypoint_path = Path(entrypoint_raw)
    if not entrypoint_path.is_absolute():
        entrypoint_path = metadata_path.parent / entrypoint_path
    entrypoint_path = entrypoint_path.resolve()

    if not entrypoint_path.exists():
        raise _metadata_error(
            metadata_path,
            f"'entrypoint' does not exist: {entrypoint_path}",
        )
    if not entrypoint_path.is_file():
        raise _metadata_error(
            metadata_path,
            f"'entrypoint' is not a file: {entrypoint_path}",
        )
    if not os.access(entrypoint_path, os.X_OK):
        raise _metadata_error(
            metadata_path,
            f"'entrypoint' is not executable: {entrypoint_path}",
        )
    return entrypoint_path


def _metadata_error(metadata_path: Path, message: str) -> ValueError:
    return ValueError(f"Invalid hook metadata in {metadata_path}: {message}")
