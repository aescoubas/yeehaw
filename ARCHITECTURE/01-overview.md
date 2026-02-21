# 01 — Architecture Overview

## System Identity

Yeehaw is a **Planner-Worker multi-agent swarm** for software development. It
decomposes high-level instructions into structured roadmaps and dispatches tasks
to coding agents (Claude Code, Gemini CLI, Codex) running in isolated git
worktrees, coordinated via tmux sessions and a file-based signal protocol.

## Language & Runtime

- **Python 3.12+** — stdlib-first approach, minimal external dependencies
- **SQLite** via `sqlite3` (stdlib) — single-file persistence, zero-config
- **FastMCP** — MCP server exposing task CRUD for the Planner agent
- **argparse** (stdlib) — CLI framework
- **watchdog** — filesystem event monitoring for signal protocol
- **uv** — package management and virtual environments

## Core Architecture Pattern

```
                    ┌─────────────────────┐
                    │   Human Operator     │
                    │  (morning briefing)  │
                    └─────────┬───────────┘
                              │ brain dump
                              ▼
                    ┌─────────────────────┐
                    │   Planner Agent      │
                    │  (Claude/Gemini)     │
                    │  connected via MCP   │
                    └─────────┬───────────┘
                              │ create_task(), update_roadmap()
                              ▼
                    ┌─────────────────────┐
                    │   SQLite Database    │
                    │   (.yeehaw/yeehaw.db)│
                    │   ─── The Brain ─── │
                    └─────────┬───────────┘
                              │ query pending tasks
                              ▼
                    ┌─────────────────────┐
                    │   Orchestrator       │
                    │   (tick loop, 5s)    │
                    └───┬─────────────┬───┘
                        │             │
              dispatch  │             │  monitor
                        ▼             ▼
               ┌──────────────┐ ┌──────────────┐
               │ Worker Agent │ │ Worker Agent │  ... N workers
               │ (tmux + git  │ │ (tmux + git  │
               │  worktree)   │ │  worktree)   │
               └──────┬───────┘ └──────┬───────┘
                      │                │
                      ▼                ▼
               signal.json       signal.json
               (file-based)      (file-based)
```

## Three-Layer Design

### Layer 1: The Brain (SQLite + MCP Server)
SQLite stores all state: projects, roadmaps, phases, tasks, events, alerts,
scheduler config. The FastMCP server exposes this as tools that the Planner
agent can call. The CLI also queries the DB directly for status commands.

### Layer 2: The Planner (AI Agent via MCP)
A single AI agent instance (Claude Code or Gemini CLI) connects to the MCP
server. The human pastes a brain dump, and the Planner translates it into
structured rows: projects, roadmaps with phases, and individual tasks.

### Layer 3: The Workers (tmux + git worktrees + file signals)
The orchestrator reads pending tasks from the DB, creates isolated git
worktrees, launches worker agents in tmux sessions, and monitors for
completion via the sentinel file protocol. Workers never touch the DB.

## Component Map

| Package | Responsibility |
|---------|---------------|
| `cli/` | argparse command tree, user-facing interface |
| `store/` | SQLite schema + CRUD operations |
| `mcp/` | FastMCP server exposing task tools for Planner |
| `planner/` | Planner session lifecycle, MCP server spawn |
| `orchestrator/` | Core dispatch/monitor tick loop |
| `agent/` | Agent profiles (Claude, Gemini, Codex) |
| `git/` | Worktree create/cleanup |
| `tmux/` | Session create/attach/kill |
| `signal/` | Sentinel file protocol + watchdog |
| `roadmap/` | Markdown parser + validator |

## Data Flow

1. `yeehaw plan briefing.md` → spawns MCP server + Planner agent
2. Planner calls `create_project()`, `create_roadmap()`, `create_task()` via MCP
3. `yeehaw roadmap show` → human reviews structured output
4. `yeehaw roadmap approve --project foo` → marks roadmap as approved
5. `yeehaw run --project foo` → orchestrator starts tick loop
6. Each tick: monitor active tasks for signals, dispatch pending tasks
7. `yeehaw status` → query DB, print table
8. `yeehaw attach task-3` → drop into tmux session of worker agent
