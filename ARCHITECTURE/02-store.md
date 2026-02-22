# 02 — SQLite Store

## Design

- SQLite database at `<runtime_root>/yeehaw.db`
- WAL mode + busy timeout for concurrent readers
- Updates come from orchestrator, CLI commands, and MCP server tool calls
- ISO timestamps stored as text
- Idempotent schema initialization with targeted migrations

## Core Tables

### `projects`

Tracks registered repos.

- `name` (unique)
- `repo_root` (absolute path for task execution and verification)

### `roadmaps`

Stores roadmap markdown and lifecycle state.

- `raw_md`
- `status`: `draft | approved | executing | completed | invalid`
- `integration_branch`: roadmap-level branch where completed task branches are merged

### `roadmap_phases`

- `phase_number`, `title`, optional `verify_cmd`
- `status`: `pending | executing | completed | failed`

### `tasks`

- `task_number`, `title`, `description`
- `status`: `pending | queued | paused | in-progress | done | failed | blocked`
- assignment/runtime metadata: `assigned_agent`, `branch_name`, `worktree_path`, `signal_dir`
- retry metadata: `attempts`, `max_attempts`, `last_failure`

### `task_dependencies`

Dependency edges between tasks:

- `blocked_task_id`
- `blocker_task_id`
- uniqueness on edge pair

This powers dispatch gating (`queued` tasks launch only when blockers are `done`).

### `git_worktrees`, `events`, `alerts`, `scheduler_config`

- `git_worktrees`: bookkeeping rows
- `events`: operational audit trail
- `alerts`: actionable warnings/errors with ack flag
- `scheduler_config` singleton:
  - `max_global_tasks`
  - `max_per_project`
  - `tick_interval_sec`
  - `task_timeout_min`

## Store Operations (Key APIs)

Projects:

- `create_project(name, repo_root)`
- `get_project(name)`
- `list_projects()`
- `delete_project(name)`

Roadmaps:

- `create_roadmap(project_id, raw_md)` (invalidates prior active roadmaps for project)
- `get_active_roadmap(project_id)`
- `update_roadmap_status(roadmap_id, status)`
- `set_roadmap_integration_branch(roadmap_id, branch_name)`
- `edit_roadmap_in_place(...)` (structural/immutability checks + task sync)
- `apply_roadmap_dependencies(roadmap_id, roadmap)`

Tasks:

- `assign_task(...)` sets `in-progress`, increments attempts, stores branch/worktree/signal
- `queue_task(task_id)`
- `pause_task(task_id)` and `resume_task(task_id)`
- `complete_task(task_id, status)` for terminal `done`/`blocked`
- `fail_task(task_id, failure_msg)`
- `reset_task_attempts(task_id)`
- `are_task_dependencies_satisfied(task_id)`

Operational data:

- `log_event(...)`, `list_events(...)`
- `create_alert(...)`, `list_alerts(...)`, `ack_alert(...)`
- `get_scheduler_config()`, `update_scheduler_config(...)`

## Dependency Handling

Dependency refs are parsed from task description metadata:

- `**Depends on:** 1.1, 1.2` (also accepts `P0.1` format)

When persisting roadmap dependencies:

1. Map task numbers to DB task IDs.
2. Validate unknown/self references.
3. Detect cycles.
4. Replace dependency edges for that roadmap atomically.

## In-Place Roadmap Edit Rules

`edit_roadmap_in_place` enforces history safety:

- Editable task statuses: `pending`, `queued`
- Locked task statuses: `paused`, `in-progress`, `done`, `failed`, `blocked`
- Non-draft roadmaps cannot add/remove/reorder phases
- Completed/failed phases cannot be structurally changed

It synchronizes tasks using task-number and title/description fingerprint matching so
renumbering and content updates remain stable for editable tasks.

## Migrations

`init_db()` applies:

- legacy schema migration (`root_path -> repo_root`, old status mapping)
- `roadmaps.integration_branch` backfill when missing
- tasks-table rebuild to include `paused` status when upgrading older DBs
