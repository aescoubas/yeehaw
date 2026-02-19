from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class TmuxError(RuntimeError):
    """Raised when a tmux command fails."""


@dataclass(slots=True)
class TmuxResult:
    stdout: str
    stderr: str


def ensure_tmux_available() -> None:
    if shutil.which("tmux") is None:
        raise TmuxError("tmux is not installed or not found in PATH")


def _run_tmux(args: list[str], stdin: str | None = None, check: bool = True) -> TmuxResult:
    ensure_tmux_available()
    proc = subprocess.run(
        ["tmux", *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip() or "unknown tmux error"
        raise TmuxError(f"tmux {' '.join(args)} failed: {stderr}")
    return TmuxResult(stdout=proc.stdout, stderr=proc.stderr)


def has_session(session: str) -> bool:
    result = _run_tmux(["has-session", "-t", session], check=False)
    return result.stderr == "" and result.stdout == ""


def ensure_session(session: str, cwd: str) -> None:
    if has_session(session):
        return
    _run_tmux(["new-session", "-d", "-s", session, "-n", "control", "-c", cwd, "bash"])


def list_windows(session: str) -> list[str]:
    result = _run_tmux(["list-windows", "-t", session, "-F", "#{window_name}"])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def ensure_window(session: str, window: str, cwd: str, command: str) -> None:
    existing = set(list_windows(session))
    if window in existing:
        _run_tmux(["kill-window", "-t", f"{session}:{window}"])
    _run_tmux(["new-window", "-d", "-t", session, "-n", window, "-c", cwd, command])


def send_text(target: str, text: str, press_enter: bool = True) -> None:
    _run_tmux(["load-buffer", "-"], stdin=text)
    _run_tmux(["paste-buffer", "-d", "-t", target])
    if press_enter:
        _run_tmux(["send-keys", "-t", target, "Enter"])


def send_keys(target: str, *keys: str) -> None:
    if not keys:
        return
    _run_tmux(["send-keys", "-t", target, *keys])


def capture_pane(target: str, lines: int = 1200) -> str:
    result = _run_tmux(["capture-pane", "-p", "-t", target, "-S", f"-{lines}"])
    return result.stdout


def attach_session(session: str) -> None:
    ensure_tmux_available()
    if os.environ.get("TMUX"):
        proc = subprocess.run(["tmux", "switch-client", "-t", session], check=False)
        if proc.returncode != 0:
            raise TmuxError(f"tmux switch-client failed for {session}")
        return
    proc = subprocess.run(["tmux", "attach-session", "-t", session], check=False)
    if proc.returncode != 0:
        raise TmuxError(f"tmux attach-session failed for {session}")


def kill_session(session: str) -> None:
    _run_tmux(["kill-session", "-t", session], check=False)
