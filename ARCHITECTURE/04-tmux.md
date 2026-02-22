# 04 — Tmux Session Management

## Purpose

Workers run in detached tmux sessions so the orchestrator can continue ticking while
operators optionally attach to live agent terminals.

## Session Identity

Session name format:

`yeehaw-task-<task_id>`

## Operations

`tmux.session` exposes:

- `ensure_session(session_name, working_dir)`
- `send_text(session_name, text)`
- `launch_agent(session_name, working_dir, command)` (ensure + send)
- `has_session(session_name)`
- `capture_pane(session_name)`
- `kill_session(session_name)`
- `attach_session(session_name)` (replaces current process)
- `pipe_output(session_name, log_path)` (streams pane output to file)

## Logging

After launch, orchestrator calls `pipe_output`:

- command: `tmux pipe-pane -o ... "cat >> <log_path>"`
- one file per task attempt:
  - `<runtime_root>/logs/task-<id>/attempt-XX-<agent>.log`

If log piping fails, task keeps running and an event/alert is emitted.

## Operator Interaction

- `yeehaw attach <task_id>` attaches to active session
- detach with `Ctrl+b`, then `d`
- `yeehaw logs <task_id> --follow` provides non-tmux live output view
