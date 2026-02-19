# 07 - Sentinel File Protocol

Agents signal completion by writing a JSON file. This replaces fragile pane-scanning.

## Signal File

Path: `{signal_dir}/signal.json`

```json
{
  "task_id": 42,
  "status": "done",
  "summary": "Implemented user authentication with JWT",
  "artifacts": ["internal/auth/auth.go", "internal/auth/auth_test.go"],
  "timestamp": "2024-01-15T14:30:00Z"
}
```

## Status Values

- `done` - task completed successfully
- `failed` - task could not be completed
- `blocked` - task blocked by external dependency

## Detection

- `fsnotify` watches the signal directory for file creation/write events
- 500ms debounce after event detection (agent may still be writing)
- 3 parse retries at 200ms intervals (handle partial writes)
- Fallback: periodic polling every 30s as safety net

## Signal Directory

Located at `.yeehaw/signals/task-{id}/` in the repo root. Created by the harness before agent launch.
