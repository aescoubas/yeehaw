# 08 - Orchestrator

The orchestrator is a single-threaded tick loop that manages the task lifecycle.

## Tick Loop

Every tick (default 5s):

1. **monitorActive()** - For each running task:
   - Check signal file (via fsnotify channel)
   - Check tmux session alive
   - Check timeout
   - On signal received → verify → complete or fail

2. **dispatchQueued()** - For each queued task (respecting limits):
   - Choose agent (round-robin or configured)
   - Create git worktree
   - Create signal directory
   - Build task prompt
   - Launch tmux session with agent CLI
   - Update task status to dispatched

## Concurrency Limits

- Global: max 5 concurrent tasks (default)
- Per-project: max 3 concurrent tasks (default)
- Configurable via `yeehaw scheduler config`

## Completion Flow

1. Signal file detected with status "done"
2. Run phase verification command (if any)
3. If verification passes → mark task done, log event
4. If verification fails → increment attempt, re-queue (up to max_attempts)
5. Clean up worktree after successful merge or final failure
