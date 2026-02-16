from __future__ import annotations

import pytest

from yeehaw import tmux


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_ensure_tmux_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _name: None)
    with pytest.raises(tmux.TmuxError):
        tmux.ensure_tmux_available()

    monkeypatch.setattr(tmux.shutil, "which", lambda _name: "/usr/bin/tmux")
    tmux.ensure_tmux_available()


def test_run_tmux_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux, "ensure_tmux_available", lambda: None)

    monkeypatch.setattr(tmux.subprocess, "run", lambda *_a, **_k: _Proc(0, stdout="x", stderr=""))
    out = tmux._run_tmux(["list-sessions"])
    assert out.stdout == "x"

    monkeypatch.setattr(tmux.subprocess, "run", lambda *_a, **_k: _Proc(1, stderr="bad"))
    with pytest.raises(tmux.TmuxError, match="bad"):
        tmux._run_tmux(["list-sessions"])

    out2 = tmux._run_tmux(["list-sessions"], check=False)
    assert out2.stderr == "bad"


def test_has_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux, "_run_tmux", lambda *_a, **_k: tmux.TmuxResult("", ""))
    assert tmux.has_session("s") is True

    monkeypatch.setattr(tmux, "_run_tmux", lambda *_a, **_k: tmux.TmuxResult("", "err"))
    assert tmux.has_session("s") is False


def test_ensure_session_and_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(args: list[str], stdin=None, check=True):
        calls.append(("run", args))
        if args and args[0] == "list-windows":
            return tmux.TmuxResult("win1\n", "")
        return tmux.TmuxResult("", "")

    monkeypatch.setattr(tmux, "_run_tmux", fake_run)
    monkeypatch.setattr(tmux, "has_session", lambda _s: True)
    tmux.ensure_session("s", "/tmp")
    assert not any(c[1][0] == "new-session" for c in calls)

    calls.clear()
    monkeypatch.setattr(tmux, "has_session", lambda _s: False)
    tmux.ensure_session("s", "/tmp")
    assert any(c[1][0] == "new-session" for c in calls)

    tmux.ensure_window("s", "win1", "/tmp", "bash")
    assert any(c[1][0] == "kill-window" for c in calls)
    assert any(c[1][0] == "new-window" for c in calls)


def test_send_text_capture_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], stdin=None, check=True):
        calls.append(args)
        if args[0] == "capture-pane":
            return tmux.TmuxResult("pane", "")
        return tmux.TmuxResult("", "")

    monkeypatch.setattr(tmux, "_run_tmux", fake_run)
    tmux.send_text("s:0.0", "hello", press_enter=True)
    assert calls[0][0] == "load-buffer"
    assert calls[2][0] == "send-keys"

    calls.clear()
    tmux.send_text("s:0.0", "hello", press_enter=False)
    assert len(calls) == 2

    pane = tmux.capture_pane("s:0.0", lines=7)
    assert pane == "pane"

    monkeypatch.setattr(tmux, "ensure_tmux_available", lambda: None)
    monkeypatch.setattr(tmux.subprocess, "run", lambda *_a, **_k: _Proc(0))
    tmux.attach_session("s")

    monkeypatch.setattr(tmux.subprocess, "run", lambda *_a, **_k: _Proc(1))
    with pytest.raises(tmux.TmuxError):
        tmux.attach_session("s")
