# 10 — Edge Cases and Failure Handling

## Worker Crash (`session_lost`)

Condition:

- task is `in-progress` but tmux session no longer exists

Handling:

1. if `signal.json` exists, process signal normally
2. else mark task failed (`Tmux session lost`)
3. retry if attempts remain
4. cleanup worktree

## Timeout

Condition:

- elapsed time since `started_at` exceeds `task_timeout_min`

Handling:

1. capture pane snapshot when possible
2. kill session
3. mark failed with log/snapshot hints
4. retry if attempts remain
5. cleanup worktree

## `done` Signal with Dirty Worktree

Condition:

- worker reports `done` but `git status --porcelain` is non-empty

Handling:

1. reject completion
2. mark task failed (`uncommitted changes`)
3. retry policy applies

## Merge Failure on Completion

Condition:

- task branch cannot merge into roadmap integration branch

Handling:

1. abort merge in temporary merge worktree
2. mark failed with merge details
3. retry policy applies (worker will relaunch from refreshed base)

## Missing Integration Branch

Condition:

- roadmap has `integration_branch` value but branch does not exist

Handling:

- launch fails with explicit error and task is failed for retry/inspection

## Invalid/Partial Signal JSON

Handling:

- parser retries 3 times (200ms backoff)
- ignored until valid signal appears (watchdog + polling fallback)

## Paused Tasks

- paused tasks are never dispatched
- if pause is requested while in-progress, tmux session is terminated first
- resume requeues task

## Concurrent Orchestrator Processes

- startup checks `<runtime_root>/orchestrator.pid`
- live PID -> startup aborts with clear error
- stale PID -> replaced

## Log Pipe Failure

If `tmux pipe-pane` fails:

- task continues running
- warning event/alert is emitted
- operator can still attach via tmux
