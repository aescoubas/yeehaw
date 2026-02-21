# 08 — Orchestrator Engine

## Design

Single-threaded tick-based loop. Each tick (default 5 seconds):

1. **Monitor active tasks** — check signals, tmux liveness, timeouts
2. **Dispatch queued tasks** — respect concurrency limits, launch workers

## Tick Loop

```python
class Orchestrator:
    def __init__(self, store: Store, repo_root: Path, config: dict):
        self.store = store
        self.repo_root = repo_root
        self.config = config
        self.signal_watcher = SignalWatcher(repo_root / ".yeehaw" / "signals")
        self.running = False

    def run(self, project_id: int | None = None) -> None:
        self.running = True
        self.signal_watcher.start()
        try:
            while self.running:
                self._tick(project_id)
                time.sleep(self.config["tick_interval_sec"])
        finally:
            self.signal_watcher.stop()

    def _tick(self, project_id: int | None) -> None:
        self._monitor_active(project_id)
        self._dispatch_queued(project_id)

    def stop(self) -> None:
        self.running = False
```

## Monitor Active Tasks

For each task with `status = "in-progress"`:

1. Check for signal file → process signal
2. Check tmux session alive → handle crash if dead
3. Check timeout → handle timeout if exceeded

## Handle Signal

- `"done"` → run verification command → mark done or fail
- `"failed"` → log failure → retry if attempts < max
- `"blocked"` → mark blocked → create alert

Always: kill tmux session, cleanup worktree.

## Dispatch Queued Tasks

1. Check global concurrency limit
2. For each queued task: check per-project limit
3. Select agent (explicit > project default > global default)
4. Create worktree + signal directory
5. Build prompt (include failure context on retries)
6. Update DB with assignment
7. Launch tmux session with agent command

## Phase Advancement

After all tasks in a phase complete:
1. Run phase verification command (if set)
2. Mark phase completed or failed
3. If completed, queue next phase's tasks
4. If all phases done, mark roadmap completed

## Concurrency Defaults

| Setting | Default |
|---------|---------|
| `max_global_tasks` | 5 |
| `max_per_project` | 3 |
| `tick_interval_sec` | 5 |
| `task_timeout_min` | 60 |

## PID File

Only one orchestrator per project. Uses `.yeehaw/orchestrator.pid`.
Checks on startup, verifies process alive, overwrites stale PIDs.

## Graceful Shutdown

`SIGINT`/`SIGTERM` → set `running = False` → current tick completes →
active tmux sessions continue → PID file cleaned up.
