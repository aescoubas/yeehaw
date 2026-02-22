# 09 — MCP Server

## Purpose

`src/yeehaw/mcp/server.py` exposes planning/supervision tools over FastMCP so
Claude/Codex/Gemini can manage Yeehaw state during roadmap discussions.

Transport: stdio (`mcp.run(transport="stdio")`).

## Tool Surface

Project tools:

- `create_project(name, repo_root)`
- `list_projects()`

Roadmap tools:

- `get_roadmap(project_name, color=True)`
- `preview_roadmap(markdown, color=True)`
- `create_roadmap(project_name, markdown)`
- `edit_roadmap(project_name, markdown)` (true in-place edit)
- `approve_roadmap(project_name)`

Task/status tools:

- `list_tasks(project_name=None, status=None)`
- `get_project_status(project_name)`
- `pause_task(task_id)`
- `resume_task(task_id)`
- `update_task(task_id, status=None, assigned_agent=None, reset_attempts=False)`

## Colorized Verbose Roadmap Preview

`preview_roadmap` and `get_roadmap` return a `preview` field rendered with ANSI
styles when `color=True`.

The formatter preserves verbose task body lines, including:

- metadata lines (`**Depends on:**`, etc.)
- checklist items (`- [ ] ...`, `- [x] ...`)

## Roadmap Persistence Behavior

`create_roadmap`:

1. parse + validate markdown
2. create roadmap/phases/tasks rows
3. apply dependency edges from task metadata
4. return structured counts + preview

`edit_roadmap`:

1. targets active roadmap for project
2. parse + validate new markdown
3. applies in-place sync with history safety checks
4. updates dependency edges
5. returns edit stats + preview

## Pause/Resume Semantics

- `pause_task` allowed from `pending`, `queued`, `in-progress`
- if in-progress and tmux exists, session is killed before status change
- paused tasks are not dispatched
- `resume_task` transitions `paused -> queued`

## `update_task` Limitations (Current Behavior)

`update_task` supports:

- status updates only for `done`, `blocked`, `failed`, `queued`
- optional `reset_attempts`
- `assigned_agent` update only when current task status is `pending`

It does not currently provide a direct `in-progress` transition.

## Planner Integration

`yeehaw plan` configures selected planner agent with this MCP server and provides a
prompt that encourages:

- interactive requirement clarification
- repeated preview during discussion
- final persistence via `create_roadmap` or `edit_roadmap`
