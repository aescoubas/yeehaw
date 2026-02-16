from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import db
from .git_repo import GitRepoError, GitRepoInfo, detect_repo


@dataclass(slots=True)
class ImportResult:
    created: int
    updated: int
    skipped: int
    failed: int
    details: list[str]


def discover_git_roots(roots: list[str | Path], max_depth: int = 5) -> list[Path]:
    found: set[Path] = set()

    for root in roots:
        base = Path(root).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            continue

        stack: list[tuple[Path, int]] = [(base, 0)]
        while stack:
            current, depth = stack.pop()
            if (current / ".git").exists():
                found.add(current)

            if depth >= max_depth:
                continue

            try:
                children = sorted((p for p in current.iterdir() if p.is_dir()), key=lambda p: p.name)
            except OSError:
                continue

            for child in children:
                if child.name in {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache"}:
                    continue
                stack.append((child, depth + 1))

    return sorted(found)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-") or "project"


def _name_from_remote(remote_url: str | None, fallback: str) -> str:
    if not remote_url:
        return fallback

    normalized = remote_url
    if normalized.endswith(".git"):
        normalized = normalized[:-4]

    if ":" in normalized and not normalized.startswith("http"):
        # git@github.com:owner/repo -> owner/repo
        after_colon = normalized.split(":", 1)[1]
        if "/" in after_colon:
            owner, repo = after_colon.split("/", 1)
            return f"{_slugify(owner)}-{_slugify(repo)}"

    parts = [p for p in normalized.split("/") if p]
    if len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
        return f"{_slugify(owner)}-{_slugify(repo)}"

    return fallback


def _choose_name(conn, preferred: str, root_path: str) -> str:
    by_root = conn.execute(
        "SELECT name FROM projects WHERE root_path = ?",
        (root_path,),
    ).fetchone()
    if by_root is not None:
        return str(by_root["name"])

    existing = conn.execute("SELECT root_path FROM projects WHERE name = ?", (preferred,)).fetchone()
    if existing is None:
        return preferred

    if str(existing["root_path"]) == root_path:
        return preferred

    i = 2
    while True:
        candidate = f"{preferred}-{i}"
        row = conn.execute("SELECT root_path FROM projects WHERE name = ?", (candidate,)).fetchone()
        if row is None or str(row["root_path"]) == root_path:
            return candidate
        i += 1


def import_projects(
    conn,
    roots: list[str | Path],
    max_depth: int = 5,
    default_guidelines: str = "",
    dry_run: bool = False,
) -> ImportResult:
    roots_found = discover_git_roots(roots, max_depth=max_depth)
    details: list[str] = []
    created = 0
    updated = 0
    skipped = 0
    failed = 0

    for repo_root in roots_found:
        try:
            info: GitRepoInfo = detect_repo(repo_root)
        except GitRepoError as exc:
            skipped += 1
            details.append(f"SKIP  {repo_root} ({exc})")
            continue

        fallback_name = _slugify(Path(info.root_path).name)
        preferred_name = _name_from_remote(info.remote_url, fallback_name)
        name = _choose_name(conn, preferred_name, info.root_path)

        existing = conn.execute("SELECT id FROM projects WHERE root_path = ?", (info.root_path,)).fetchone()
        if existing is not None:
            if dry_run:
                updated += 1
                details.append(f"UPDATE {name} <- {info.root_path}")
                continue

            row = conn.execute("SELECT guidelines FROM projects WHERE id = ?", (existing["id"],)).fetchone()
            guidelines = str(row["guidelines"] or "") if row else ""
            if not guidelines:
                guidelines = default_guidelines
            db.create_project(
                conn,
                name=name,
                root_path=info.root_path,
                guidelines=guidelines,
                git_remote_url=info.remote_url,
                default_branch=info.default_branch,
                head_sha=info.head_sha,
            )
            updated += 1
            details.append(f"UPDATE {name} <- {info.root_path}")
            continue

        if dry_run:
            created += 1
            details.append(f"CREATE {name} <- {info.root_path}")
            continue

        db.create_project(
            conn,
            name=name,
            root_path=info.root_path,
            guidelines=default_guidelines,
            git_remote_url=info.remote_url,
            default_branch=info.default_branch,
            head_sha=info.head_sha,
        )
        created += 1
        details.append(f"CREATE {name} <- {info.root_path}")

    if not roots_found:
        details.append("No git repositories found under provided roots.")

    return ImportResult(
        created=created,
        updated=updated,
        skipped=skipped,
        failed=failed,
        details=details,
    )
