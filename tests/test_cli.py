"""Tests for CLI parsing and selected handlers."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import yeehaw.cli.main as cli_main
from yeehaw.cli.project import handle_init, handle_project
from yeehaw.config.models import FEATURE_FLAG_NAMES
from yeehaw.runtime import runtime_config_path


def test_main_routes_project_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}
    runtime_root = tmp_path / "runtime-home"

    def fake_handle_project(args: Namespace, db_path: Path) -> None:
        called["args"] = args
        called["db_path"] = db_path

    monkeypatch.setenv("YEEHAW_HOME", str(runtime_root))
    monkeypatch.chdir(tmp_path)
    import yeehaw.cli.project as project_module

    monkeypatch.setattr(project_module, "handle_project", fake_handle_project)

    cli_main.main(["project", "list"])

    assert isinstance(called["args"], Namespace)
    args = called["args"]
    assert isinstance(args, Namespace)
    assert args.project_command == "list"
    assert called["db_path"] == runtime_root / "yeehaw.db"


def test_main_routes_status_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}
    runtime_root = tmp_path / "runtime-home"

    def fake_handle_status(args: Namespace, db_path: Path) -> None:
        called["args"] = args
        called["db_path"] = db_path

    monkeypatch.setenv("YEEHAW_HOME", str(runtime_root))
    monkeypatch.chdir(tmp_path)
    import yeehaw.cli.status as status_module

    monkeypatch.setattr(status_module, "handle_status", fake_handle_status)

    cli_main.main(["status", "--json"])

    args = called["args"]
    assert isinstance(args, Namespace)
    assert args.as_json is True
    assert called["db_path"] == runtime_root / "yeehaw.db"


def test_project_handlers_round_trip(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / ".yeehaw" / "yeehaw.db"

    handle_init(db_path)

    add_args = Namespace(project_command="add", name="proj-a", repo=str(tmp_path))
    handle_project(add_args, db_path)

    list_args = Namespace(project_command="list")
    handle_project(list_args, db_path)
    out = capsys.readouterr().out

    assert "Initialized yeehaw" in out
    assert "Project 'proj-a' created" in out
    assert "proj-a" in out

    remove_args = Namespace(project_command="remove", name="proj-a")
    handle_project(remove_args, db_path)
    out2 = capsys.readouterr().out
    assert "removed" in out2


def test_main_config_set_rejects_invalid_key(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["config", "set", "unknown_flag", "true"])

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    assert "unknown_flag" in err


def test_main_config_set_rejects_invalid_value(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["config", "set", "hooks", "yes"])

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    assert "'yes'" in err


def test_main_status_baseline_unchanged_with_all_feature_flags_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}
    runtime_root = tmp_path / "runtime-home"

    def fake_handle_status(args: Namespace, db_path: Path) -> None:
        called["args"] = args
        called["db_path"] = db_path

    monkeypatch.setenv("YEEHAW_HOME", str(runtime_root))
    monkeypatch.chdir(tmp_path)
    config_path = runtime_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"features": {name: False for name in FEATURE_FLAG_NAMES}}),
    )
    import yeehaw.cli.status as status_module

    monkeypatch.setattr(status_module, "handle_status", fake_handle_status)

    cli_main.main(["status", "--json"])

    args = called["args"]
    assert isinstance(args, Namespace)
    assert args.as_json is True
    assert called["db_path"] == runtime_root / "yeehaw.db"
