from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from ..models import RuntimeKind, SessionHandle, SessionSpec
from .base import RuntimeAdapter, RuntimeErrorBase


def _safe_session_name(project_root: Path, title: str) -> str:
    project_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_root.name).strip("-") or "project"
    title_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-") or "session"
    suffix = uuid.uuid4().hex[:6]
    return f"yeehawv2-{project_slug}-{title_slug}-{suffix}"[:70]


class TmuxRuntimeAdapter(RuntimeAdapter):
    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.TMUX

    def _run_tmux(self, *args: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        if shutil.which("tmux") is None:
            raise RuntimeErrorBase("tmux is not installed or not found in PATH")
        proc = subprocess.run(
            ["tmux", *args],
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            stderr = proc.stderr.strip() or "unknown tmux error"
            raise RuntimeErrorBase(f"tmux {' '.join(args)} failed: {stderr}")
        return proc

    def start_session(self, spec: SessionSpec) -> SessionHandle:
        session = _safe_session_name(spec.project_root, spec.title)
        window = "agent"
        target = f"{session}:{window}.0"
        self._run_tmux("new-session", "-d", "-s", session, "-n", "control", "-c", str(spec.project_root), "bash")
        self._run_tmux("new-window", "-d", "-t", session, "-n", window, "-c", str(spec.project_root), spec.command)
        return SessionHandle(
            runtime_kind=RuntimeKind.TMUX,
            session_id=session,
            target=target,
            pid=None,
        )

    def send_user_input(self, handle: SessionHandle, text: str) -> None:
        if not text.strip():
            return
        self._run_tmux("load-buffer", "-", stdin=text)
        self._run_tmux("paste-buffer", "-d", "-t", handle.target)
        self._run_tmux("send-keys", "-t", handle.target, "Enter")

    def capture_output(self, handle: SessionHandle, lines: int = 400) -> str:
        proc = self._run_tmux("capture-pane", "-p", "-t", handle.target, "-S", f"-{max(10, lines)}")
        return proc.stdout

    def is_session_alive(self, handle: SessionHandle) -> bool:
        proc = self._run_tmux("has-session", "-t", handle.session_id, check=False)
        return proc.returncode == 0

    def terminate_session(self, handle: SessionHandle) -> None:
        self._run_tmux("kill-session", "-t", handle.session_id, check=False)
