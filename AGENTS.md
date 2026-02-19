# Yeehaw Agent Instructions

You are a coding agent working inside the **yeehaw** orchestration harness. Follow these instructions precisely.

## Your Environment

- You are running in a **git worktree** branched from the project's main branch.
- Your working directory is the root of this worktree.
- A `.yeehaw/` directory exists in the repo root with runtime metadata.
- You have a **signal directory** at the path provided in your task prompt.

## Task Protocol

1. **Read the task description** carefully. It contains everything you need.
2. **Do the work.** Make commits to your branch as you go.
3. **When finished**, write a signal file to indicate completion.

## Signal File Protocol

When your task is complete (success or failure), create the file `signal.json` in your signal directory:

```json
{
  "task_id": <your-task-id>,
  "status": "done",
  "summary": "Brief description of what you did",
  "artifacts": ["list", "of", "key", "files", "changed"],
  "timestamp": "2024-01-01T00:00:00Z"
}
```

Status values:
- `"done"` - Task completed successfully
- `"failed"` - Task could not be completed
- `"blocked"` - Task is blocked by an external dependency

**IMPORTANT:** The signal file is how the harness knows you are finished. Without it, your task will eventually time out.

## Commit Guidelines

- Make small, focused commits with clear messages.
- Prefix commit messages with the task number: `[task-1.1] Add user model`.
- Do NOT push to remote; the harness handles merging.

## Constraints

- Do NOT modify files outside your worktree.
- Do NOT run long-lived servers or daemons.
- Do NOT install system-level packages.
- Keep your work focused on the specific task assigned.
- If you encounter an issue that blocks you, write a `"blocked"` signal.

## Verification

After completing your work, if a verification command was provided in the task prompt, run it and include the result in your signal summary.

## Communication

- Your only communication channel with the harness is the signal file.
- Do NOT attempt to read or write to stdin/stdout for harness communication.
- All task context is provided in your initial prompt.
