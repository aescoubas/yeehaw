# 03 — Git Worktree Management

## Purpose

Each task runs on its own git branch + worktree so workers do not overwrite one
another and each task has isolated commit history.

## Branching Model

Task branch name:

`yeehaw/task-<task_number>-<sanitized-title>`

Sanitization rules:

- lowercase title
- replace non `[a-z0-9]` with `-`
- trim edge `-`
- truncate slug to 50 chars

Roadmap integration branch:

`yeehaw/roadmap-<roadmap_id>`

This branch is created on first dispatch for a roadmap and used as the base for all
task worktrees in that roadmap execution.

## Worktree Location

Worktrees live under runtime root, not inside project repo:

`<runtime_root>/worktrees/<repo-name>-<repo-hash>/<task-branch-tail>`

`repo-hash` is a short SHA1 of resolved repo path to avoid collisions.

## Prepare / Cleanup APIs

`prepare_worktree(repo_root, runtime_root, branch, base_ref="HEAD")`:

1. Computes worktree path under runtime root.
2. Removes stale worktree path if present.
3. Force-updates task branch to `base_ref`.
4. Adds git worktree for task branch.

`cleanup_worktree(repo_root, worktree_path)`:

1. Force removes worktree.
2. Runs `git worktree prune`.

## Orchestrator Usage

During task launch:

- base ref is roadmap integration branch (if present/created)
- branch/worktree path are stored on task row
- retries may reuse existing task branch name while resetting branch tip to base ref

During task completion:

- worktree is removed after signal handling
- task branch is merged into integration branch on successful `done`

## Branch Status in `yeehaw status`

Branch state is computed by ancestry between task branch and target base branch
(roadmap integration branch when set, otherwise `main`):

- `n/a`
- `ahead`
- `diverged`
- `merged`
