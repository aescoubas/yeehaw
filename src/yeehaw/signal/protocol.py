"""Sentinel file protocol for agent completion signaling."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


def read_signal(signal_path: Path, retries: int = 3) -> dict | None:
    """Read and parse a signal.json file with retry for partial writes."""
    for attempt in range(retries):
        try:
            text = signal_path.read_text()
            data = json.loads(text)
            if "task_id" in data and "status" in data:
                return data
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        if attempt < retries - 1:
            time.sleep(0.2)
    return None


class SignalHandler(FileSystemEventHandler):
    """Track created/updated signal files and emit after debounce delay."""

    def __init__(self, debounce_sec: float = 0.5) -> None:
        self.debounce_sec = debounce_sec
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith("signal.json"):
            with self._lock:
                self._pending[event.src_path] = time.monotonic()

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith("signal.json"):
            with self._lock:
                self._pending[event.src_path] = time.monotonic()

    def get_ready_signals(self) -> list[Path]:
        """Return signals that have passed debounce window."""
        now = time.monotonic()
        ready: list[Path] = []
        with self._lock:
            expired = [
                path
                for path, timestamp in self._pending.items()
                if now - timestamp >= self.debounce_sec
            ]
            for path in expired:
                del self._pending[path]
                ready.append(Path(path))
        return ready


class SignalWatcher:
    """Watch signal directory tree with watchdog and polling fallback."""

    def __init__(self, signals_root: Path) -> None:
        self.signals_root = signals_root
        self.handler = SignalHandler()
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start recursive watcher for signal files."""
        self.signals_root.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(self.handler, str(self.signals_root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        """Stop watcher and join observer thread."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def get_ready_signals(self) -> list[Path]:
        """Return debounced ready signals observed by watchdog."""
        return self.handler.get_ready_signals()

    def poll_signals(self) -> list[Path]:
        """Fallback scan for signal files on disk."""
        found: list[Path] = []
        if self.signals_root.exists():
            for signal_file in self.signals_root.rglob("signal.json"):
                found.append(signal_file)
        return found
