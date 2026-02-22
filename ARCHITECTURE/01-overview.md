# 01 — Architecture Overview

## System Purpose

Yeehaw is a roadmap-driven multi-agent orchestrator. It lets a planner/supervisor
agent create and refine a roadmap, then runs worker agents task-by-task in isolated
git worktrees.

## Runtime Model

- Language/runtime: Python 3.12+
- Persistence: SQLite (`sqlite3`)
- CLI: `argparse`
- Planner bridge: FastMCP server over stdio
- Worker execution: tmux sessions + filesystem signal protocol
- File watching: `watchdog` with polling fallback

Runtime state is shared under one runtime root:

- Default: `~/.yeehaw`
- Override: `YEEHAW_HOME`

Key paths under runtime root:

- `yeehaw.db`
- `signals/task-<id>/signal.json`
- `logs/task-<id>/attempt-XX-<agent>.log`
- `worktrees/<repo-key>/...`
- `orchestrator.pid`
- `workers.json`

## Major Components

| Component | Responsibility |
|---|---|
| `cli/` | User-facing commands (`init`, `project`, `roadmap`, `plan`, `run`, `status`, `logs`, etc.) |
| `store/` | Schema + transactional state updates |
| `roadmap/` | Markdown parser/validator + dependency extraction |
| `mcp/` | Planner/supervisor tools (roadmap preview/edit, pause/resume, status views) |
| `planner/` | Interactive planning session + single-shot roadmap generation |
| `orchestrator/` | Dispatch/monitor loop, retries, phase progression, branch merges |
| `agent/` | Agent profiles, launchers, worker runtime config |
| `git/` | Branch/worktree creation and cleanup |
| `tmux/` | Session lifecycle + log piping |
| `signal/` | `signal.json` watcher and parser |

## End-to-End Flow

1. `yeehaw init` initializes runtime DB.
2. `yeehaw project add` registers a repo root for a project.
3. Planner flow:
   - `yeehaw plan ...` for interactive conversation over MCP, or
   - `yeehaw roadmap generate ...` for one-shot generation.
4. `yeehaw roadmap approve` queues first-phase tasks and sets roadmap executing.
5. `yeehaw run` starts orchestrator tick loop.
6. Orchestrator dispatches queued tasks when:
   - global/per-project slots are available, and
   - all task dependencies are `done`.
7. Worker writes `signal.json` (`done`/`failed`/`blocked`).
8. On `done`, orchestrator validates clean worktree, merges task branch into roadmap
   integration branch, and completes task.
9. When a phase is fully `done`, phase verify command runs; success queues next phase.
10. `status`, `logs`, and `alerts` provide live operational visibility.

## Task Lifecycle

`pending -> queued -> in-progress -> done`

Other paths:

- `queued|in-progress -> paused -> queued` (via pause/resume)
- `in-progress -> failed` (worker failure/crash/timeout/merge/validation failure)
- `in-progress -> blocked` (worker reports external blocker)

Retries: failed tasks are re-queued until `max_attempts` is exhausted.
