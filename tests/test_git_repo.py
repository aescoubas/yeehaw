from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw import git_repo


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_git_success_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run_ok(*_args, **_kwargs):
        return _Proc(0, stdout="ok\n")

    monkeypatch.setattr(git_repo.subprocess, "run", fake_run_ok)
    assert git_repo._git(tmp_path, "status") == "ok"

    def fake_run_err(*_args, **_kwargs):
        return _Proc(1, stderr="boom")

    monkeypatch.setattr(git_repo.subprocess, "run", fake_run_err)
    with pytest.raises(git_repo.GitRepoError, match="boom"):
        git_repo._git(tmp_path, "status")


def test_detect_repo_not_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(git_repo, "_git", lambda *_args: (_ for _ in ()).throw(git_repo.GitRepoError("fatal")))
    with pytest.raises(git_repo.GitRepoError, match="is not a git repository"):
        git_repo.detect_repo(tmp_path)


def test_detect_repo_with_origin_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def fake_git(path: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("remote", "get-url", "origin"):
            return "git@github.com:a/b.git"
        if args == ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"):
            return "refs/remotes/origin/main"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        raise AssertionError(args)

    monkeypatch.setattr(git_repo, "_git", fake_git)
    info = git_repo.detect_repo(tmp_path)
    assert info.root_path == str(root.resolve())
    assert info.remote_url == "git@github.com:a/b.git"
    assert info.default_branch == "main"
    assert info.head_sha == "abc123"


def test_detect_repo_without_origin_head_fallbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def fake_git(path: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("remote", "get-url", "origin"):
            raise git_repo.GitRepoError("no remote")
        if args == ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"):
            raise git_repo.GitRepoError("no symbolic")
        if args == ("rev-parse", "--verify", "refs/heads/main"):
            raise git_repo.GitRepoError("no main")
        if args == ("rev-parse", "--verify", "refs/heads/master"):
            return "ok"
        if args == ("rev-parse", "HEAD"):
            raise git_repo.GitRepoError("detached")
        raise AssertionError(args)

    monkeypatch.setattr(git_repo, "_git", fake_git)
    info = git_repo.detect_repo(tmp_path)
    assert info.remote_url is None
    assert info.default_branch == "master"
    assert info.head_sha is None
