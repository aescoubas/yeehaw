# 04 - Tmux Session Management

Agents run inside tmux sessions. Each task gets a fresh session.

## Session Naming

Format: `yeehaw-task-{task_id}`

## Operations

- `EnsureSession(name, dir)` - create session with working directory
- `SendText(name, text)` - send command to session (prompt as CLI arg)
- `HasSession(name)` - check if session exists (liveness check)
- `CapturePane(name)` - capture output (for debugging only)
- `KillSession(name)` - terminate session on completion/timeout

## Agent Launch

The primary method passes the prompt as a CLI argument:
```
claude --dangerously-skip-permissions -p "your task prompt"
```

This avoids timing issues with paste-buffer that plagued the Python version.
