"""Runtime feature flag configuration commands."""

from __future__ import annotations

import argparse

import json
from pathlib import Path
from typing import Any

from yeehaw.config.loader import load_feature_flags
from yeehaw.config.models import FEATURE_FLAG_NAMES
from yeehaw.runtime import runtime_config_path

_BOOL_LITERALS: dict[str, bool] = {"true": True, "false": False}


def handle_config(args: argparse.Namespace, db_path: Path) -> None:
    """Handle `yeehaw config` subcommands."""
    _ = db_path
    config_path = runtime_config_path()

    if args.config_command == "show":
        _show_config(config_path)
    elif args.config_command == "set":
        parsed_value = _parse_bool_literal(args.value)
        if parsed_value is None:
            print(f"Error: invalid value '{args.value}'; expected 'true' or 'false'.")
            return
        _set_feature_flag(config_path, args.key, parsed_value)


def _show_config(config_path: Path) -> None:
    """Show effective runtime feature flag state."""
    try:
        flags = load_feature_flags(config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    status = "found" if config_path.exists() else "not found"
    print("Runtime Configuration:")
    print(f"  Config file: {config_path} ({status})")
    print("Feature Flags:")
    for key in FEATURE_FLAG_NAMES:
        print(f"  {key}: {str(getattr(flags, key)).lower()}")


def _set_feature_flag(config_path: Path, key: str, value: bool) -> None:
    """Set one runtime feature flag and persist to runtime config."""
    if key not in FEATURE_FLAG_NAMES:
        print(f"Error: unsupported feature flag '{key}'.")
        return

    try:
        flags = load_feature_flags(config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    updated_features = {name: getattr(flags, name) for name in FEATURE_FLAG_NAMES}
    updated_features[key] = value

    payload = {"features": updated_features}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    print(f"Updated features.{key} = {str(value).lower()}")
    print(f"Config file: {config_path}")


def _parse_bool_literal(raw_value: str | bool) -> bool | None:
    """Parse canonical CLI boolean literals."""
    if isinstance(raw_value, bool):
        return raw_value
    return _BOOL_LITERALS.get(raw_value.strip().lower())
