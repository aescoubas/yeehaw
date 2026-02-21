# 04 — Tmux Session Management

## Purpose

Each worker agent runs inside a **detached tmux session**. This allows:
- Background execution without blocking the CLI
- Live attachment for observation (`yeehaw attach`)
- Clean process isolation and cleanup
- Full scrollback capture for logging

## Session Naming

```
yeehaw-task-{task_id}
```

## Operations

### Ensure Session

```python
import subprocess

def ensure_session(session_name: str, working_dir: str) -> None:
    subprocess.run([
        "tmux", "new-session", "-d", "-s", session_name, "-c", working_dir,
    ], check=True, capture_output=True)
```

### Send Command

```python
def send_text(session_name: str, text: str) -> None:
    subprocess.run([
        "tmux", "send-keys", "-t", session_name, text, "Enter",
    ], check=True, capture_output=True)
```

### Check Liveness

```python
def has_session(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name], capture_output=True,
    )
    return result.returncode == 0
```

### Capture Pane

```python
def capture_pane(session_name: str) -> str:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"],
        capture_output=True, text=True,
    )
    return result.stdout
```

### Kill Session

```python
def kill_session(session_name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
```

## Agent Launch Pattern

```python
def launch_agent(session_name: str, working_dir: str, command: str) -> None:
    ensure_session(session_name, working_dir)
    send_text(session_name, command)
```

## User Interaction

`yeehaw attach <task-id>` resolves the session name and replaces the process:

```python
import os

def attach_session(session_name: str) -> None:
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])
```

The user detaches with `Ctrl+b, d` and the agent keeps running.
