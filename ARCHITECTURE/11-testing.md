# 11 — Testing Strategy

## Test Stack

- `pytest`
- optional coverage: `pytest-cov`
- integration-style tests for git/tmux behavior where needed

## Current Test Modules

```
tests/
├── conftest.py
├── test_agent.py
├── test_cli.py
├── test_cli_more.py
├── test_git.py
├── test_main_module.py
├── test_mcp.py
├── test_mcp_more.py
├── test_orchestrator.py
├── test_planner.py
├── test_planner_generate.py
├── test_roadmap.py
├── test_schema_migration.py
├── test_signal.py
├── test_store.py
├── test_tmux.py
└── test_worker_runtime_config.py
```

## Coverage Focus

- parser/validator behavior (including dependency metadata and numbering)
- schema init/migrations (legacy compatibility, paused/integration branch support)
- store CRUD + dependency persistence + in-place roadmap edit safety
- orchestrator dispatch/monitor/retry/phase advancement/merge flow
- MCP tool responses and error paths
- CLI behavior and status/log formatting
- worker launch hardening (`workers.json`, default MCP disablement)

## Recommended Commands

```bash
uv run --extra dev pytest -q
uv run --extra dev pytest --cov=yeehaw --cov-report=term-missing
```

Examples:

```bash
uv run --extra dev pytest tests/test_orchestrator.py -q
uv run --extra dev pytest tests/test_schema_migration.py -q
```

## Practical Notes

- Use temporary directories/DBs for isolation.
- Keep subprocess-heavy tests mocked where logic coverage is sufficient.
- Preserve a small set of integration tests for real git/tmux semantics.
