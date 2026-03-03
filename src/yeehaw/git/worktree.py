"""Git worktree management for task isolation."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

GIT_SUBPROCESS_TIMEOUT_SEC = 60


def branch_name(task_number: str, title: str) -> str:
    """Generate a sanitized git branch name for a task."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:50]
    return f"yeehaw/task-{task_number}-{slug}"


def prepare_worktree(
    repo_root: Path,
    runtime_root: Path,
    branch: str,
    base_ref: str = "HEAD",
) -> Path:
    """Create a git worktree for the given branch and return its path."""
    dir_name = branch.split("/")[-1]
    worktree_path = _worktrees_root(runtime_root, repo_root) / dir_name
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    if worktree_path.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_root,
            capture_output=True,
            timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
        )

    subprocess.run(
        ["git", "branch", "-f", branch, base_ref],
        cwd=repo_root,
        check=True,
        capture_output=True,
        timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
    )

    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=repo_root,
        check=True,
        capture_output=True,
        timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
    )

    return worktree_path


def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove worktree and prune stale entries."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_root,
        capture_output=True,
        timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
    )
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        capture_output=True,
        timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
    )


def _worktrees_root(runtime_root: Path, repo_root: Path) -> Path:
    """Return per-repo worktree root under the shared runtime directory."""
    repo_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", repo_root.name).strip("-") or "repo"
    repo_hash = hashlib.sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:10]
    return runtime_root / "worktrees" / f"{repo_name}-{repo_hash}"
