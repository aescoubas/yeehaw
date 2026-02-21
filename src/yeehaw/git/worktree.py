"""Git worktree management for task isolation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def branch_name(task_number: str, title: str) -> str:
    """Generate a sanitized git branch name for a task."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:50]
    return f"yeehaw/task-{task_number}-{slug}"


def prepare_worktree(repo_root: Path, branch: str) -> Path:
    """Create a git worktree for the given branch and return its path."""
    dir_name = branch.split("/")[-1]
    worktree_path = repo_root / ".yeehaw" / "worktrees" / dir_name

    if worktree_path.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_root,
            capture_output=True,
        )

    subprocess.run(
        ["git", "branch", "-f", branch, "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )

    return worktree_path


def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove worktree and prune stale entries."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_root,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        capture_output=True,
    )
