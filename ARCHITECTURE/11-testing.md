# 11 — Testing Strategy

## Framework

- **pytest** with fixtures for database, temp directories, and mock subprocesses
- Target **80%+ code coverage** via `pytest-cov`
- Tests live in `tests/` at project root, mirroring `src/yeehaw/` structure

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_store.py            # SQLite CRUD, schema validation
├── test_roadmap.py          # Parser, validator edge cases
├── test_signal.py           # Signal reading, watchdog events
├── test_git.py              # Branch naming, worktree operations
├── test_agent.py            # Profile resolution, prompt building
├── test_orchestrator.py     # Dispatch/monitor with mocked deps
├── test_mcp.py              # MCP server tool responses
└── test_cli.py              # CLI argument parsing, output format
```

## Fixtures (`conftest.py`)

```python
import pytest
import sqlite3
from pathlib import Path
from yeehaw.store.store import Store
from yeehaw.store.schema import SCHEMA_DDL

@pytest.fixture
def tmp_db(tmp_path):
    """Ephemeral SQLite database for each test."""
    db_path = tmp_path / "test.db"
    store = Store(db_path)
    yield store
    store.close()

@pytest.fixture
def tmp_repo(tmp_path):
    """Temporary git repository for worktree tests."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                    cwd=repo, capture_output=True)
    return repo

@pytest.fixture
def sample_roadmap_md():
    """Valid roadmap markdown for parser tests."""
    return '''# Roadmap: test-project

## Phase 1: Setup

**Verify:** `make test`

### Task 1.1: Create schema

Set up the database schema.

### Task 1.2: Add migrations

Write migration scripts.

## Phase 2: Features

### Task 2.1: User model

Implement the user model.
'''
```

## Test Categories

### Store Tests (`test_store.py`)
- Create/read/update/delete for all entities
- Schema validation on fresh DB
- Foreign key constraints
- Status transition enforcement
- Concurrent read access (WAL mode)
- Scheduler config singleton behavior

### Roadmap Tests (`test_roadmap.py`)
- Valid roadmap parsing
- Missing header, out-of-sequence phases/tasks
- Phase without tasks
- Task without description
- Verify command extraction
- Multi-paragraph task descriptions
- Edge: empty lines, trailing whitespace

### Signal Tests (`test_signal.py`)
- Valid signal read
- Partial JSON (incomplete write)
- Missing required fields
- Retry logic with mock sleep
- watchdog event handler callback

### Git Tests (`test_git.py`)
- Branch name sanitization
- Special characters in title
- Long title truncation
- Worktree create/cleanup (requires real git)

### Agent Tests (`test_agent.py`)
- Profile resolution (explicit, default)
- Unknown agent fallback
- Prompt construction with/without failure context
- Launch command shell escaping
- Launcher script generation

### Orchestrator Tests (`test_orchestrator.py`)
- Single tick: dispatch queued task (mocked tmux/git)
- Single tick: detect signal, complete task
- Concurrency limits respected
- Timeout detection
- Retry on failure
- Phase advancement after all tasks done
- PID file creation/cleanup

### MCP Tests (`test_mcp.py`)
- Tool registration and schema
- create_project tool
- create_roadmap with validation
- list_tasks filtering
- Error responses for invalid input

### CLI Tests (`test_cli.py`)
- Subcommand routing
- Required argument validation
- Output format (table, JSON)
- Help text generation

## Running Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=yeehaw --cov-report=term-missing

# Specific module
uv run pytest tests/test_store.py -v

# Skip integration tests (require git/tmux)
uv run pytest -m "not integration"
```

## Markers

```python
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "integration: tests requiring git or tmux (deselect with '-m not integration')",
]
```
