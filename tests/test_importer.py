from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from yeehaw import db, importer
from yeehaw.git_repo import GitRepoError, GitRepoInfo


def test_discover_git_roots(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = b / "nested"
    a.mkdir()
    b.mkdir()
    c.mkdir(parents=True)
    (a / ".git").mkdir()
    (c / ".git").mkdir()
    (b / "node_modules").mkdir()

    roots = importer.discover_git_roots([tmp_path], max_depth=5)
    assert a in roots
    assert c in roots

    roots2 = importer.discover_git_roots([tmp_path / "does-not-exist"], max_depth=1)
    assert roots2 == []
    roots3 = importer.discover_git_roots([tmp_path], max_depth=0)
    assert roots3 == []


def test_slugify_and_name_from_remote() -> None:
    assert importer._slugify("a b/c") == "a-b-c"
    assert importer._slugify("!!!") == "project"
    assert importer._name_from_remote(None, "fallback") == "fallback"
    assert importer._name_from_remote("git@github.com:owner/repo.git", "f") == "owner-repo"
    assert importer._name_from_remote("https://x/y/z", "f") == "y-z"
    assert importer._name_from_remote("single", "f") == "f"


def test_choose_name(conn: sqlite3.Connection) -> None:
    db.create_project(conn, "n", "/tmp/r1", "g")
    assert importer._choose_name(conn, "n", "/tmp/r1") == "n"
    assert importer._choose_name(conn, "x", "/tmp/r2") == "x"
    assert importer._choose_name(conn, "n", "/tmp/r2") == "n-2"

    db.create_project(conn, "n-2", "/tmp/r3", "g")
    assert importer._choose_name(conn, "n", "/tmp/r4") == "n-3"


def test_import_projects_empty(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(importer, "discover_git_roots", lambda *_a, **_k: [])
    result = importer.import_projects(conn, roots=["/tmp"], dry_run=False)
    assert result.created == 0
    assert "No git repositories found" in result.details[0]


def test_import_projects_paths(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, tmp_path: Path) -> None:
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir()
    r2.mkdir()

    monkeypatch.setattr(importer, "discover_git_roots", lambda *_a, **_k: [r1, r2])

    infos = {
        str(r1): GitRepoInfo(root_path=str(r1), remote_url="git@github.com:o/r.git", default_branch="main", head_sha="a"),
        str(r2): GitRepoInfo(root_path=str(r2), remote_url=None, default_branch=None, head_sha=None),
    }

    def fake_detect(path):
        return infos[str(path)]

    monkeypatch.setattr(importer, "detect_repo", fake_detect)

    dry = importer.import_projects(conn, roots=[tmp_path], dry_run=True, default_guidelines="dg")
    assert dry.created == 2
    assert dry.updated == 0

    real = importer.import_projects(conn, roots=[tmp_path], dry_run=False, default_guidelines="dg")
    assert real.created == 2
    assert real.failed == 0

    upd = importer.import_projects(conn, roots=[tmp_path], dry_run=False, default_guidelines="dg")
    assert upd.updated == 2

    dry_upd = importer.import_projects(conn, roots=[tmp_path], dry_run=True, default_guidelines="dg")
    assert dry_upd.updated == 2


def test_import_projects_skip(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, tmp_path: Path) -> None:
    r1 = tmp_path / "r1"
    r1.mkdir()
    monkeypatch.setattr(importer, "discover_git_roots", lambda *_a, **_k: [r1])
    monkeypatch.setattr(importer, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(GitRepoError("bad")))
    res = importer.import_projects(conn, roots=[tmp_path], dry_run=False)
    assert res.skipped == 1
    assert "SKIP" in res.details[0]


def test_discover_git_roots_handles_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / ".git").mkdir()

    orig_iterdir = type(d).iterdir

    def bad_iterdir(self):
        if self == d:
            raise OSError("nope")
        return orig_iterdir(self)

    monkeypatch.setattr(type(d), "iterdir", bad_iterdir)
    roots = importer.discover_git_roots([tmp_path], max_depth=2)
    assert roots == [d]


def test_import_projects_existing_blank_guidelines_gets_default(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    r1 = tmp_path / "r1"
    r1.mkdir()
    monkeypatch.setattr(importer, "discover_git_roots", lambda *_a, **_k: [r1])
    info = GitRepoInfo(root_path=str(r1), remote_url=None, default_branch=None, head_sha=None)
    monkeypatch.setattr(importer, "detect_repo", lambda *_a, **_k: info)

    db.create_project(conn, "r1", str(r1), "")
    res = importer.import_projects(conn, roots=[tmp_path], dry_run=False, default_guidelines="dg")
    assert res.updated == 1
