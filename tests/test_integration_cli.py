from __future__ import annotations

import subprocess
from pathlib import Path

from yeehaw import cli


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def test_cli_integration_project_and_roadmap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    db_path = tmp_path / "yeehaw.db"

    assert cli.main(["--db", str(db_path), "init-db"]) == 0
    assert cli.main(["--db", str(db_path), "project", "create", "--root", str(repo), "--name", "demo-repo"]) == 0
    assert cli.main(["--db", str(db_path), "project", "list"]) == 0

    roadmap = repo / "roadmap.md"
    roadmap.write_text(
        """## 2. Execution Phases

### Phase 1: Setup
**Objective:**
Do setup
""",
        encoding="utf-8",
    )
    assert cli.main(["--db", str(db_path), "roadmap", "validate", str(roadmap)]) == 0
    assert cli.main(["--db", str(db_path), "run", "status"]) == 0
