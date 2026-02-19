# 10 - Edge Cases

## Agent Never Signals
- Timeout (configurable: 30/60/120 min) → kill tmux session → check for late signal → fail → retry with next agent
- Max attempts: 4 per task

## Agent Crashes (tmux dies)
- Detected via `tmux has-session` returning non-zero
- Check for late signal file (agent may have written it before crash)
- If no signal → fail → retry

## Verification Fails
- Re-queue task with failure output appended to prompt
- Up to 4 total attempts
- After max attempts → mark task failed, alert operator

## Invalid Roadmap from Master
- Validate structure (phases, tasks, numbering)
- Store as status "invalid" if validation fails
- Alert operator
- Re-prompt master up to 3 times

## Signal File Race Condition
- 500ms debounce after fsnotify CREATE/WRITE event
- 3 parse retries at 200ms intervals for incomplete JSON
- Final fallback: 30s polling interval

## Worktree Conflicts
- If branch already exists → append attempt number to branch name
- If worktree path exists → clean up stale worktree first

## SQLite Busy
- WAL mode + 5000ms busy timeout handles most contention
- Single-writer design prevents write conflicts
