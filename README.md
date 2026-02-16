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
- `Enter` on `Projects`: apply project selection and jump to `Runs`
- `n`: open `Create Run` modal for selected project (roadmap path + agent)
- `w`: run guided workflow for selected project:
  - launch interactive roadmap coach session in tmux
  - validate generated roadmap when you detach
  - confirm handoff and launch coding run
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
