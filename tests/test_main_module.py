"""Tests for package __main__ entry point."""

from __future__ import annotations

import runpy

import pytest


def test_python_m_yeehaw_calls_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"count": 0}

    def fake_main() -> None:
        called["count"] += 1

    import yeehaw.cli.main as cli_main

    monkeypatch.setattr(cli_main, "main", fake_main)

    runpy.run_module("yeehaw.__main__", run_name="__main__")

    assert called["count"] == 1
