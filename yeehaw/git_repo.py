from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitRepoError(ValueError):
    """Raised when repository metadata cannot be resolved."""


@dataclass(slots=True)
class GitRepoInfo:
    root_path: str
    remote_url: str | None
    default_branch: str | None
    head_sha: str | None


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "git command failed"
        raise GitRepoError(stderr)
    return proc.stdout.strip()


def detect_repo(path: str | Path) -> GitRepoInfo:
    start = Path(path).expanduser().resolve()

    try:
        root = _git(start, "rev-parse", "--show-toplevel")
    except GitRepoError as exc:
        raise GitRepoError(f"{start} is not a git repository: {exc}") from exc

    root_path = Path(root).resolve()

    remote_url: str | None = None
    default_branch: str | None = None
    head_sha: str | None = None

    try:
        remote_url = _git(root_path, "remote", "get-url", "origin")
    except GitRepoError:
        remote_url = None

    try:
        head_ref = _git(root_path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        # Looks like refs/remotes/origin/main.
        if head_ref:
            default_branch = head_ref.rsplit("/", 1)[-1]
    except GitRepoError:
        default_branch = None

    if default_branch is None:
        for candidate in ("main", "master"):
            try:
                _git(root_path, "rev-parse", "--verify", f"refs/heads/{candidate}")
                default_branch = candidate
                break
            except GitRepoError:
                continue

    try:
        head_sha = _git(root_path, "rev-parse", "HEAD")
    except GitRepoError:
        head_sha = None

    return GitRepoInfo(
        root_path=str(root_path),
        remote_url=remote_url,
        default_branch=default_branch,
        head_sha=head_sha,
    )
