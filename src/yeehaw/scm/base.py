"""SCM adapter contract for roadmap integration branch publishing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class SCMAdapterError(RuntimeError):
    """Raised when an SCM adapter cannot publish integration artifacts."""


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


class SCMAdapter(ABC):
    """Adapter interface for publishing roadmap integration branches."""

    @abstractmethod
    def publish_roadmap_integration(
        self,
        *,
        repo_root: Path,
        roadmap_id: int,
        integration_branch: str,
        base_branch: str = "main",
    ) -> RoadmapPublishResult:
        """Publish integration branch and return branch + summary metadata."""

