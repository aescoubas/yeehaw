"""Typed row models for store query results."""

from __future__ import annotations

from typing import TypedDict


class TaskRow(TypedDict, total=False):
    """Task row returned from store task query methods."""

    id: int
    roadmap_id: int
    phase_id: int
    task_number: str
    title: str
    description: str
    status: str
    assigned_agent: str | None
    branch_name: str | None
    worktree_path: str | None
    signal_dir: str | None
    attempts: int
    max_attempts: int
    last_failure: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str
    max_tokens: int | None
    max_runtime_min: int | None
    tokens_used: int | None
    roadmap_status: str
    roadmap_integration_branch: str | None
    project_name: str
    project_id: int
    project_repo_root: str
