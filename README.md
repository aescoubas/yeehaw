# Yeehaw

Yeehaw is a multi-agent coding orchestrator for running roadmap tasks with agent CLIs (`claude`, `codex`, `gemini`) in isolated git worktrees.

It provides:
- Project registration and roadmap management.
- An orchestrator that dispatches tasks to worker agents.
- Interactive planning via an MCP-backed supervisor session.
- Task monitoring (`status`, `logs`, `attach`, `alerts`).
- MCP tools to inspect/update roadmaps and pause/resume tasks.

## Requirements

- Python 3.12+
- `uv`
- `git`
- `tmux`
- At least one agent CLI installed and authenticated:
  - `claude`
  - `codex`
  - `gemini`

Install project deps:

```bash
uv sync
```

Run commands with:

```bash
uv run yeehaw ...
```

Or install the CLI entrypoint once and run `yeehaw` directly:

```bash
uv tool install -e .
yeehaw --help
```

## Important Path Behavior

Yeehaw runtime state is **global by default**.

- Runtime root defaults to: `~/.yeehaw`
- Database path defaults to: `~/.yeehaw/yeehaw.db`
- Logs, signals, PID, worker config, and worktrees are all under `~/.yeehaw`

You can override the runtime root with `YEEHAW_HOME`:

```bash
export YEEHAW_HOME=/custom/path/to/yeehaw-runtime
```

## Quick Start

1. Initialize Yeehaw runtime state:

```bash
uv run yeehaw init
```

2. Add a project and point it to the target git repo:

```bash
uv run yeehaw project add demo --repo /absolute/path/to/your/repo
```

3. Create a roadmap:

```bash
uv run yeehaw roadmap create demo-roadmap.md --project demo
```

4. Approve roadmap (queues first phase tasks):

```bash
uv run yeehaw roadmap approve --project demo
```

5. Start orchestrator:

```bash
uv run yeehaw run --project demo --agent codex
```

6. Monitor:

```bash
uv run yeehaw status --project demo
uv run yeehaw logs 11 --follow
```

## Command Reference

Top-level:

```bash
uv run yeehaw {init,project,roadmap,plan,run,daemon,status,attach,stop,logs,scheduler,alerts,workers}
```

### `init`

Initialize runtime directory and database.

```bash
uv run yeehaw init
```

### `project`

- Add project:

```bash
uv run yeehaw project add <name> --repo <repo_root>
```

- List projects:

```bash
uv run yeehaw project list
```

- Remove project:

```bash
uv run yeehaw project remove <name>
```

### `roadmap`

- Create from markdown:

```bash
uv run yeehaw roadmap create <file.md> --project <name>
```

- Show active roadmap summary:

```bash
uv run yeehaw roadmap show --project <name>
```

- Approve active draft roadmap:

```bash
uv run yeehaw roadmap approve --project <name>
```

- Clear active roadmap(s) for project (destructive):

```bash
uv run yeehaw roadmap clear --project <name>
```

- Generate roadmap from natural text (single-shot planner run):

```bash
uv run yeehaw roadmap generate --project <name> --prompt "Build v1 CLI with tests"
uv run yeehaw roadmap generate --project <name> --file briefing.md --agent codex --approve
```

### `plan`

Start interactive planning session (supervisor style) with Yeehaw MCP tools attached:

```bash
uv run yeehaw plan --project <name> --agent codex
uv run yeehaw plan briefing.md --project <name> --agent claude
```

### `run`

Start orchestrator loop:

```bash
uv run yeehaw run
uv run yeehaw run --project demo
uv run yeehaw run --project demo --agent gemini
```

`--agent` sets default worker agent for unassigned tasks.

### `daemon`

Manage a persistent orchestrator via user-level systemd service.

Install + enable + start:

```bash
uv run yeehaw daemon install --agent codex
```

Check status / logs:

```bash
uv run yeehaw daemon status
uv run yeehaw daemon logs --lines 200
uv run yeehaw daemon logs --follow
```

Control lifecycle:

```bash
uv run yeehaw daemon start
uv run yeehaw daemon stop
uv run yeehaw daemon restart
uv run yeehaw daemon uninstall
```

Notes:
- Service name defaults to `yeehaw-orchestrator.service` (override with `--service-name`).
- `daemon install` writes a unit file under `~/.config/systemd/user/`.
- Unit uses your Yeehaw runtime root (`YEEHAW_HOME` / `~/.yeehaw`) and runs `python -m yeehaw run`.

### `status`

```bash
uv run yeehaw status
uv run yeehaw status --project demo
uv run yeehaw status --json
```

Rows are sorted by DB task `ID` (ascending).

Columns:
- `ID`: DB task id.
- `Task`: logical task number (`1.1`, `2.3`, ...).
- `Title`: truncated to fixed width.
- `Status`: task status.
- `Agent`: assigned agent.
- `Branch`: git ancestry state (against roadmap integration branch when present, otherwise `main`):
  - `n/a`: no branch or branch missing
  - `ahead`: branch has commits not in target base branch
  - `diverged`: both branch and target base branch moved
  - `merged`: no branch-only commits remain
- `Attempts`: attempt counter shown as `<attempts>/<max_attempts>`.
- `Tokens`: parsed token usage from latest in-progress log (if detectable), else `n/a`.
- `Merge`: latest merge/rebase diagnostic summary (non-success states only), else `n/a`.

### `attach`

Attach terminal to worker tmux session:

```bash
uv run yeehaw attach <task_id>
```

Detach from tmux with `Ctrl+b`, then `d`.

### `stop`

Stop running task(s), kill tmux session, clean worktree, and mark task failed:

```bash
uv run yeehaw stop <task_id>
uv run yeehaw stop --all
```

### `logs`

Show task attempt logs:

```bash
uv run yeehaw logs <task_id>
uv run yeehaw logs <task_id> --attempt 2 --tail 400
uv run yeehaw logs <task_id> --follow
uv run yeehaw logs <task_id> --merge-history
uv run yeehaw logs <task_id> --merge-history --history-limit 10
```

### `scheduler`

- Show scheduler config:

```bash
uv run yeehaw scheduler show
```

- Update scheduler config:

```bash
uv run yeehaw scheduler config --max-global 5 --max-project 5 --tick 5 --timeout 60
```

Defaults:
- `max_global_tasks=5`
- `max_per_project=5`
- `tick_interval_sec=5`
- `task_timeout_min=60`

### `alerts`

```bash
uv run yeehaw alerts
uv run yeehaw alerts --ack <alert_id>
```

### `workers`

Show resolved worker launch configuration:

```bash
uv run yeehaw workers show
```

## Roadmap Format

Roadmaps are markdown with strict structure.

Supported:
- Phase numbering can start at `0` or `1`, then must be sequential.
- Task headings can be:
  - `### Task N.M: ...`
  - `### P0.1: ...`
- `**Depends on:**` metadata is parsed and enforced by scheduler.
- Optional trailing checklist marker in title is normalized (for example `[x]` is stripped from title text).

Verbose format example:

```md
# Roadmap: demo

## Phase 0: Foundation
**Verify:** `pytest -q`

### Task 0.1: Define contract
**Depends on:** none
**Repo:** demo
**Files:**
- `docs/contract.md` - Define output schema and CLI behavior
**Description:**
Write the baseline contract shared by all scripts.
**Done when:**
- [ ] Schema fields documented
- [ ] Exit code policy documented
```

## Task Lifecycle and Orchestration

Task statuses:
- `pending`: task exists but not yet queued.
- `queued`: ready to dispatch, waiting for dependencies and/or available scheduler capacity.
- `paused`: intentionally paused; not dispatched until resumed.
- `in-progress`: worker currently running.
- `done`: finished successfully.
- `failed`: failed attempt or manual stop.
- `blocked`: worker reported external blocker.

Phase flow:
- On roadmap approval, first phase tasks are queued.
- Queued tasks only dispatch when all declared dependencies are `done`.
- When all tasks in a phase are `done`, phase verify command runs.
- If verify passes, next phase is queued.
- If no next phase, roadmap becomes `completed`.

Failure/retry behavior:
- Crash, timeout, dirty `done` signal, rebase/merge failure, or worker `failed` signal marks task failed.
- Task is re-queued until `max_attempts` is exhausted (default `4`).
- Exhaustion emits an alert.

## Extension Model and Compatibility

Yeehaw's extension model is intentionally optional and keeps the orchestration core
lean. Extensions consume lifecycle hook events to add telemetry or notifications;
they do not replace core scheduling or task-state authority.

- Default behavior is unchanged when no extensions are configured.
- Core operation does not require external services (no mandatory broker, webhook,
  or hosted control plane).
- Hook payload and response contracts are versioned (`schema_version`) and documented
  in `ARCHITECTURE/12-extensions.md`.
- Compatibility guarantees for a major schema version:
  - existing event names are stable,
  - required fields keep the same meaning and type,
  - new fields/events are additive,
  - breaking changes require a new major schema version.
- Extension timeout/failure is fail-open and does not change task outcomes by
  default.

## Worktrees, Branches, and Logs

Per-task branch name:
- `yeehaw/task-<task_number>-<slug>`

Per-roadmap integration branch:
- `yeehaw/roadmap-<roadmap_id>` (created on first dispatch for that roadmap)
- Task worktrees are based on this branch.
- On `done`, Yeehaw first rebases the task branch onto this integration branch, then merges (fast-forward preferred).

Worktree location:
- `~/.yeehaw/worktrees/<repo-key>/...`

Orchestrator runtime/log locations:
- `~/.yeehaw/logs/task-<id>/attempt-XX-<agent>.log`
- `~/.yeehaw/signals/task-<id>/signal.json`
- `~/.yeehaw/orchestrator.pid`

## Worker Configuration (`~/.yeehaw/workers.json`)

Workers default to **no MCP servers configured** unless overridden.

Example:

```json
{
  "disable_default_mcp": true,
  "extra_args": ["--global-flag"],
  "env": {
    "GLOBAL_ENV": "1"
  },
  "agents": {
    "codex": {
      "disable_default_mcp": false,
      "extra_args": ["--agent-flag"],
      "env": {
        "CODEX_ENV": "2"
      }
    }
  }
}
```

Resolution rules:
- Global config applies to all agents.
- Per-agent config overrides/extends global config.
- `extra_args` are concatenated: global first, then agent-specific.
- `env` maps are merged: agent keys override global keys.

## MCP Server

Run server directly:

```bash
uv run python -m yeehaw.mcp.server --db ~/.yeehaw/yeehaw.db
```

Exposed tools:
- `create_project(name, repo_root)`
- `list_projects()`
- `get_roadmap(project_name, color=True)`
- `preview_roadmap(markdown, color=True)`
- `create_roadmap(project_name, markdown)`
- `edit_roadmap(project_name, markdown)` (true in-place edit)
- `list_tasks(project_name=None, status=None)`
- `get_project_status(project_name)`
- `approve_roadmap(project_name)`
- `pause_task(task_id)`
- `resume_task(task_id)`
- `update_task(task_id, status=None, assigned_agent=None, reset_attempts=False)`

Notes:
- `pause_task` works for `pending`, `queued`, and `in-progress`.
- `resume_task` moves `paused -> queued`.
- There is no dedicated MCP `stop_task` tool yet; use CLI `yeehaw stop`.
- `update_task` only supports status transitions to `done`, `blocked`, `failed`, or `queued`; agent assignment only applies when current status is `pending`.

## In-Place Roadmap Editing

Roadmap in-place editing is available via MCP (`edit_roadmap`), not CLI.

High-level flow for a supervisor/controller agent:
1. `get_roadmap(project_name="demo")`
2. Produce updated markdown with renumbered downstream tasks.
3. Optional: `preview_roadmap(markdown=<draft>, color=True)`
4. `edit_roadmap(project_name="demo", markdown=<updated>)`

Safety rules enforced:
- Non-editable task history (`paused`, `in-progress`, `done`, `failed`, `blocked`) cannot be modified or removed.
- For non-draft roadmaps, phase structure cannot be added/removed/reordered.

## Planner / Supervisor Usage

Interactive planning:

```bash
uv run yeehaw plan --project demo --agent codex
```

Planner prompt is configured to:
- Use colorized roadmap previews during discussion.
- Show full verbose preview output (not summary) when presenting updated roadmap drafts.
- Persist via `create_roadmap` or `edit_roadmap`.

## Troubleshooting

### `Another orchestrator is running (PID ...)`

- Stop old process or remove stale PID file:

```bash
rm -f ~/.yeehaw/orchestrator.pid
```

### Want a clean reset of local Yeehaw state

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
mv ~/.yeehaw/yeehaw.db ~/.yeehaw/yeehaw.db.reset-$ts.bak 2>/dev/null || true
mv ~/.yeehaw/yeehaw.db-shm ~/.yeehaw/yeehaw.db-shm.reset-$ts.bak 2>/dev/null || true
mv ~/.yeehaw/yeehaw.db-wal ~/.yeehaw/yeehaw.db-wal.reset-$ts.bak 2>/dev/null || true
uv run yeehaw init
```

Then re-add projects and roadmaps.

### `project add` succeeded but `project list` is empty

You are likely using a different runtime root (for example a different `YEEHAW_HOME` value).

### No logs for a task

- Task may not have launched yet.
- Check task status first:

```bash
uv run yeehaw status --project <name>
```

### Task failed during merge/rebase and you need diagnostics

- Check status for the latest merge summary:

```bash
uv run yeehaw status --project <name>
```

- Inspect full merge attempt history for the task:

```bash
uv run yeehaw logs <task_id> --merge-history
uv run yeehaw logs <task_id> --merge-history --history-limit 10
```

## Development

Run tests:

```bash
uv run --extra dev pytest -q
```
