"""Runtime config loading with strict feature flag validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yeehaw.config.models import FEATURE_FLAG_NAMES, FeatureFlags
from yeehaw.runtime import runtime_config_path

_ROOT_KEYS = frozenset({"features"})
_FEATURE_KEYS = frozenset(FEATURE_FLAG_NAMES)


def load_feature_flags(config_path: Path | None = None) -> FeatureFlags:
    """Load feature flags from runtime config, defaulting to all disabled."""
    resolved_path = config_path or runtime_config_path()
    if not resolved_path.exists():
        return FeatureFlags()

    try:
        raw_payload = json.loads(resolved_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in runtime config {resolved_path}: {exc}") from exc

    if not isinstance(raw_payload, dict):
        raise ValueError(f"Invalid runtime config in {resolved_path}: root must be an object")

    unknown_root_keys = sorted(set(raw_payload) - _ROOT_KEYS)
    if unknown_root_keys:
        keys = ", ".join(unknown_root_keys)
        raise ValueError(
            f"Invalid runtime config in {resolved_path}: unsupported top-level keys: {keys}"
        )

    raw_features = raw_payload.get("features", {})
    if not isinstance(raw_features, dict):
        raise ValueError(f"Invalid runtime config in {resolved_path}: 'features' must be an object")

    unknown_feature_keys = sorted(set(raw_features) - _FEATURE_KEYS)
    if unknown_feature_keys:
        keys = ", ".join(unknown_feature_keys)
        raise ValueError(f"Invalid runtime config in {resolved_path}: unknown feature flags: {keys}")

    parsed_flags: dict[str, bool] = {}
    for key in FEATURE_FLAG_NAMES:
        value = raw_features.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(
                f"Invalid runtime config in {resolved_path}: "
                f"features.{key} must be a boolean (got {_json_type_name(value)})"
            )
        parsed_flags[key] = value

    return FeatureFlags(**parsed_flags)


def _json_type_name(value: Any) -> str:
    """Return a human-readable JSON type label."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
