"""SCM adapter interfaces and implementations."""

from yeehaw.scm.base import (
    SCMAdapter,
    SCMAdapterError,
)
from yeehaw.scm.github import GitHubSCMAdapter
from yeehaw.scm.git_local import LocalGitSCMAdapter
from yeehaw.scm.models import (
    PublishedBranch,
    RoadmapPRPublication,
    RoadmapPRPublishRequest,
    RoadmapPRPublishResult,
    RoadmapPhaseSummary,
    RoadmapPublishResult,
    RoadmapPublishSummary,
    RoadmapTaskSummary,
    SCMAlert,
    SCMEvent,
)

__all__ = [
    "PublishedBranch",
    "RoadmapTaskSummary",
    "RoadmapPhaseSummary",
    "RoadmapPublishResult",
    "RoadmapPublishSummary",
    "RoadmapPRPublishRequest",
    "RoadmapPRPublication",
    "RoadmapPRPublishResult",
    "SCMEvent",
    "SCMAlert",
    "SCMAdapter",
    "SCMAdapterError",
    "LocalGitSCMAdapter",
    "GitHubSCMAdapter",
]
