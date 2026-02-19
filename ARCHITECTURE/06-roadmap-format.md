# 06 - Roadmap Format

Roadmaps are structured markdown documents produced by the master agent.

## Format

```markdown
# Roadmap: project-name

## Phase 1: Phase Title
**Verification:** `go test ./...`

### Task 1.1: Task Title
Task description paragraph(s).

### Task 1.2: Another Task
More description.

## Phase 2: Next Phase
**Verification:** `npm test`

### Task 2.1: Something
Description.
```

## Parsing Rules

1. H1 (`# Roadmap: ...`) - project name confirmation
2. H2 (`## Phase N: ...`) - phase boundary
3. Bold verification line after H2 - phase verification command
4. H3 (`### Task N.M: ...`) - task boundary
5. Everything after H3 until next heading - task description

## Validation

- At least one phase required
- Each phase must have at least one task
- Task numbers must be sequential within phases
- Phase numbers must be sequential
