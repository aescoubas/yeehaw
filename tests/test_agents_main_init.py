from __future__ import annotations

import runpy

import pytest

from yeehaw import __version__
from yeehaw import agents


def test_version_exposed() -> None:
    assert __version__ == "0.1.0"


def test_resolve_command_override() -> None:
    cmd, warmup = agents.resolve_command("codex", "echo 'hello world'")
    assert cmd == "echo 'hello world'"
    assert warmup == 2.0


def test_resolve_command_profile_and_fallback() -> None:
    cmd, warmup = agents.resolve_command("CoDeX")
    assert cmd == "codex"
    assert warmup == 3.0

    custom_cmd, custom_warmup = agents.resolve_command("my custom agent")
    assert custom_cmd == "my custom agent"
    assert custom_warmup == 2.0


def test_main_module_exits_with_cli_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import yeehaw.cli

    monkeypatch.setattr(yeehaw.cli, "main", lambda: 7)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("yeehaw.__main__", run_name="__main__")
    assert exc.value.code == 7
