# 07 — Signal Protocol

## Purpose

Workers notify completion by writing a JSON file. They do not write to the DB
directly.

## Signal Path

`<runtime_root>/signals/task-<task_id>/signal.json`

The directory is created by orchestrator before launch and passed in task prompt.

## Signal Format

```json
{
  "task_id": 42,
  "status": "done",
  "summary": "Brief description",
  "artifacts": ["path1", "path2"],
  "timestamp": "2026-02-22T12:00:00Z"
}
```

Required fields for processing:

- `task_id`
- `status`

Supported statuses:

- `done`
- `failed`
- `blocked`

## Detection

`SignalWatcher` combines:

- watchdog recursive observer with debounce (`0.5s`)
- periodic filesystem polling fallback (`poll_signals()`)

Signal parsing retries partial writes up to 3 times with 200ms delay.

## Orchestrator Actions

On `done`:

1. Validate task worktree is clean (`git status --porcelain` must be empty).
2. Merge task branch into roadmap integration branch.
3. Mark task `done` on success.
4. If validation/merge fails, mark `failed` and apply retry policy.

On `failed`:

- mark task failed
- queue retry if attempts remain

On `blocked`:

- mark task blocked
- create warning alert

After any processed signal:

- kill tmux session
- remove task worktree
- check phase completion/advancement
