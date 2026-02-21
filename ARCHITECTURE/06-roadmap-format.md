# 06 — Roadmap Format

## Structure

Roadmaps are structured Markdown documents with a strict hierarchy:

```markdown
# Roadmap: project-name

## Phase 1: Foundation

**Verify:** `make test`

### Task 1.1: Set up database schema

Detailed description of what needs to be done.
Multiple paragraphs are fine.

### Task 1.2: Create API endpoints

Another task description.

## Phase 2: Features

**Verify:** `pytest tests/integration/`

### Task 2.1: User authentication

Description here.
```

## Rules

1. **H1** (`#`) — Exactly one, format: `# Roadmap: {project-name}`
2. **H2** (`##`) — Phases, format: `## Phase {N}: {title}`
3. **Bold verify** — Optional line after H2: `**Verify:** \`{command}\``
4. **H3** (`###`) — Tasks, format: `### Task {N.M}: {title}`
5. **Body** — Everything after H3 until next heading is the task description
6. Phase numbers sequential starting from 1
7. Task numbers match their phase: Phase 2 tasks are 2.1, 2.2, etc.
8. At least one phase required, each phase needs at least one task

## Parser Output

```python
@dataclass
class Task:
    number: str         # "1.1", "2.3"
    title: str
    description: str

@dataclass
class Phase:
    number: int
    title: str
    verify_cmd: str | None
    tasks: list[Task]

@dataclass
class Roadmap:
    project_name: str
    phases: list[Phase]
```

## Validation

```python
def validate_roadmap(roadmap: Roadmap) -> list[str]:
    """Returns list of validation error messages. Empty = valid."""
    errors = []
    if not roadmap.phases:
        errors.append("Roadmap must have at least one phase")
    for i, phase in enumerate(roadmap.phases):
        if phase.number != i + 1:
            errors.append(f"Phase {phase.number} out of sequence (expected {i+1})")
        if not phase.tasks:
            errors.append(f"Phase {phase.number} has no tasks")
        for j, task in enumerate(phase.tasks):
            expected = f"{phase.number}.{j+1}"
            if task.number != expected:
                errors.append(f"Task {task.number} out of sequence (expected {expected})")
    return errors
```

## Parser

Single-pass line-by-line state machine:
```
States: INIT → HEADER → PHASE → TASK_HEADER → TASK_BODY
```
