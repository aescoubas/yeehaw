# yeehaw

Python harness to orchestrate CLI coding agents (`codex`, `claude`, `gemini`, or custom) in `tmux`, with SQLite-backed state.

## Core model

- A `project` is one Git repository clone at one filesystem path.
- A `roadmap` is an execution plan with staged work.
- A `run` executes roadmap tracks in parallel tmux windows.

## What this MVP does

- Registers Git-backed projects and stores repo metadata (`remote`, `default branch`, `head sha`).
- Supports roadmap definitions in either YAML or markdown phase format.
- Launches one tmux window per roadmap track.
- Runs stages autonomously and persists status/events in SQLite.
- Detects stage completion and blocked-input markers from pane output.
- Provides CLI status and a curses TUI monitor.
- Provides `project coach` and `roadmap coach` to talk to agents interactively.

## Install

```bash
pip install -e .
```

Requirements:

- Python 3.10+
- `tmux`
- Agent CLIs available in `PATH`

## Quick start

1. Initialize DB and create a project from a repo path:

```bash
yeehaw init-db
yeehaw project create --root . --guidelines-file templates/guidelines.example.md
```

2. Or define the project by talking to an agent:

```bash
yeehaw project coach --root . --agent codex
```

3. Bulk import existing repositories:

```bash
yeehaw project import --roots /home/you/git_repos --max-depth 6
```

4. Create a roadmap:

```bash
yeehaw roadmap template --format markdown --output roadmap.md
yeehaw roadmap validate roadmap.md
```

5. Or talk to an agent to author the roadmap:

```bash
yeehaw roadmap coach --project yeehaw --agent codex --output roadmap.md
```

6. Run roadmap:

```bash
yeehaw run start --project yeehaw --roadmap roadmap.md
```

7. Monitor:

```bash
yeehaw run status
yeehaw run status --run-id 1
yeehaw tui
```

Dashboard controls:

- `Tab`: switch focus between `Projects` and `Runs`
- `v`: toggle `Runs`/`Tasks` list mode
- `Enter` on `Projects`: apply project selection and jump to `Runs`
- `n`: open `Create Run` modal for selected project (roadmap path + agent)
- `w`: run guided workflow for selected project:
  - open an inline roadmap coach chat inside the TUI
  - validate generated roadmap when you exit the inline chat
  - confirm handoff and launch coding run
- `a`: add/import a project from filesystem path (TUI onboarding)
- `b`: create a batch from free-form tasks (planner agent generates roadmap, tasks are queued)
- `s`: run one global scheduler tick
- `z`: toggle auto scheduler ticks in TUI
- `y`: reply inline to selected blocked task (`awaiting_input`)
- `j` / `k` or arrows: move selection in focused panel
- `PgUp` / `PgDn`: jump selection in focused panel
- `g` / `G`: first/last run
- `r`: refresh
- `q`: quit

## Roadmap formats

YAML template:

```bash
yeehaw roadmap template --format yaml --output roadmap.yaml
```

Markdown template (phase-style):

```bash
yeehaw roadmap template --format markdown --output roadmap.md
```

Phase-style markdown expected shape:

```markdown
## 2. Execution Phases

### Phase 1: Setup & Configuration
**Status:** TODO
**Token Budget:** Medium
**Prerequisites:** None

**Objective:**
Short objective paragraph.

**Tasks:**
- [ ] Item

**Verification:**
- [ ] Check

---
```

Markdown phase roadmaps are parsed into a single track (`main`) with sequential stages.

## Completion/input protocol

For each stage, the harness asks agents to emit:

- `[[YEEHAW_DONE <token>]]` then `Summary:` and `Artifacts:` sections.
- `[[YEEHAW_NEEDS_INPUT <token>]]` then `Question: ...` when blocked.

The orchestrator watches tmux pane output and updates SQLite state.

## Global Scheduler + Task Queue

Create a batch from free-form tasks:

```bash
yeehaw batch create --project <project> --name "<batch>" --tasks "task 1; task 2; task 3"
```

Run scheduler:

```bash
yeehaw scheduler start
# or one cycle:
yeehaw scheduler tick
```

Inspect/respond tasks:

```bash
yeehaw task list --status awaiting_input
yeehaw task reply --task-id <id> --answer "your answer"
yeehaw task pause --task-id <id>
```

Update scheduler limits:

```bash
yeehaw scheduler config --set --max-global 20 --max-project 10 --stuck-minutes 12
```

## SQLite path

Default:

- `./.yeehaw/yeehaw.db`

Override:

- `YEEHAW_DB=/path/to/yeehaw.db`
- `yeehaw --db /path/to/yeehaw.db ...`

## Testing

Install dev dependencies and run tests with coverage:

```bash
pip install -e ".[dev]"
pytest
```

Coverage is enforced at 100% for the automated test target.

## v2 Scaffold

`yeehaw v2` scaffolding is available under `yeehaw_v2/` with:

- runtime adapter interfaces + implementations (`tmux` and local PTY),
- SQLite schema bootstrap for the v2 control plane,
- a scheduler/control-plane with dispatcher auto-apply, stuck detection, auto-reassign, and usage ingestion,
- a multi-pane TUI dashboard for projects, batches, tasks, and sessions,
- batch lifecycle controls (pause/resume/preempt/replan),
- project onboarding and roadmap editing directly from the TUI.

Design details are documented in `docs/rfc-v2.md`.

Quick start (scaffold):

```bash
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db init-db
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db project add --name myproj --root /path/to/repo
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db roadmap edit --project myproj --path roadmap.md
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db roadmap validate --path /path/to/repo/roadmap.md
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db task queue --project myproj --title "Implement feature X" --runtime local_pty --agent codex
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db scheduler tick
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db usage record --provider openai --model gpt-5 --input-tokens 1000 --output-tokens 450 --cost-usd 0.021
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db usage summary
python -m yeehaw_v2 --db ./.yeehaw/yeehaw_v2.db tui
```

v2 TUI controls:

- `Tab`: cycle focus `Projects -> Batches -> Tasks -> Sessions`
- `o`: onboard project (name/root/guidelines file)
- `n`: queue one task for selected project
- `b`: queue batch from free-form task list (opened in `$EDITOR`)
- `Enter` on `Batches`: open batch detail modal (tasks + timeline)
- `r` in batch detail: replan batch from `$EDITOR` and replace open tasks
- `p` / `u` / `x`: pause, resume, preempt selected batch
- `y`: reply to selected task (operator input to active session)
- `a`: open alerts/dispatcher modal (resolve alert or apply decision)
- `w`: open selected session workspace (`tmux attach` or local PTY view)
- `t`: single scheduler tick
- `z`: toggle auto-tick
- `r`: refresh dashboard
- `q`: quit
