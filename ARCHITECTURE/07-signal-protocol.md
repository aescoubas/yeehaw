# 07 — Sentinel File Protocol

## Purpose

Workers signal task completion by writing a JSON file to a known directory.
This decouples workers from the database — they never need DB access.

## Signal Directory

```
.yeehaw/signals/task-{id}/signal.json
```

Created by the orchestrator before launching the agent. Agent receives the
full path in its task prompt.

## Signal File Format

```json
{
  "task_id": 42,
  "status": "done",
  "summary": "Implemented user model with migrations",
  "artifacts": ["src/models/user.py", "migrations/001_create_users.sql"],
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Status Values

| Status | Meaning | Orchestrator Action |
|--------|---------|-------------------|
| `"done"` | Completed successfully | Run verification → mark done |
| `"failed"` | Agent could not complete | Log failure, re-queue if attempts < max |
| `"blocked"` | External dependency needed | Mark blocked, create alert |

## Detection: watchdog

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class SignalHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback
        self._debounce = {}

    def on_created(self, event):
        if event.src_path.endswith("signal.json"):
            self._schedule(event.src_path)

    def on_modified(self, event):
        if event.src_path.endswith("signal.json"):
            self._schedule(event.src_path)

    def _schedule(self, path):
        self._debounce[path] = time.monotonic()  # 500ms debounce
```

## Fallback: Polling

30-second polling interval as fallback for edge cases where watchdog events
are missed.

## Parse Retries

```python
def read_signal(signal_path: Path, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            data = json.loads(signal_path.read_text())
            if "task_id" in data and "status" in data:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
        if attempt < retries - 1:
            time.sleep(0.2)
    return None
```

## Lifecycle

1. Orchestrator creates `.yeehaw/signals/task-{id}/`
2. Agent receives signal directory path in prompt
3. Agent writes `signal.json` when finished
4. watchdog detects creation/modification
5. 500ms debounce → orchestrator reads signal
6. Orchestrator processes result
7. Signal directory preserved for debugging
