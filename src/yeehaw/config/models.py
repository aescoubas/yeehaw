"""Typed models for Yeehaw runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class FeatureFlags:
    """Runtime feature toggles for optional subsystems."""

    hooks: bool = False
    policies: bool = False
    conflict_scheduler: bool = False
    trivial_conflict_resolver: bool = False
    budgets: bool = False
    notifications: bool = False
    pr_automation: bool = False
    memory_packs: bool = False


FEATURE_FLAG_NAMES: tuple[str, ...] = tuple(field.name for field in fields(FeatureFlags))
