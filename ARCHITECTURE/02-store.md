# 02 - SQLite Store

The store uses `modernc.org/sqlite` (pure Go, no CGO) with WAL mode for concurrent reads.

## Tables

- **projects** - Registered projects with name, root path, git metadata
- **roadmaps** - Raw markdown roadmaps with lifecycle status (draft → approved → executing → completed)
- **roadmap_phases** - Parsed phases with verification commands
- **tasks** - Individual work items with agent assignment, status tracking, retry counts
- **git_worktrees** - Worktree state tracking (active → merged → removed)
- **events** - Append-only event log for TUI and debugging
- **alerts** - Operator-facing alerts (open/resolved)
- **scheduler_config** - Singleton row with concurrency limits and timeouts

## Design Choices

- Single-writer with `SetMaxOpenConns(1)` - no locking complexity
- WAL mode + busy timeout for durability
- All timestamps stored as ISO 8601 text
- Schema versioning via DDL idempotency (`CREATE TABLE IF NOT EXISTS`)
