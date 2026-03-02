"""SCM adapter interfaces and implementations."""

from yeehaw.scm.base import (
    PublishedBranch,
    RoadmapPublishResult,
    RoadmapPublishSummary,
    SCMAdapter,
    SCMAdapterError,
)
from yeehaw.scm.git_local import LocalGitSCMAdapter

__all__ = [
    "PublishedBranch",
    "RoadmapPublishResult",
    "RoadmapPublishSummary",
    "SCMAdapter",
    "SCMAdapterError",
    "LocalGitSCMAdapter",
]

