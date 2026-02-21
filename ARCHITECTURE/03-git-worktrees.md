# 03 — Git Worktree Management

## Purpose

Each worker agent runs in an **isolated git worktree** branched from the
project's HEAD. This prevents agents from stepping on each other's files
and provides clean branch-per-task isolation.

## Directory Layout

```
repo-root/
├── .yeehaw/
│   ├── yeehaw.db
│   ├── worktrees/
│   │   ├── yeehaw-task-1-add-user-model/
│   │   ├── yeehaw-task-2-api-endpoints/
│   │   └── ...
│   └── signals/
│       ├── task-1/signal.json
│       └── task-2/signal.json
```

## Branch Naming

```
yeehaw/task-{number}-{sanitized-title}
```

- Title is lowercased, non-alphanumeric chars replaced with `-`
- Consecutive dashes collapsed, leading/trailing dashes stripped
- Truncated to 50 chars max

```python
import re

def branch_name(task_number: str, title: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    slug = slug[:50]
    return f"yeehaw/task-{task_number}-{slug}"
```

## Lifecycle

### 1. Prepare Worktree

```python
import subprocess
from pathlib import Path

def prepare_worktree(repo_root: Path, branch: str) -> Path:
    worktree_path = repo_root / ".yeehaw" / "worktrees" / branch.split("/")[-1]

    if worktree_path.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_root, capture_output=True,
        )

    subprocess.run(
        ["git", "branch", "-f", branch, "HEAD"],
        cwd=repo_root, check=True, capture_output=True,
    )

    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=repo_root, check=True, capture_output=True,
    )

    return worktree_path
```

### 2. Agent Works in Worktree

Agent makes commits to its branch. Each commit prefixed with task number:
`[task-1.1] Add user model`.

### 3. Cleanup Worktree

```python
def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_root, capture_output=True,
    )
    subprocess.run(["git", "worktree", "prune"], cwd=repo_root, capture_output=True)
```

## Error Handling

- If branch exists from a retry, force-update it to HEAD
- If worktree path is stale, force-remove before creating
- All git commands use `capture_output=True` to avoid polluting terminal
- On retry, append `-attempt-{n}` to branch name if conflicts persist
