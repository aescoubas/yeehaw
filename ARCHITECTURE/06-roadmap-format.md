# 06 — Roadmap Markdown Format

## Required Structure

```md
# Roadmap: <project-name>

## Phase <N>: <title>
**Verify:** `<command>`            # optional, must appear before first task in phase

### Task <N.M>: <title>            # also accepts: ### P0.1: <title>
<task description block>
```

## Parsing Rules

- Header: exactly one `# Roadmap: ...`
- Phase heading: `## Phase <int>: ...`
- Task heading:
  - `### Task N.M: ...`
  - `### P0.1: ...` (normalized to `0.1` in storage)
- Optional trailing checklist marker in task title (like `[x]`) is stripped
- Task description is every line until next heading

## Numbering Rules

- Phases can start at `0` or `1`, then must be sequential
- Tasks must be sequential within phase (`N.1`, `N.2`, ...)
- Task number must match phase number

## Dependency Metadata

Dependencies are extracted from task description lines:

`**Depends on:** <refs>`

Accepted refs:

- `1.1`, `2.3`, `P0.1` (normalized)
- comma/space mixed text is tolerated; task refs are regex-extracted
- `none`, `n/a`, `-` treated as no dependencies

## Validation

`validate_roadmap(...)` enforces:

- at least one phase
- sequential phase and task numbering
- valid task-number format
- dependency references must exist
- no self-dependency
- no dependency cycle

Example cycle error:

`Task dependency cycle detected: 1.1 -> 1.2 -> 1.1`

## Execution Semantics

- Dependency edges are persisted to `task_dependencies`
- Orchestrator dispatches queued tasks only when all blockers are `done`
- Phase verify command runs only after all phase tasks are `done`
