"""Typed SCM models for roadmap publication and PR automation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RoadmapPRAction = Literal["created", "updated", "skipped", "failed"]


@dataclass(frozen=True)
class PublishedBranch:
    """Resolved publication target for a roadmap integration branch."""

    provider: str
    branch_name: str
    head_sha: str
    remote_name: str | None = None
    remote_url: str | None = None


@dataclass(frozen=True)
class RoadmapPublishSummary:
    """Portable summary payload for a published roadmap branch."""

    roadmap_id: int
    base_branch: str
    integration_branch: str
    head_sha: str
    commits_ahead: int
    commit_subjects: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoadmapPublishResult:
    """Combined publication target and summary output."""

    branch: PublishedBranch
    summary: RoadmapPublishSummary


@dataclass(frozen=True)
class RoadmapTaskSummary:
    """Compact task-level summary for roadmap PR descriptions."""

    task_number: str
    title: str
    status: str
    summary: str | None = None


@dataclass(frozen=True)
class RoadmapPhaseSummary:
    """Compact phase-level summary for roadmap PR descriptions."""

    phase_number: int
    title: str
    status: str
    tasks: tuple[RoadmapTaskSummary, ...] = ()


@dataclass(frozen=True)
class SCMEvent:
    """Structured event payload emitted by SCM adapters."""

    kind: str
    message: str


@dataclass(frozen=True)
class SCMAlert:
    """Structured alert payload emitted by SCM adapters."""

    severity: str
    message: str


@dataclass(frozen=True)
class RoadmapPRPublishRequest:
    """Input payload for create/update roadmap PR operations."""

    repo_root: Path
    roadmap_id: int
    integration_branch: str
    base_branch: str = "main"
    enabled: bool = False
    title: str | None = None
    summary: RoadmapPublishSummary | None = None
    phase_summaries: tuple[RoadmapPhaseSummary, ...] = ()


@dataclass(frozen=True)
class RoadmapPRPublication:
    """Resolved GitHub pull request metadata after publish."""

    number: int
    html_url: str
    title: str
    body: str
    state: str


@dataclass(frozen=True)
class RoadmapPRPublishResult:
    """Result payload for create/update roadmap PR operations."""

    provider: str
    action: RoadmapPRAction
    pull_request: RoadmapPRPublication | None = None
    events: tuple[SCMEvent, ...] = ()
    alerts: tuple[SCMAlert, ...] = ()
    error: str | None = None
