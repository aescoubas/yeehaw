"""Runtime policy pack loading with default + project override support."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from yeehaw.policy.models import PolicyPack, parse_policy_pack
from yeehaw.runtime import runtime_root as resolve_runtime_root

POLICIES_DIR_NAME = "policies"
DEFAULT_POLICY_FILENAME = "default.json"


def load_policy_pack(
    project_name: str,
    *,
    runtime_root: Path | None = None,
) -> PolicyPack:
    """Load a policy pack from runtime defaults and project overrides."""
    normalized_project_name = project_name.strip()
    if not normalized_project_name:
        raise ValueError("project_name must be a non-empty string")

    resolved_runtime_root = runtime_root or resolve_runtime_root()
    policies_dir = resolved_runtime_root / POLICIES_DIR_NAME
    default_policy_path = policies_dir / DEFAULT_POLICY_FILENAME
    project_policy_path = _resolve_project_policy_path(policies_dir, normalized_project_name)

    merged_payload: dict[str, Any] = {}
    source_paths: list[Path] = []

    if default_policy_path.exists():
        default_payload = _read_policy_payload(default_policy_path)
        parse_policy_pack(default_payload, source=default_policy_path)
        merged_payload = _deep_merge(merged_payload, default_payload)
        source_paths.append(default_policy_path)

    if project_policy_path is not None:
        project_payload = _read_policy_payload(project_policy_path)
        parse_policy_pack(project_payload, source=project_policy_path)
        merged_payload = _deep_merge(merged_payload, project_payload)
        source_paths.append(project_policy_path)

    merged_source = " + ".join(str(path) for path in source_paths) if source_paths else "<defaults>"
    return parse_policy_pack(merged_payload, source=merged_source)


def _resolve_project_policy_path(policies_dir: Path, project_name: str) -> Path | None:
    for path in _project_policy_candidates(policies_dir, project_name):
        if path.exists():
            return path
    return None


def _project_policy_candidates(policies_dir: Path, project_name: str) -> list[Path]:
    slug = _slugify(project_name)
    base_names = _dedupe_preserve_order([project_name, slug])

    candidates: list[Path] = []
    for base_name in base_names:
        for parent in (policies_dir, policies_dir / "projects"):
            for filename in (f"{base_name}.json", f"{base_name}.policy.json"):
                candidates.append(parent / filename)
    return candidates


def _read_policy_payload(policy_path: Path) -> dict[str, Any]:
    if not policy_path.is_file():
        raise ValueError(f"Invalid policy config in {policy_path}: expected a file")

    try:
        payload = json.loads(policy_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in policy config {policy_path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to read policy config {policy_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(
            f"Invalid policy config in {policy_path}: root must be an object"
        )
    return payload


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = _deep_merge(base_value, override_value)
        else:
            merged[key] = override_value
    return merged


def _slugify(project_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped
