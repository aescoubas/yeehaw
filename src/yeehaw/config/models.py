"""Typed models for Yeehaw runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass

FEATURE_FLAG_NAMES: tuple[str, ...] = (
    "hooks",
    "policies",
    "conflict_scheduler",
    "budgets",
    "notifications",
    "pr_automation",
    "memory_packs",
)


@dataclass(frozen=True)
class FeatureFlags:
    """Runtime feature toggles for optional subsystems."""

    hooks: bool = False
    policies: bool = False
    conflict_scheduler: bool = False
    budgets: bool = False
    notifications: bool = False
    pr_automation: bool = False
    memory_packs: bool = False
