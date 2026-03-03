"""Tmux session management for worker agent isolation."""

from __future__ import annotations

import os
import shlex
import subprocess


def ensure_session(session_name: str, working_dir: str) -> None:
    """Create a detached tmux session."""
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", working_dir],
        check=True,
        capture_output=True,
    )


def send_text(session_name: str, text: str) -> None:
    """Send text to tmux session followed by Enter."""
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, text, "Enter"],
        check=True,
        capture_output=True,
    )


def has_session(session_name: str) -> bool:
    """Return True if tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def capture_pane(session_name: str) -> str:
    """Capture pane scrollback for debugging."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def kill_session(session_name: str) -> None:
    """Kill tmux session; ignore errors when already absent."""
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
    )


def attach_session(session_name: str) -> None:
    """Attach to tmux session, replacing current process."""
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def launch_agent(session_name: str, working_dir: str, command: str) -> None:
    """Create session and dispatch command."""
    ensure_session(session_name, working_dir)
    send_text(session_name, command)


def pipe_output(session_name: str, log_path: str) -> None:
    """Pipe tmux pane output to a log file."""
    quoted_log_path = shlex.quote(log_path)
    subprocess.run(
        ["tmux", "pipe-pane", "-o", "-t", session_name, f"cat >> {quoted_log_path}"],
        check=True,
        capture_output=True,
    )
