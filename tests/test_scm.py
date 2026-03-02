"""Tests for SCM adapter contracts and local git implementation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yeehaw.scm.base import SCMAdapter, SCMAdapterError
from yeehaw.scm.github import GitHubSCMAdapter
from yeehaw.scm.git_local import LocalGitSCMAdapter
from yeehaw.scm.models import (
    RoadmapPRPublishRequest,
    RoadmapPhaseSummary,
    RoadmapPublishSummary,
    RoadmapTaskSummary,
)


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


def test_github_adapter_skips_when_pr_automation_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubSCMAdapter(owner="octocat", repo="yeehaw", token="token", enabled=True)

    def _unexpected_call(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("GitHub API should not be called when automation is disabled")

    monkeypatch.setattr(GitHubSCMAdapter, "_request_json", _unexpected_call)
    request = RoadmapPRPublishRequest(
        repo_root=tmp_path,
        roadmap_id=7,
        integration_branch="yeehaw/roadmap-7",
        enabled=False,
    )

    result = adapter.publish_roadmap_pull_request(request)

    assert result.provider == "github"
    assert result.action == "skipped"
    assert result.error is None
    assert result.pull_request is None
    assert result.alerts == ()
    assert result.events[0].kind == "roadmap_pr_publish_skipped"


def test_github_adapter_creates_pr_with_task_and_phase_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubSCMAdapter(owner="octocat", repo="yeehaw", token="token", enabled=True)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _fake_request_json(
        _self: GitHubSCMAdapter,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        calls.append((method, path, payload))
        if method == "GET":
            return []
        if method == "POST":
            assert payload is not None
            return {
                "number": 12,
                "html_url": "https://github.com/octocat/yeehaw/pull/12",
                "title": payload["title"],
                "body": payload["body"],
                "state": "open",
            }
        raise AssertionError(f"Unexpected method {method}")

    monkeypatch.setattr(GitHubSCMAdapter, "_request_json", _fake_request_json)
    request = RoadmapPRPublishRequest(
        repo_root=tmp_path,
        roadmap_id=7,
        integration_branch="yeehaw/roadmap-7",
        base_branch="main",
        enabled=True,
        summary=RoadmapPublishSummary(
            roadmap_id=7,
            base_branch="main",
            integration_branch="yeehaw/roadmap-7",
            head_sha="abc123",
            commits_ahead=2,
            commit_subjects=("feat: add github adapter", "test: cover scm"),
            changed_files=("src/yeehaw/scm/github.py", "tests/test_scm.py"),
        ),
        phase_summaries=(
            RoadmapPhaseSummary(
                phase_number=1,
                title="SCM automation",
                status="completed",
                tasks=(
                    RoadmapTaskSummary(
                        task_number="7.1",
                        title="SCM contract",
                        status="done",
                    ),
                    RoadmapTaskSummary(
                        task_number="7.2",
                        title="GitHub adapter",
                        status="done",
                        summary="Creates or updates roadmap PR",
                    ),
                ),
            ),
        ),
    )

    result = adapter.publish_roadmap_pull_request(request)

    assert result.action == "created"
    assert result.error is None
    assert result.pull_request is not None
    assert result.pull_request.number == 12
    assert result.pull_request.html_url.endswith("/pull/12")
    assert result.events[0].kind == "roadmap_pr_created"
    assert result.alerts == ()

    assert len(calls) == 2
    method_1, path_1, payload_1 = calls[0]
    assert method_1 == "GET"
    assert payload_1 is None
    assert path_1.startswith("/repos/octocat/yeehaw/pulls?")
    assert "state=open" in path_1
    assert "head=octocat%3Ayeehaw%2Froadmap-7" in path_1
    assert "base=main" in path_1

    method_2, path_2, payload_2 = calls[1]
    assert method_2 == "POST"
    assert path_2 == "/repos/octocat/yeehaw/pulls"
    assert payload_2 is not None
    assert payload_2["base"] == "main"
    assert payload_2["head"] == "yeehaw/roadmap-7"
    assert "Roadmap 7" in payload_2["title"]
    assert "Phase 1 [completed]: SCM automation" in payload_2["body"]
    assert "Task 7.2 [done]: GitHub adapter" in payload_2["body"]
    assert "Creates or updates roadmap PR" in payload_2["body"]


def test_github_adapter_updates_existing_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubSCMAdapter(owner="octocat", repo="yeehaw", token="token", enabled=True)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _fake_request_json(
        _self: GitHubSCMAdapter,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        calls.append((method, path, payload))
        if method == "GET":
            return [{"number": 51}]
        if method == "PATCH":
            assert payload is not None
            return {
                "number": 51,
                "html_url": "https://github.com/octocat/yeehaw/pull/51",
                "title": payload["title"],
                "body": payload["body"],
                "state": "open",
            }
        raise AssertionError(f"Unexpected method {method}")

    monkeypatch.setattr(GitHubSCMAdapter, "_request_json", _fake_request_json)
    request = RoadmapPRPublishRequest(
        repo_root=tmp_path,
        roadmap_id=7,
        integration_branch="yeehaw/roadmap-7",
        enabled=True,
    )

    result = adapter.publish_roadmap_pull_request(request)

    assert result.action == "updated"
    assert result.error is None
    assert result.pull_request is not None
    assert result.pull_request.number == 51
    assert result.events[0].kind == "roadmap_pr_updated"
    assert result.alerts == ()

    assert len(calls) == 2
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"
    assert calls[1][1] == "/repos/octocat/yeehaw/pulls/51"
    assert calls[1][2] is not None
    assert "head" not in calls[1][2]
    assert calls[1][2]["base"] == "main"


def test_github_adapter_reports_alert_and_event_on_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubSCMAdapter(owner="octocat", repo="yeehaw", token="token", enabled=True)

    def _raise_error(
        _self: GitHubSCMAdapter,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        raise SCMAdapterError(f"{method} {path} failed")

    monkeypatch.setattr(GitHubSCMAdapter, "_request_json", _raise_error)
    request = RoadmapPRPublishRequest(
        repo_root=tmp_path,
        roadmap_id=8,
        integration_branch="yeehaw/roadmap-8",
        enabled=True,
    )

    result = adapter.publish_roadmap_pull_request(request)

    assert result.provider == "github"
    assert result.action == "failed"
    assert result.pull_request is None
    assert result.error is not None
    assert "failed" in result.error
    assert result.events[0].kind == "roadmap_pr_publish_failed"
    assert result.alerts[0].severity == "warn"
    assert "failed" in result.alerts[0].message
