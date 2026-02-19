# 01 - Architecture Overview

Yeehaw is a multi-agent coding orchestrator written in Go. It decomposes high-level project instructions into structured roadmaps, then dispatches individual tasks to coding agents (Claude Code, Gemini CLI, Codex) running in isolated git worktrees.

## Core Flow

1. **User** provides free-text instructions
2. **Master Agent** (Claude Code in tmux) converts instructions into a structured markdown roadmap
3. **Harness** parses and validates the roadmap, stores it in SQLite
4. **User** approves the roadmap
5. **Orchestrator** loop dispatches tasks to agents, monitors progress, handles completion/failure
6. **Agents** work in git worktrees, signal completion via sentinel files
7. **Harness** verifies results, merges branches, advances phases

## Key Components

| Component | Package | Purpose |
|-----------|---------|---------|
| CLI | `cmd/yeehaw` | User interface (cobra) |
| Store | `internal/store` | SQLite persistence |
| Git | `internal/git` | Worktree management |
| Tmux | `internal/tmux` | Session management |
| Agent | `internal/agent` | Agent profile registry |
| Roadmap | `internal/roadmap` | Markdown parser |
| Signal | `internal/signal` | Sentinel file protocol |
| Orchestrator | `internal/orchestrator` | Core dispatch/monitor loop |
| TUI | `internal/tui` | Bubble Tea dashboard |
