"""Tests for project memory pack loading and validation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from yeehaw.context.loader import load_project_memory_pack
from yeehaw.context.models import MEMORY_PACK_MAX_BYTES, ProjectMemoryPack


def _valid_memory_pack(extra_line: str = "") -> str:
    return (
        "# Demo Project Memory Pack\n"
        "\n"
        "## Conventions\n"
        "- Keep public APIs explicit.\n"
        "\n"
        "## Architecture Constraints\n"
        "- Avoid global mutable state.\n"
        "\n"
        "## Coding Standards\n"
        f"- Type hint all function signatures.{extra_line}\n"
    )


def test_load_project_memory_pack_defaults_when_file_missing(tmp_path: Path) -> None:
    memory_pack = load_project_memory_pack("demo-project", runtime_root=tmp_path)

    assert memory_pack == ProjectMemoryPack(project_name="demo-project")
    assert memory_pack.is_empty is True


def test_load_project_memory_pack_uses_runtime_root_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("YEEHAW_HOME", str(tmp_path))
    memory_pack_path = tmp_path / "context" / "projects" / "demo-project.md"
    memory_pack_path.parent.mkdir(parents=True, exist_ok=True)
    memory_pack_path.write_text(_valid_memory_pack())

    memory_pack = load_project_memory_pack("demo-project")

    assert memory_pack.source_path == memory_pack_path
    assert "## Conventions" in memory_pack.markdown
    assert "## Architecture Constraints" in memory_pack.markdown
    assert "## Coding Standards" in memory_pack.markdown


def test_load_project_memory_pack_resolution_order_is_deterministic(tmp_path: Path) -> None:
    runtime_scoped_path = tmp_path / "context" / "demo-project.md"
    project_scoped_path = tmp_path / "context" / "projects" / "demo-project.md"
    runtime_scoped_path.parent.mkdir(parents=True, exist_ok=True)
    project_scoped_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_scoped_path.write_text(_valid_memory_pack(extra_line=" runtime copy"))
    project_scoped_path.write_text(_valid_memory_pack(extra_line=" project copy"))

    memory_pack = load_project_memory_pack("demo-project", runtime_root=tmp_path)

    assert memory_pack.source_path == project_scoped_path
    assert memory_pack.markdown.endswith("project copy")


def test_load_project_memory_pack_uses_slugified_project_candidate(tmp_path: Path) -> None:
    memory_pack_path = tmp_path / "context" / "projects" / "demo-project.md"
    memory_pack_path.parent.mkdir(parents=True, exist_ok=True)
    memory_pack_path.write_text(_valid_memory_pack())

    memory_pack = load_project_memory_pack("Demo Project", runtime_root=tmp_path)

    assert memory_pack.source_path == memory_pack_path
    assert memory_pack.project_name == "Demo Project"


def test_load_project_memory_pack_rejects_oversized_payload(tmp_path: Path) -> None:
    memory_pack_path = tmp_path / "context" / "projects" / "demo-project.md"
    memory_pack_path.parent.mkdir(parents=True, exist_ok=True)
    memory_pack_path.write_text("x" * (MEMORY_PACK_MAX_BYTES + 1))

    with pytest.raises(ValueError, match=re.escape(str(memory_pack_path))) as exc:
        load_project_memory_pack("demo-project", runtime_root=tmp_path)

    assert "file exceeds max size" in str(exc.value)


def test_load_project_memory_pack_rejects_missing_required_sections(tmp_path: Path) -> None:
    memory_pack_path = tmp_path / "context" / "projects" / "demo-project.md"
    memory_pack_path.parent.mkdir(parents=True, exist_ok=True)
    memory_pack_path.write_text(
        "# Demo Project Memory Pack\n"
        "\n"
        "## Conventions\n"
        "- Keep functions small.\n"
    )

    with pytest.raises(ValueError, match=re.escape(str(memory_pack_path))) as exc:
        load_project_memory_pack("demo-project", runtime_root=tmp_path)

    assert "missing required sections" in str(exc.value)
