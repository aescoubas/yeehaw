"""Tests for global runtime feature flag loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yeehaw.config.loader import load_feature_flags
from yeehaw.config.models import FeatureFlags
from yeehaw.runtime import runtime_config_path


def test_load_feature_flags_defaults_when_config_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("YEEHAW_HOME", str(tmp_path))

    flags = load_feature_flags()

    assert flags == FeatureFlags()


def test_load_feature_flags_uses_runtime_config_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("YEEHAW_HOME", str(tmp_path))
    config_path = runtime_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "features": {
                    "hooks": True,
                    "notifications": True,
                    "pr_automation": True,
                }
            }
        )
    )

    flags = load_feature_flags()

    assert flags == FeatureFlags(
        hooks=True,
        notifications=True,
        pr_automation=True,
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"features": {"hooks": "yes"}}, "features.hooks must be a boolean"),
        ({"features": {"unknown_flag": True}}, "unknown feature flags: unknown_flag"),
        ({"features": []}, "'features' must be an object"),
    ],
)
def test_load_feature_flags_invalid_config_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
    match: str,
) -> None:
    monkeypatch.setenv("YEEHAW_HOME", str(tmp_path))
    config_path = runtime_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=match):
        load_feature_flags()
