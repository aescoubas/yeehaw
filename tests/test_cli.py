from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

import pytest

from yeehaw import cli
from yeehaw.git_repo import GitRepoError, GitRepoInfo
from yeehaw.importer import ImportResult
from yeehaw.roadmap import RoadmapDef, StageDef, TrackDef


class _Conn:
    def close(self) -> None:
        return


def _args(**kwargs):
    return argparse.Namespace(**kwargs)


def test_read_text(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text(" x ", encoding="utf-8")
    assert cli._read_text(None) == ""
    assert cli._read_text(str(p)) == "x"


def test_cmd_init_db(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())
    rc = cli.cmd_init_db(_args(db=str(tmp_path / "db.sqlite")))
    assert rc == 0
    assert "DB initialized" in capsys.readouterr().out


def test_cmd_project_create_paths(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())

    monkeypatch.setattr(cli, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(GitRepoError("bad")))
    rc = cli.cmd_project_create(_args(db=None, root=str(tmp_path), guidelines_file=None, allow_non_git=False, name=None))
    assert rc == 2

    calls: dict = {}

    def fake_create_project(_conn, name, root_path, guidelines, **kwargs):
        calls.update({"name": name, "root": root_path, "g": guidelines, **kwargs})
        return 1

    monkeypatch.setattr(cli.db, "create_project", fake_create_project)
    rc2 = cli.cmd_project_create(_args(db=None, root=str(tmp_path), guidelines_file=None, allow_non_git=True, name="n"))
    assert rc2 == 0
    assert calls["name"] == "n"

    repo = GitRepoInfo(root_path=str(tmp_path / "repo"), remote_url="u", default_branch="main", head_sha="sha")
    monkeypatch.setattr(cli, "detect_repo", lambda *_a, **_k: repo)
    rc3 = cli.cmd_project_create(_args(db=None, root=str(tmp_path), guidelines_file=None, allow_non_git=False, name=None))
    out = capsys.readouterr().out
    assert rc3 == 0
    assert "remote=u" in out
    assert "default_branch=main" in out
    assert "head_sha=sha" in out


def test_cmd_project_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(cli.db, "list_projects", lambda *_a, **_k: [])
    assert cli.cmd_project_list(_args(db=None)) == 0
    assert "No projects found" in capsys.readouterr().out

    rows = [{"id": 1, "name": "p", "root_path": "/x", "git_remote_url": None, "default_branch": None}]
    monkeypatch.setattr(cli.db, "list_projects", lambda *_a, **_k: rows)
    cli.cmd_project_list(_args(db=None))
    assert "root=/x" in capsys.readouterr().out


def test_cmd_project_coach(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(GitRepoError("bad")))
    rc = cli.cmd_project_coach(
        _args(
            root=str(tmp_path),
            allow_non_git=False,
            agent="codex",
            guidelines_output="g.md",
            name_hint=None,
            session_prefix="s",
            no_attach=False,
            command=None,
        )
    )
    assert rc == 2

    monkeypatch.setattr(cli, "start_project_coach", lambda **_k: "sess")
    monkeypatch.setattr(
        cli,
        "detect_repo",
        lambda *_a, **_k: GitRepoInfo(root_path=str(tmp_path), remote_url=None, default_branch=None, head_sha=None),
    )
    rc2 = cli.cmd_project_coach(
        _args(
            root=str(tmp_path),
            allow_non_git=False,
            agent="codex",
            guidelines_output="g.md",
            name_hint="n",
            session_prefix="s",
            no_attach=True,
            command=None,
        )
    )
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "Project coach session: sess" in out
    assert "Attach with" in out

    monkeypatch.setattr(cli, "detect_repo", lambda *_a, **_k: (_ for _ in ()).throw(GitRepoError("bad")))
    rc3 = cli.cmd_project_coach(
        _args(
            root=str(tmp_path),
            allow_non_git=True,
            agent="codex",
            guidelines_output="g.md",
            name_hint=None,
            session_prefix="s",
            no_attach=False,
            command=None,
        )
    )
    assert rc3 == 0


def test_cmd_project_import(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(
        cli,
        "import_projects",
        lambda *_a, **_k: ImportResult(created=1, updated=2, skipped=3, failed=0, details=["x"]),
    )
    rc = cli.cmd_project_import(
        _args(db=None, roots=[str(tmp_path)], max_depth=1, guidelines_file=None, dry_run=True)
    )
    assert rc == 0
    assert "Summary: created=1" in capsys.readouterr().out

    monkeypatch.setattr(
        cli,
        "import_projects",
        lambda *_a, **_k: ImportResult(created=0, updated=0, skipped=0, failed=1, details=[]),
    )
    rc2 = cli.cmd_project_import(
        _args(db=None, roots=[str(tmp_path)], max_depth=1, guidelines_file=None, dry_run=False)
    )
    assert rc2 == 1


def test_cmd_roadmap_validate_and_template(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    stage = StageDef(id="s", title="t", goal="g")
    rm = RoadmapDef(version=1, name="n", guidelines=[], tracks=[TrackDef(id="t", topic="x", agent="a", stages=[stage])], raw_text="")
    monkeypatch.setattr(cli, "load_roadmap", lambda *_a, **_k: rm)
    assert cli.cmd_roadmap_validate(_args(path="x", default_agent="codex")) == 0
    assert "Valid roadmap: n" in capsys.readouterr().out

    monkeypatch.setattr(cli, "load_roadmap", lambda *_a, **_k: (_ for _ in ()).throw(cli.RoadmapValidationError("bad")))
    assert cli.cmd_roadmap_validate(_args(path="x", default_agent="codex")) == 2

    out_yaml = tmp_path / "r.yaml"
    assert cli.cmd_roadmap_template(_args(format="yaml", output=str(out_yaml))) == 0
    assert out_yaml.exists()

    out_md = tmp_path / "r.md"
    assert cli.cmd_roadmap_template(_args(format="markdown", output=str(out_md))) == 0
    assert out_md.exists()


def test_cmd_roadmap_coach_and_run_start(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "start_roadmap_coach", lambda **_k: "sess")
    rc = cli.cmd_roadmap_coach(
        _args(project="p", output="r.md", agent="codex", db=None, session_prefix="s", no_attach=True, command=None)
    )
    assert rc == 0
    assert "Attach with" in capsys.readouterr().out

    monkeypatch.setattr(cli, "run_roadmap", lambda **_k: 12)
    rc2 = cli.cmd_run_start(
        _args(project="p", roadmap="r.md", db=None, default_agent="codex", poll_seconds=1.0, session_prefix="s")
    )
    assert rc2 == 0
    assert "id=12" in capsys.readouterr().out


def test_cmd_run_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(cli.db, "latest_runs", lambda *_a, **_k: [])
    assert cli.cmd_run_status(_args(db=None, run_id=None)) == 0
    assert "No runs found" in capsys.readouterr().out

    monkeypatch.setattr(
        cli.db,
        "latest_runs",
        lambda *_a, **_k: [{"id": 1, "status": "running", "project_name": "p", "roadmap_name": "r", "tmux_session": "s"}],
    )
    assert cli.cmd_run_status(_args(db=None, run_id=None)) == 0

    monkeypatch.setattr(cli.db, "get_run", lambda *_a, **_k: None)
    assert cli.cmd_run_status(_args(db=None, run_id=1)) == 2

    monkeypatch.setattr(
        cli.db,
        "get_run",
        lambda *_a, **_k: {"id": 1, "status": "running", "project_name": "p", "tmux_session": "s"},
    )
    monkeypatch.setattr(
        cli.db,
        "run_tracks",
        lambda *_a, **_k: [
            {
                "track_id": "t",
                "status": "awaiting_input",
                "current_stage_index": 1,
                "agent": "codex",
                "window_name": "w",
                "waiting_question": "q",
            }
        ],
    )
    monkeypatch.setattr(cli.db, "run_events", lambda *_a, **_k: [{"created_at": "t", "level": "info", "message": "m"}])
    assert cli.cmd_run_status(_args(db=None, run_id=1)) == 0
    out = capsys.readouterr().out
    assert "question: q" in out
    assert "Events:" in out


def test_cmd_tui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple] = []
    monkeypatch.setattr(cli, "run_tui", lambda **k: called.append((k["db_path"], k["refresh_seconds"])))
    rc = cli.cmd_tui(_args(db="x", refresh_seconds=0.5))
    assert rc == 0
    assert called == [("x", 0.5)]


def test_build_parser_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = cli.build_parser()
    parsed = parser.parse_args(["init-db"])
    assert parsed.command == "init-db"

    p = argparse.ArgumentParser()
    p.set_defaults(func=lambda _args: 9)
    monkeypatch.setattr(cli, "build_parser", lambda: p)
    assert cli.main([]) == 9


def test_cli_module_main_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    argv = ["yeehaw.cli", "--db", str(tmp_path / "db.sqlite"), "init-db"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("yeehaw.cli", run_name="__main__")
    assert exc.value.code == 0
