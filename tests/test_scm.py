"""Tests for SCM adapter contracts and local git implementation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yeehaw.scm.base import SCMAdapter, SCMAdapterError
from yeehaw.scm.git_local import LocalGitSCMAdapter


class _MissingPublishAdapter(SCMAdapter):
    """Intentional incomplete adapter for abstract contract validation."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _run_git(repo_root, "init")
    _run_git(repo_root, "config", "user.name", "Yeehaw Test")
    _run_git(repo_root, "config", "user.email", "yeehaw@example.com")
    _run_git(repo_root, "branch", "-M", "main")

    (repo_root / "README.md").write_text("seed\n")
    _run_git(repo_root, "add", "README.md")
    _run_git(repo_root, "commit", "-m", "seed")
    return repo_root


def test_scm_adapter_contract_is_abstract() -> None:
    with pytest.raises(TypeError):
        _MissingPublishAdapter()


@pytest.mark.integration
def test_local_git_adapter_publishes_branch_with_summary(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path)
    integration_branch = "yeehaw/roadmap-7"

    _run_git(repo_root, "checkout", "-b", integration_branch)
    feature_file = repo_root / "feature.txt"
    feature_file.write_text("first line\n")
    _run_git(repo_root, "add", "feature.txt")
    _run_git(repo_root, "commit", "-m", "feat: add feature file")

    feature_file.write_text("first line\nsecond line\n")
    _run_git(repo_root, "add", "feature.txt")
    _run_git(repo_root, "commit", "-m", "chore: refine feature file")
    _run_git(repo_root, "checkout", "main")

    adapter = LocalGitSCMAdapter(max_summary_commits=1)
    result = adapter.publish_roadmap_integration(
        repo_root=repo_root,
        roadmap_id=7,
        integration_branch=integration_branch,
        base_branch="main",
    )

    assert result.branch.provider == "git-local"
    assert result.branch.branch_name == integration_branch
    assert result.branch.head_sha
    assert result.branch.remote_name == "origin"
    assert result.branch.remote_url is None

    assert result.summary.roadmap_id == 7
    assert result.summary.base_branch == "main"
    assert result.summary.integration_branch == integration_branch
    assert result.summary.head_sha == result.branch.head_sha
    assert result.summary.commits_ahead == 2
    assert result.summary.commit_subjects == ("chore: refine feature file",)
    assert result.summary.changed_files == ("feature.txt",)


@pytest.mark.integration
def test_local_git_adapter_fails_when_integration_branch_is_missing(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path)

    adapter = LocalGitSCMAdapter()
    with pytest.raises(SCMAdapterError, match="Integration branch"):
        adapter.publish_roadmap_integration(
            repo_root=repo_root,
            roadmap_id=8,
            integration_branch="yeehaw/roadmap-8",
            base_branch="main",
        )


@pytest.mark.integration
def test_local_git_adapter_fails_when_base_branch_is_missing(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path)
    integration_branch = "yeehaw/roadmap-9"

    _run_git(repo_root, "checkout", "-b", integration_branch)
    (repo_root / "feature.txt").write_text("content\n")
    _run_git(repo_root, "add", "feature.txt")
    _run_git(repo_root, "commit", "-m", "feat: add content")
    _run_git(repo_root, "checkout", "main")

    adapter = LocalGitSCMAdapter()
    with pytest.raises(SCMAdapterError, match="Base branch"):
        adapter.publish_roadmap_integration(
            repo_root=repo_root,
            roadmap_id=9,
            integration_branch=integration_branch,
            base_branch="does-not-exist",
        )

