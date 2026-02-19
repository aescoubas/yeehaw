# Yeehaw v2 RFC

## Summary
`yeehaw v2` is a control-plane rewrite for coordinating multiple coding agents across multiple projects with native terminal UX.

The design keeps orchestration in a central service while allowing direct, real agent interaction in terminal workspaces.

## Goals
- Support both `tmux` and non-`tmux` local PTY runtimes.
- Treat agent sessions as first-class objects (start, monitor, preempt, reassign, recover).
- Keep roadmap authoring/editing in external editor (`$EDITOR`, e.g. `nvim`).
- Add per-agent token/cost accounting.
- Allow early LLM dispatcher decisions with policy guardrails.
- Preserve a global scheduler with limits:
  - max concurrent sessions global: `20`
  - max concurrent sessions per project: `10`

## Non-Goals
- Remote execution (SSH workers, k8s workers, cloud runners).
- Auto-merge and branch integration.
- Replacing agent-native UX with synthetic chat widgets.

## Product Experience
- `Ops mode`: dashboard for queue, alerts, roadmap approval, budget, and scheduler controls.
- `Workspace mode`: attach/switch into real agent terminal session.
- `w` from project view:
  1. starts/opens roadmap coach session
  2. enters workspace mode for native conversation
  3. returns to ops mode for validation + handoff

## Architecture
### 1. Control Plane
- Long-running orchestration loop.
- Owns task state machine, scheduling, policy checks, and alerting.
- Writes all durable state transitions to SQLite.

### 2. Runtime Adapters
- Common runtime contract:
  - `start_session`
  - `send_user_input`
  - `capture_output`
  - `is_session_alive`
  - `terminate_session`
- Implementations:
  - `TmuxRuntimeAdapter`
  - `LocalPtyRuntimeAdapter`

### 3. Dispatcher Agent
- LLM proposes decomposition, routing, and priority updates.
- Proposals are persisted and can be auto-applied or operator-approved.
- Hard policy constraints always win over LLM proposals.

### 4. Accounting
- Usage records by provider/model/session/task.
- Normalized fields: `input_tokens`, `output_tokens`, `cost_usd`.
- Budget status visible in TUI and attached to alerts.

### 5. Editor Bridge
- Opens roadmap files in `$EDITOR`.
- On editor exit:
  - parse/validate roadmap
  - version revision
  - compute plan delta for scheduler

## State Machines
### Task
`queued -> dispatching -> running -> awaiting_input | stuck | preempted -> running -> completed | failed`

### Session
`starting -> active -> paused -> ended | crashed`

### Roadmap
`draft -> edited -> validated -> approved -> executing -> replanned`

## Data Model (v2 baseline)
- `projects`
- `roadmaps`, `roadmap_revisions`
- `task_batches`, `tasks`, `task_attempts`, `task_dependencies`
- `agent_sessions`, `session_events`, `operator_messages`
- `dispatcher_decisions`
- `usage_records`
- `scheduler_config`
- `alerts`
- `checkpoints`
- `git_worktrees`

## Policy Model
- Task branch/worktree isolation is mandatory.
- Agent commits only on assigned branch/worktree.
- Agent merges are forbidden.
- Violations produce blocking alerts and task failure.

## Scheduler Model
- Global queue with simple weighted priority.
- Hard capacity caps (global + per-project).
- Preemption support for higher-priority work.
- Stuck detection combines:
  - inactivity timeout
  - loop signatures
  - interactive trap detection
- Auto-reassign with bounded attempts.

## Migration Strategy
- Introduce `yeehaw_v2` package in parallel with existing code.
- Keep v1 operational during v2 stabilization.
- Migrate project/task metadata incrementally via import tool.
- Switch default CLI entrypoint after v2 feature parity.

## Initial Delivery Plan
1. Scaffold `yeehaw_v2` core modules:
   - runtime interfaces and adapters
   - schema bootstrap
   - control-plane tick loop
2. Add roadmap editor integration.
3. Implement dispatcher and accounting ingestion.
4. Integrate TUI workspace switching and approval flow.
5. Add E2E scenarios for preemption/reassign and policy enforcement.

## Implementation Status (Current)
Completed in the current codebase:
- Runtime adapters: `tmux` + local PTY.
- Control-plane dispatch loop with global/project concurrency caps.
- Dispatcher decision persistence + auto-apply at dispatch.
- Usage ingestion from runtime output with delta accounting.
- Stuck detection (interactive trap, loop signature, inactivity) with auto-reassign/failover.
- Per-task worktree/branch preparation and metadata persistence.
- TUI ops dashboard with projects, batches, tasks, sessions.
- TUI batch lifecycle operations: queue, detail, replan, pause, resume, preempt.
- TUI alerts/dispatcher modal actions.
- TUI project onboarding flow (replacing CLI-first onboarding).
- TUI inline operator reply to blocked tasks.

Still to tighten for production quality:
- Strong policy enforcement over actual git actions performed by agents (commit/merge guardrails).
- Comprehensive E2E stress scenarios for long-running concurrent workloads.
- Final default CLI cutover from v1 to v2 once parity and soak testing are complete.
