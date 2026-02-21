# 10 — Edge Cases & Error Handling

## Agent Timeout

1. Task exceeded `task_timeout_min`
2. Kill tmux session
3. Check for late signal (agent may have finished before timeout)
4. Signal found → process normally
5. No signal → mark failed, log `"timeout"`, retry up to `max_attempts`

## Tmux Crash

1. `has_session()` returns False for active task
2. Check for signal file
3. Signal found → process normally
4. No signal → mark failed, log `"session_lost"`, retry

## Verification Failure

1. Signal says `"done"` but verification command fails
2. Capture pane output for context
3. Mark failed with verification output as `last_failure`
4. Re-queue with failure context in prompt
5. After `max_attempts`, create error alert

## Invalid Roadmap

1. Parser returns validation errors
2. Store roadmap with status `"invalid"`
3. Return errors to caller (MCP or CLI)
4. Planner agent can see errors and fix

## Signal File Race Conditions

1. Agent starts writing signal.json
2. watchdog fires on CREATE
3. Partial JSON → `json.JSONDecodeError`
4. Retry 3x at 200ms intervals
5. Still invalid → log warning, wait for next tick

## Worktree Conflicts

1. Branch exists from previous attempt
2. Force-update: `git branch -f {branch} HEAD`
3. Worktree path exists → `git worktree remove --force`
4. Persistent conflict → append `-attempt-{n}`

## SQLite Busy

1. WAL mode enables concurrent reads
2. `busy_timeout=5000` handles brief contention
3. Single-writer prevents deadlocks
4. CLI status commands are read-only

## Agent Spawn Failure

1. `tmux new-session` fails
2. Catch `subprocess.CalledProcessError`
3. Mark task failed, create error alert
4. Do NOT retry immediately (systemic issue)

## Concurrent Orchestrator Instances

1. PID file at `.yeehaw/orchestrator.pid`
2. Check on startup, verify process alive
3. Stale PID → overwrite and proceed
4. Active PID → abort with error

## Graceful Shutdown

1. `SIGINT`/`SIGTERM` caught
2. `running = False`
3. Current tick completes
4. Active tmux sessions continue
5. Next `yeehaw run` reconnects
6. PID file cleaned up
