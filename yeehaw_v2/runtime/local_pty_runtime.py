from __future__ import annotations

import fcntl
import os
import pty
import subprocess
import termios
import tty
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..models import RuntimeKind, SessionHandle, SessionSpec
from .base import RuntimeAdapter, RuntimeErrorBase


@dataclass(slots=True)
class _PtySession:
    proc: subprocess.Popen[bytes]
    master_fd: int


class LocalPtyRuntimeAdapter(RuntimeAdapter):
    def __init__(self) -> None:
        self._sessions: dict[str, _PtySession] = {}

    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.LOCAL_PTY

    def start_session(self, spec: SessionSpec) -> SessionHandle:
        master_fd, slave_fd = pty.openpty()
        session_id = f"localpty-{uuid.uuid4().hex[:10]}"

        env = os.environ.copy()
        env.update(spec.env)
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", spec.command],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(spec.project_root),
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            tty.setraw(master_fd, when=termios.TCSANOW)
        except termios.error:
            pass

        self._sessions[session_id] = _PtySession(proc=proc, master_fd=master_fd)
        return SessionHandle(
            runtime_kind=RuntimeKind.LOCAL_PTY,
            session_id=session_id,
            target=session_id,
            pid=proc.pid,
        )

    def _get(self, handle: SessionHandle) -> _PtySession:
        session = self._sessions.get(handle.session_id)
        if session is None:
            raise RuntimeErrorBase(f"local PTY session not found: {handle.session_id}")
        return session

    def send_user_input(self, handle: SessionHandle, text: str) -> None:
        session = self._get(handle)
        if not text.endswith("\n"):
            text += "\n"
        try:
            os.write(session.master_fd, text.encode("utf-8", errors="ignore"))
        except OSError as exc:
            raise RuntimeErrorBase(f"failed to write PTY input: {exc}") from exc

    def capture_output(self, handle: SessionHandle, lines: int = 400) -> str:
        session = self._get(handle)
        chunks: list[bytes] = []
        while True:
            try:
                data = os.read(session.master_fd, 8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        text = b"".join(chunks).decode("utf-8", errors="ignore")
        rows = text.splitlines()
        return "\n".join(rows[-max(20, lines) :])

    def is_session_alive(self, handle: SessionHandle) -> bool:
        session = self._sessions.get(handle.session_id)
        if session is None:
            return False
        return session.proc.poll() is None

    def terminate_session(self, handle: SessionHandle) -> None:
        session = self._sessions.pop(handle.session_id, None)
        if session is None:
            return
        if session.proc.poll() is None:
            session.proc.terminate()
            try:
                session.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                session.proc.kill()
        try:
            os.close(session.master_fd)
        except OSError:
            pass
