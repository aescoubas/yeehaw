"""SCM adapter contract for roadmap integration branch publishing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from yeehaw.scm.models import PublishedBranch, RoadmapPublishResult, RoadmapPublishSummary


class SCMAdapterError(RuntimeError):
    """Raised when an SCM adapter cannot publish integration artifacts."""


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
