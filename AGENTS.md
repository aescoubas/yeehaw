# AGENTS.md

## Development Rule: Tests Required for New Functions

When implementing any new function, run the full test suite before considering the change complete.

Required command:

```bash
.venv/bin/python -m pytest -q
```

If tests fail, fix the issues and rerun the full suite until it passes.
