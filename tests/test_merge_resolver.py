"""Unit tests for trivial merge conflict classification helpers."""

from __future__ import annotations

from yeehaw.orchestrator.merge_resolver import ConflictHunk, TrivialConflictAutoResolver


def test_whitespace_only_conflict_allows_empty_normalized_hunks() -> None:
    hunks = (
        ConflictHunk(ours=(" \n", "\t"), theirs=("\n", "   ")),
        ConflictHunk(ours=("a  \n",), theirs=("a\n",)),
    )

    assert TrivialConflictAutoResolver._is_whitespace_only_conflict(hunks) is True


def test_whitespace_only_conflict_rejects_real_content_delta() -> None:
    hunks = (
        ConflictHunk(ours=("hello\n",), theirs=("world\n",)),
    )

    assert TrivialConflictAutoResolver._is_whitespace_only_conflict(hunks) is False
