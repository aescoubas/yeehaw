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


def test_cmd_batch_and_task_and_scheduler(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "create_batch_from_task_list", lambda **_k: 77)
    tasks_file = tmp_path / "tasks.txt"
    tasks_file.write_text("- a\n- b\n", encoding="utf-8")
    rc = cli.cmd_batch_create(
        _args(
            project="p",
            name="b",
            tasks=None,
            tasks_file=str(tasks_file),
            planner_agent="codex",
            db=None,
            timeout_minutes=1,
        )
    )
    assert rc == 0
    assert "id=77" in capsys.readouterr().out

    rc2 = cli.cmd_batch_create(
        _args(
            project="p",
            name="b",
            tasks="",
            tasks_file=None,
            planner_agent="codex",
            db=None,
            timeout_minutes=1,
        )
    )
    assert rc2 == 2

    called_replan: list[tuple] = []
    monkeypatch.setattr(
        cli,
        "replan_batch_from_roadmap",
        lambda **k: called_replan.append((k["batch_id"], k["roadmap_path"])),
    )
    rc3 = cli.cmd_batch_replan(_args(batch_id=3, roadmap="r.md", db=None))
    assert rc3 == 0
    assert called_replan == [(3, "r.md")]

    class _Conn2:
        pass

    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn2())
    monkeypatch.setattr(cli.db, "get_project", lambda *_a, **_k: {"id": 1} if _a[1] == "p" else None)
    monkeypatch.setattr(
        cli.db,
        "list_tasks",
        lambda *_a, **_k: [
            {
                "id": 1,
                "status": "awaiting_input",
                "project_name": "p",
                "priority": "high",
                "assigned_agent": "codex",
                "preferred_agent": None,
                "title": "T1",
                "blocked_question": "q?",
            }
        ],
    )
    assert cli.cmd_task_list(_args(db=None, status=None, project="p", limit=10)) == 0
    assert "question: q?" in capsys.readouterr().out

    monkeypatch.setattr(cli.db, "get_project", lambda *_a, **_k: None)
    assert cli.cmd_task_list(_args(db=None, status=None, project="missing", limit=10)) == 2

    monkeypatch.setattr(cli.db, "get_project", lambda *_a, **_k: {"id": 1})
    monkeypatch.setattr(cli.db, "list_tasks", lambda *_a, **_k: [])
    assert cli.cmd_task_list(_args(db=None, status="queued", project="p", limit=10)) == 0

    class _Sched:
        def __init__(self, **_k):
            self.calls: list[tuple] = []

        def reply_to_task(self, task_id: int, answer: str) -> None:
            self.calls.append(("reply", task_id, answer))

        def pause_task(self, task_id: int) -> None:
            self.calls.append(("pause", task_id))

        def tick(self):
            class _Stats:
                dispatched = 1
                completed = 2
                awaiting_input = 3
                reassigned = 4
                failed = 5

            return _Stats()

        def run_forever(self) -> None:
            return None

    sched = _Sched()
    monkeypatch.setattr(cli, "GlobalScheduler", lambda **_k: sched)
    assert cli.cmd_task_reply(_args(db=None, task_id=1, answer="a")) == 0
    assert cli.cmd_task_pause(_args(db=None, task_id=1)) == 0
    assert cli.cmd_scheduler_tick(_args(db=None, poll_seconds=0.1, max_attempts=3)) == 0
    assert "dispatched=1" in capsys.readouterr().out
    assert cli.cmd_scheduler_start(_args(db=None, poll_seconds=0.1, max_attempts=3)) == 0

    monkeypatch.setattr(cli.db, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(
        cli.db,
        "scheduler_config",
        lambda *_a, **_k: {
            "max_global_sessions": 20,
            "max_project_sessions": 10,
            "default_stuck_minutes": 12,
            "auto_reassign": 1,
            "preemption_enabled": 1,
        },
    )
    update_calls: list[dict] = []
    monkeypatch.setattr(cli.db, "update_scheduler_config", lambda *_a, **k: update_calls.append(k))
    assert cli.cmd_scheduler_config(
        _args(
            db=None,
            set=True,
            max_global=25,
            max_project=9,
            stuck_minutes=8,
            auto_reassign=False,
            preemption_enabled=True,
        )
    ) == 0
    assert update_calls


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
