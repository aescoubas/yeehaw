"""Tests for signal protocol parsing and watcher behavior."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent

from yeehaw.signal.protocol import SignalHandler, SignalWatcher, read_signal


def test_read_signal_valid(tmp_path: Path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(
        json.dumps({"task_id": 7, "status": "done", "summary": "ok"}),
    )

    data = read_signal(signal_path)
    assert data is not None
    assert data["task_id"] == 7


def test_read_signal_invalid_returns_none(tmp_path: Path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text("{invalid")

    assert read_signal(signal_path, retries=2) is None


def test_read_signal_retries_for_partial_write(tmp_path: Path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text("{\"task_id\":")

    def writer() -> None:
        time.sleep(0.1)
        signal_path.write_text(
            json.dumps({"task_id": 12, "status": "done", "summary": "later"}),
        )

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        data = read_signal(signal_path, retries=4)
    finally:
        thread.join()

    assert data is not None
    assert data["task_id"] == 12


def test_signal_handler_debounce(tmp_path: Path) -> None:
    handler = SignalHandler(debounce_sec=0.05)
    signal_path = tmp_path / "task-1" / "signal.json"
    signal_path.parent.mkdir(parents=True)

    handler.on_created(FileCreatedEvent(str(signal_path)))
    assert handler.get_ready_signals() == []

    time.sleep(0.06)
    ready = handler.get_ready_signals()
    assert ready == [signal_path]


def test_signal_watcher_poll_signals(tmp_path: Path) -> None:
    root = tmp_path / "signals"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "signal.json").write_text('{}')
    (root / "b" / "signal.json").write_text('{}')

    watcher = SignalWatcher(root)
    found = watcher.poll_signals()

    assert sorted(path.parent.name for path in found) == ["a", "b"]
