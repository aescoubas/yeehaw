"""Local git-backed SCM adapter implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from yeehaw.scm.base import (
    PublishedBranch,
    RoadmapPublishResult,
    RoadmapPublishSummary,
    SCMAdapter,
    SCMAdapterError,
)


@dataclass(frozen=True)
class LocalGitSCMAdapter(SCMAdapter):
    """SCM adapter that treats local git refs as the publication target."""

    remote_name: str = "origin"
    max_summary_commits: int = 20

    def publish_roadmap_integration(
        self,
        *,
        repo_root: Path,
        roadmap_id: int,
        integration_branch: str,
        base_branch: str = "main",
    ) -> RoadmapPublishResult:
        """Resolve a publish-ready branch summary using local git state."""
        if self.max_summary_commits < 1:
            raise ValueError("max_summary_commits must be >= 1")

        integration_ref = self._branch_ref(integration_branch)
        base_ref = self._branch_ref(base_branch)

        head_sha = self._resolve_ref(repo_root, integration_ref)
        if head_sha is None:
            raise SCMAdapterError(
                f"Integration branch '{integration_branch}' is missing in {repo_root}"
            )

        if self._resolve_ref(repo_root, base_ref) is None:
            raise SCMAdapterError(
                f"Base branch '{base_branch}' is missing in {repo_root}"
            )

        commit_range = f"{base_ref}..{integration_ref}"
        diff_range = f"{base_ref}...{integration_ref}"
        commits_ahead = self._count_commits(repo_root, commit_range)
        commit_subjects = self._commit_subjects(repo_root, commit_range)
        changed_files = self._changed_files(repo_root, diff_range)

        branch = PublishedBranch(
            provider="git-local",
            branch_name=integration_branch,
            head_sha=head_sha,
            remote_name=self.remote_name,
            remote_url=self._remote_url(repo_root),
        )
        summary = RoadmapPublishSummary(
            roadmap_id=roadmap_id,
            base_branch=base_branch,
            integration_branch=integration_branch,
            head_sha=head_sha,
            commits_ahead=commits_ahead,
            commit_subjects=commit_subjects,
            changed_files=changed_files,
        )
        return RoadmapPublishResult(branch=branch, summary=summary)

    @staticmethod
    def _branch_ref(branch: str) -> str:
        if branch.startswith("refs/"):
            return branch
        return f"refs/heads/{branch}"

    def _resolve_ref(self, repo_root: Path, ref_name: str) -> str | None:
        result = self._git(
            repo_root,
            ["rev-parse", "--verify", "--quiet", ref_name],
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None

    def _count_commits(self, repo_root: Path, commit_range: str) -> int:
        result = self._git(repo_root, ["rev-list", "--count", commit_range])
        count_raw = result.stdout.strip()
        if not count_raw.isdigit():
            raise SCMAdapterError(
                f"Unexpected commit count output for range '{commit_range}': {count_raw!r}"
            )
        return int(count_raw)

    def _commit_subjects(self, repo_root: Path, commit_range: str) -> tuple[str, ...]:
        result = self._git(
            repo_root,
            [
                "log",
                "--format=%s",
                f"--max-count={self.max_summary_commits}",
                commit_range,
            ],
        )
        subjects = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return tuple(subjects)

    def _changed_files(self, repo_root: Path, diff_range: str) -> tuple[str, ...]:
        result = self._git(repo_root, ["diff", "--name-only", diff_range])
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return tuple(files)

    def _remote_url(self, repo_root: Path) -> str | None:
        result = self._git(
            repo_root,
            ["remote", "get-url", self.remote_name],
            check=False,
        )
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
        return remote_url or None

    def _git(
        self,
        repo_root: Path,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
            raise SCMAdapterError(f"git {' '.join(args)} failed: {detail}")
        return result

