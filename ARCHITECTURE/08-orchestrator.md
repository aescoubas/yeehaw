# 08 — Orchestrator Engine

## Core Loop

The orchestrator is a single-threaded tick loop:

1. monitor active tasks
2. dispatch queued tasks

Tick interval is `scheduler_config.tick_interval_sec` (default 5s).

## Startup and Shutdown

- writes `<runtime_root>/orchestrator.pid` and refuses second active instance
- installs `SIGINT`/`SIGTERM` handlers that call `stop()`
- uses a stop event so `Ctrl+C` exits promptly without waiting a full tick
- always stops watcher and removes PID file on exit

## Monitor Active Tasks

For each in-progress task:

1. process ready or polled signal files
2. detect missing tmux session (`session_lost`)
3. enforce timeout (`task_timeout_min`)

Timeout/crash handling marks task failed and may requeue if attempts remain.

## Dispatch Queued Tasks

Dispatch happens only when:

- global active tasks < `max_global_tasks`
- project active tasks < `max_per_project`
- dependencies satisfied (`task_dependencies` blockers all `done`)

## Launch Sequence

For each launch:

1. resolve task repo root from project metadata
2. resolve agent profile + worker runtime config (`workers.json`)
3. ensure roadmap integration branch exists (`yeehaw/roadmap-<roadmap_id>`)
4. prepare/reset task branch + worktree from integration branch
5. create signal directory and clear stale `signal.json`
6. write prompt file and launcher script
7. mark task `in-progress` (`attempts += 1`)
8. launch tmux session and pipe output to attempt log

## Completion Sequence (`status="done"`)

1. ensure worktree has no uncommitted changes
2. rebase task branch onto integration branch (temporary rebase worktree)
3. merge task branch into integration branch (fast-forward preferred) via temporary merge worktree
4. mark task done if rebase/merge succeeds
5. on rebase/merge failure, mark failed and retry later from updated base

Task worktree is cleaned after processing.

## Retry Policy

On failure:

- if `attempts < max_attempts`: task is requeued
- else: emit error alert for exhausted retries

## Phase and Roadmap Progression

When all tasks in a phase are `done`:

1. run phase `verify_cmd` in repo root (or no-op if absent)
2. set phase status `completed` or `failed`
3. if completed, queue next phase tasks and mark next phase `executing`
4. if no next phase, mark roadmap `completed`
