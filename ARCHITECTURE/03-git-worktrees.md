# 03 - Git Worktree Management

Each task runs in an isolated git worktree branched from the project's HEAD.

## Branch Naming

Format: `yeehaw/task-{number}-{sanitized-title}`

Example: `yeehaw/task-1.1-add-user-model`

## Lifecycle

1. `PrepareWorktree()` - creates branch + worktree under `.yeehaw/worktrees/`
2. Agent works in worktree, makes commits
3. On task completion, harness verifies the branch
4. `CleanupWorktree()` - removes worktree, prunes

## Location

All worktrees live under `{repo_root}/.yeehaw/worktrees/{branch-name}`. This directory is gitignored.
