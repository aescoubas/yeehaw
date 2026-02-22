"""Tests for tmux session helper functions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import yeehaw.tmux.session as tmux


def test_tmux_commands_invoke_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args: list[str], **kwargs):
        calls.append((args, kwargs))
        stdout = "pane output" if "capture-pane" in args else ""
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.ensure_session("s1", "/tmp")
    tmux.send_text("s1", "echo hi")
    assert tmux.has_session("s1") is True
    assert tmux.capture_pane("s1") == "pane output"
    tmux.kill_session("s1")

    assert calls[0][0][:2] == ["tmux", "new-session"]
    assert calls[1][0][:2] == ["tmux", "send-keys"]
    assert calls[2][0][:2] == ["tmux", "has-session"]
    assert calls[3][0][:2] == ["tmux", "capture-pane"]
    assert calls[4][0][:2] == ["tmux", "kill-session"]


def test_has_session_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tmux.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert tmux.has_session("missing") is False


def test_attach_session_and_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    attached: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(tmux.os, "execvp", lambda prog, args: attached.append((prog, args)))

    tmux.attach_session("s1")
    assert attached == [("tmux", ["tmux", "attach-session", "-t", "s1"])]

    steps: list[tuple[str, str]] = []
    monkeypatch.setattr(tmux, "ensure_session", lambda session, wd: steps.append((session, wd)))
    monkeypatch.setattr(tmux, "send_text", lambda session, cmd: steps.append((session, cmd)))

    tmux.launch_agent("task-1", "/tmp/work", "run agent")
    assert steps == [("task-1", "/tmp/work"), ("task-1", "run agent")]
