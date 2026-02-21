.PHONY: all install test lint check clean

all: check

install:
	uv sync

test:
	uv run pytest

test-cov:
	uv run pytest --cov=yeehaw --cov-report=term-missing

lint:
	uv run python -m py_compile src/yeehaw/cli/main.py

check: test

clean:
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
