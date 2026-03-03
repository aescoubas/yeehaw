"""Guarded auto-resolver for trivial git conflicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess

ALLOWED_CONFLICT_TYPES = frozenset({"content_conflict", "add_add_conflict"})
MAX_CONFLICT_FILE_COUNT = 6
SAFE_LOCKFILE_BASENAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "requirements.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
    }
)
PYTHON_IMPORT_SUFFIXES = frozenset({".py", ".pyi"})
IMPORT_LINE_PATTERN = re.compile(r"^(?:from\s+\S+\s+import\s+.+|import\s+.+)$")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class ConflictHunk:
    """One parsed conflict hunk with ours/theirs sections."""

    ours: tuple[str, ...]
    theirs: tuple[str, ...]


@dataclass(frozen=True)
class TrivialConflictResolution:
    """Outcome for one trivial conflict auto-resolution attempt."""

    attempted: bool
    resolved: bool
    reason: str
    conflict_class: str | None = None


class TrivialConflictAutoResolver:
    """Resolve narrowly-scoped low-risk conflicts using deterministic rules."""

    def __init__(self, worktree_path: Path) -> None:
        self.worktree_path = worktree_path

    def resolve(
        self,
        *,
        conflict_type: str,
        conflict_files: list[str],
    ) -> TrivialConflictResolution:
        """Attempt resolving known-safe conflict classes in place."""
        if conflict_type not in ALLOWED_CONFLICT_TYPES:
            return TrivialConflictResolution(
                attempted=False,
                resolved=False,
                reason=f"unsupported conflict type: {conflict_type}",
            )
        if not conflict_files:
            return TrivialConflictResolution(
                attempted=False,
                resolved=False,
                reason="no conflicted files were reported",
            )
        if len(conflict_files) > MAX_CONFLICT_FILE_COUNT:
            return TrivialConflictResolution(
                attempted=False,
                resolved=False,
                reason=f"too many conflicted files: {len(conflict_files)}",
            )

        conflict_classes: list[str] = []
        for conflict_file in conflict_files:
            if not self._is_safe_relative_path(conflict_file):
                return TrivialConflictResolution(
                    attempted=False,
                    resolved=False,
                    reason=f"unsafe conflicted path: {conflict_file}",
                )
            file_path = self.worktree_path / conflict_file
            conflict_class = self._classify_conflict_file(file_path)
            if conflict_class is None:
                return TrivialConflictResolution(
                    attempted=False,
                    resolved=False,
                    reason=f"unsupported conflict content in {conflict_file}",
                )
            conflict_classes.append(conflict_class)

        for conflict_file in conflict_files:
            checkout_result = subprocess.run(
                ["git", "checkout", "--theirs", "--", conflict_file],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
            )
            if checkout_result.returncode != 0:
                return TrivialConflictResolution(
                    attempted=True,
                    resolved=False,
                    reason=(
                        f"failed to checkout theirs for {conflict_file}: "
                        f"{self._git_error(checkout_result)}"
                    ),
                    conflict_class=self._collapsed_class(conflict_classes),
                )
            add_result = subprocess.run(
                ["git", "add", "--", conflict_file],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
            )
            if add_result.returncode != 0:
                return TrivialConflictResolution(
                    attempted=True,
                    resolved=False,
                    reason=f"failed to stage {conflict_file}: {self._git_error(add_result)}",
                    conflict_class=self._collapsed_class(conflict_classes),
                )

        return TrivialConflictResolution(
            attempted=True,
            resolved=True,
            reason="resolved with trivial conflict resolver",
            conflict_class=self._collapsed_class(conflict_classes),
        )

    @staticmethod
    def _collapsed_class(conflict_classes: list[str]) -> str:
        unique = sorted(set(conflict_classes))
        if len(unique) == 1:
            return unique[0]
        return "mixed_trivial_conflict"

    @staticmethod
    def _is_safe_relative_path(conflict_file: str) -> bool:
        path = PurePosixPath(conflict_file)
        if path.is_absolute():
            return False
        return all(part not in {"", ".", ".."} for part in path.parts)

    def _classify_conflict_file(self, file_path: Path) -> str | None:
        if not file_path.exists() or not file_path.is_file():
            return None
        if file_path.name in SAFE_LOCKFILE_BASENAMES:
            return "lockfile_regeneration"
        hunks = self._parse_conflict_hunks(file_path)
        if not hunks:
            return None
        if self._is_whitespace_only_conflict(hunks):
            return "whitespace_only"
        if file_path.suffix in PYTHON_IMPORT_SUFFIXES and self._is_import_order_only_conflict(hunks):
            return "import_order_only"
        return None

    @staticmethod
    def _parse_conflict_hunks(file_path: Path) -> tuple[ConflictHunk, ...]:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ()

        hunks: list[ConflictHunk] = []
        ours: list[str] = []
        theirs: list[str] = []
        state = "normal"
        for line in text.splitlines(keepends=True):
            if line.startswith("<<<<<<< "):
                if state != "normal":
                    return ()
                state = "ours"
                ours = []
                theirs = []
                continue
            if line.startswith("======="):
                if state != "ours":
                    return ()
                state = "theirs"
                continue
            if line.startswith(">>>>>>> "):
                if state != "theirs":
                    return ()
                hunks.append(ConflictHunk(ours=tuple(ours), theirs=tuple(theirs)))
                state = "normal"
                continue
            if state == "ours":
                ours.append(line)
            elif state == "theirs":
                theirs.append(line)

        if state != "normal":
            return ()
        return tuple(hunks)

    @staticmethod
    def _is_whitespace_only_conflict(hunks: tuple[ConflictHunk, ...]) -> bool:
        if not hunks:
            return False
        for hunk in hunks:
            ours_normalized = WHITESPACE_PATTERN.sub("", "".join(hunk.ours))
            theirs_normalized = WHITESPACE_PATTERN.sub("", "".join(hunk.theirs))
            if not ours_normalized and not theirs_normalized:
                continue
            if ours_normalized != theirs_normalized:
                return False
        return True

    @staticmethod
    def _is_import_order_only_conflict(hunks: tuple[ConflictHunk, ...]) -> bool:
        if not hunks:
            return False
        for hunk in hunks:
            ours_imports = TrivialConflictAutoResolver._normalized_import_lines(hunk.ours)
            theirs_imports = TrivialConflictAutoResolver._normalized_import_lines(hunk.theirs)
            if ours_imports is None or theirs_imports is None:
                return False
            if ours_imports != theirs_imports:
                return False
        return True

    @staticmethod
    def _normalized_import_lines(lines: tuple[str, ...]) -> tuple[str, ...] | None:
        normalized: list[str] = []
        for raw_line in lines:
            line = " ".join(raw_line.strip().split())
            if not line:
                continue
            if line.startswith("#"):
                return None
            if IMPORT_LINE_PATTERN.match(line) is None:
                return None
            normalized.append(line)
        if not normalized:
            return None
        normalized.sort()
        return tuple(normalized)

    @staticmethod
    def _git_error(result: subprocess.CompletedProcess[str]) -> str:
        detail = result.stderr.strip() or result.stdout.strip()
        return detail or "unknown git error"
