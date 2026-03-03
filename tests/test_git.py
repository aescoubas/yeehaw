"""Tests for git worktree helper utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import yeehaw.git.worktree as worktree_mod
from yeehaw.git.worktree import branch_name, cleanup_worktree, prepare_worktree


def test_branch_name_sanitizes_and_truncates() -> None:
    branch = branch_name("12.3", "Hello, World! THIS_is A Long Title" * 4)

    assert branch.startswith("yeehaw/task-12.3-")
    assert " " not in branch
    assert branch.count("/") == 1
    assert len(branch.split("-")[-1]) <= 50


@pytest.mark.integration
def test_prepare_and_cleanup_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Yeehaw Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "yeehaw@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (repo / "README.md").write_text("base\n")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    branch = branch_name("1.1", "Add parser")
    runtime_root = tmp_path / "runtime"
    worktree = prepare_worktree(repo, runtime_root, branch)

    assert worktree.exists()
    assert (worktree / "README.md").exists()

    cleanup_worktree(repo, worktree)
    assert not worktree.exists()


def test_worktree_git_commands_use_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worktree_mod.subprocess, "run", fake_run)

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime"
    branch = branch_name("1.1", "Task")
    worktree = prepare_worktree(repo, runtime_root, branch)
    cleanup_worktree(repo, worktree)

    assert calls
    for _args, kwargs in calls:
        assert kwargs.get("timeout") == worktree_mod.GIT_SUBPROCESS_TIMEOUT_SEC
