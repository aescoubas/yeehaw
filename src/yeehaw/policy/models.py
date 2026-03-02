"""Typed models and validation helpers for policy packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

POLICY_SCHEMA_VERSION = 1

_ROOT_KEYS = frozenset({"schema_version", "quality", "safety"})
_QUALITY_KEYS = frozenset(
    {
        "required_checks",
        "required_commit_message_regex",
        "max_files_changed",
        "max_diff_lines",
    }
)
_SAFETY_KEYS = frozenset(
    {
        "blocked_commands",
        "blocked_paths",
        "allowed_path_prefixes",
        "allow_network",
    }
)


@dataclass(frozen=True)
class QualityPolicy:
    """Quality-oriented constraints."""

    required_checks: tuple[str, ...] = ()
    required_commit_message_regex: str | None = None
    max_files_changed: int | None = None
    max_diff_lines: int | None = None


@dataclass(frozen=True)
class SafetyPolicy:
    """Safety-oriented constraints."""

    blocked_commands: tuple[str, ...] = ()
    blocked_paths: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    allow_network: bool = True


@dataclass(frozen=True)
class PolicyPack:
    """Resolved policy pack with quality and safety constraints."""

    schema_version: int = POLICY_SCHEMA_VERSION
    quality: QualityPolicy = field(default_factory=QualityPolicy)
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)


def parse_policy_pack(payload: Any, *, source: Path | str = "<policy>") -> PolicyPack:
    """Parse and validate a policy payload."""
    if not isinstance(payload, dict):
        raise _policy_error(source, f"root must be an object (got {_json_type_name(payload)})")

    unknown_root_keys = sorted(set(payload) - _ROOT_KEYS)
    if unknown_root_keys:
        keys = ", ".join(unknown_root_keys)
        raise _policy_error(source, f"unsupported top-level keys: {keys}")

    schema_version = payload.get("schema_version", POLICY_SCHEMA_VERSION)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise _policy_error(source, "'schema_version' must be an integer")
    if schema_version != POLICY_SCHEMA_VERSION:
        raise _policy_error(
            source,
            f"unsupported schema_version {schema_version} (expected {POLICY_SCHEMA_VERSION})",
        )

    quality_raw = payload.get("quality", {})
    if not isinstance(quality_raw, dict):
        raise _policy_error(source, "'quality' must be an object")
    unknown_quality_keys = sorted(set(quality_raw) - _QUALITY_KEYS)
    if unknown_quality_keys:
        keys = ", ".join(unknown_quality_keys)
        raise _policy_error(source, f"unsupported keys in 'quality': {keys}")

    safety_raw = payload.get("safety", {})
    if not isinstance(safety_raw, dict):
        raise _policy_error(source, "'safety' must be an object")
    unknown_safety_keys = sorted(set(safety_raw) - _SAFETY_KEYS)
    if unknown_safety_keys:
        keys = ", ".join(unknown_safety_keys)
        raise _policy_error(source, f"unsupported keys in 'safety': {keys}")

    quality = QualityPolicy(
        required_checks=_read_str_list(
            quality_raw,
            key="required_checks",
            field_path="quality.required_checks",
            source=source,
        ),
        required_commit_message_regex=_read_optional_non_empty_str(
            quality_raw,
            key="required_commit_message_regex",
            field_path="quality.required_commit_message_regex",
            source=source,
        ),
        max_files_changed=_read_int_or_none(
            quality_raw,
            key="max_files_changed",
            field_path="quality.max_files_changed",
            source=source,
            minimum=0,
        ),
        max_diff_lines=_read_int_or_none(
            quality_raw,
            key="max_diff_lines",
            field_path="quality.max_diff_lines",
            source=source,
            minimum=0,
        ),
    )

    safety = SafetyPolicy(
        blocked_commands=_read_str_list(
            safety_raw,
            key="blocked_commands",
            field_path="safety.blocked_commands",
            source=source,
        ),
        blocked_paths=_read_str_list(
            safety_raw,
            key="blocked_paths",
            field_path="safety.blocked_paths",
            source=source,
        ),
        allowed_path_prefixes=_read_str_list(
            safety_raw,
            key="allowed_path_prefixes",
            field_path="safety.allowed_path_prefixes",
            source=source,
        ),
        allow_network=_read_bool(
            safety_raw,
            key="allow_network",
            field_path="safety.allow_network",
            source=source,
            default=True,
        ),
    )

    return PolicyPack(
        schema_version=schema_version,
        quality=quality,
        safety=safety,
    )


def policy_pack_to_payload(policy_pack: PolicyPack) -> dict[str, Any]:
    """Serialize a policy pack dataclass to a JSON-compatible payload."""
    payload = asdict(policy_pack)
    quality = payload["quality"]
    safety = payload["safety"]
    quality["required_checks"] = list(policy_pack.quality.required_checks)
    safety["blocked_commands"] = list(policy_pack.safety.blocked_commands)
    safety["blocked_paths"] = list(policy_pack.safety.blocked_paths)
    safety["allowed_path_prefixes"] = list(policy_pack.safety.allowed_path_prefixes)
    return payload


def _read_str_list(
    mapping: dict[str, Any],
    *,
    key: str,
    field_path: str,
    source: Path | str,
) -> tuple[str, ...]:
    raw_value = mapping.get(key, [])
    if raw_value is None:
        raw_value = []

    if not isinstance(raw_value, list):
        raise _policy_error(
            source,
            f"'{field_path}' must be a list of non-empty strings (got {_json_type_name(raw_value)})",
        )

    values: list[str] = []
    for item in raw_value:
        if not isinstance(item, str) or not item.strip():
            raise _policy_error(source, f"'{field_path}' must contain only non-empty strings")
        values.append(item.strip())

    return tuple(dict.fromkeys(values))


def _read_int_or_none(
    mapping: dict[str, Any],
    *,
    key: str,
    field_path: str,
    source: Path | str,
    minimum: int,
) -> int | None:
    raw_value = mapping.get(key)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise _policy_error(
            source,
            f"'{field_path}' must be an integer or null (got {_json_type_name(raw_value)})",
        )
    if raw_value < minimum:
        raise _policy_error(source, f"'{field_path}' must be >= {minimum}")
    return raw_value


def _read_optional_non_empty_str(
    mapping: dict[str, Any],
    *,
    key: str,
    field_path: str,
    source: Path | str,
) -> str | None:
    raw_value = mapping.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise _policy_error(
            source,
            f"'{field_path}' must be a non-empty string or null (got {_json_type_name(raw_value)})",
        )
    stripped = raw_value.strip()
    if not stripped:
        raise _policy_error(source, f"'{field_path}' must be a non-empty string or null")
    return stripped


def _read_bool(
    mapping: dict[str, Any],
    *,
    key: str,
    field_path: str,
    source: Path | str,
    default: bool,
) -> bool:
    raw_value = mapping.get(key, default)
    if not isinstance(raw_value, bool):
        raise _policy_error(
            source,
            f"'{field_path}' must be a boolean (got {_json_type_name(raw_value)})",
        )
    return raw_value


def _json_type_name(value: Any) -> str:
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


def _policy_error(source: Path | str, message: str) -> ValueError:
    return ValueError(f"Invalid policy config in {source}: {message}")
