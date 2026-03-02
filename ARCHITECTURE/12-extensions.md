# 12 — Extension Architecture Contract

## Goals

- Keep the core orchestrator focused on scheduling, state transitions, git operations,
  and signal processing.
- Enable optional augmentation (notifications, telemetry, policy checks) through
  stable hook contracts.
- Preserve deterministic default behavior when no extensions are configured.

## Non-Goals

- Requiring any external service for normal Yeehaw operation.
- Moving core task lifecycle decisions into third-party code.
- Allowing extensions to directly mutate SQLite state or task worktrees.

## Core Boundary

Core responsibilities (non-extensible authority):

- Task state machine and dependency gating.
- Worker launch, timeout handling, and retry policy.
- Task branch rebase/merge validation for `done`.
- Phase verify command execution and roadmap progression.
- CLI and MCP command semantics.

Extension responsibilities (optional augmentation only):

- Observe lifecycle hooks.
- Return advisory side effects (`log_event`, `create_alert`).
- Perform external calls at extension risk (timeouts/failures are isolated).

## Dispatch Model

- Hook payloads and responses are JSON objects.
- Delivery is at-most-once per extension per emitted event.
- Invocation order is deterministic (configured extension order).
- Hooks are synchronous but bounded by per-hook timeout.
- With zero extensions configured, no hooks are invoked and command behavior is
  unchanged from current Yeehaw behavior.

## Hook Events

| Event name | Emitted when | Required `context` fields |
|---|---|---|
| `orchestrator.start` | orchestrator loop starts | `config`, `runtime_root` |
| `orchestrator.stop` | orchestrator loop exits | `reason`, `uptime_sec` |
| `task.dispatch.before` | queued task is about to launch | `dependency_ids`, `capacity` |
| `task.dispatch.after` | launch attempt finishes | `result`, `tmux_session`, `log_path`, `error` |
| `task.signal.received` | worker `signal.json` parsed | `signal`, `signal_path` |
| `task.state.changed` | task status changes | `from_status`, `to_status`, `reason` |
| `task.retry.scheduled` | failed task is requeued | `attempts_used`, `max_attempts`, `next_status` |
| `phase.verify.before` | phase verify command starts | `phase_number`, `verify_cmd` |
| `phase.verify.after` | phase verify command returns | `phase_number`, `verify_cmd`, `return_code`, `status` |
| `alert.created` | operational alert row is created | `alert_id`, `severity`, `message`, `task_id` |

## Event Payload Schema (`schema_version=1`)

Base envelope (all events):

```json
{
  "schema_version": 1,
  "event_name": "task.state.changed",
  "event_id": "uuid-v4",
  "emitted_at": "2026-03-02T12:34:56Z",
  "source": {
    "component": "orchestrator",
    "yeehaw_version": "x.y.z"
  },
  "project": {
    "id": 1,
    "name": "demo",
    "repo_root": "/abs/repo/path"
  },
  "roadmap": {
    "id": 7,
    "status": "executing",
    "integration_branch": "yeehaw/roadmap-7"
  },
  "task": {
    "id": 42,
    "task_number": "1.2",
    "title": "Define extension contract",
    "status": "in-progress",
    "assigned_agent": "codex",
    "branch_name": "yeehaw/task-1.2-define-extension-contract"
  },
  "attempt": {
    "current": 2,
    "max": 4,
    "timeout_minutes": 60
  },
  "context": {}
}
```

Field rules:

- `schema_version` is required and currently `1`.
- `project`, `roadmap`, `task`, and `attempt` may be `null` when not applicable to
  a global event.
- `context` is required and event-specific; unknown keys must be ignored by
  extensions.
- Timestamps are UTC RFC 3339 (`YYYY-MM-DDTHH:MM:SSZ`).

## Extension Return Schema (`schema_version=1`)

```json
{
  "schema_version": 1,
  "event_id": "uuid-v4",
  "extension": "example-extension",
  "status": "ok",
  "summary": "short optional note",
  "actions": [
    {
      "type": "log_event",
      "kind": "extension.example.note",
      "message": "extension observation"
    },
    {
      "type": "create_alert",
      "level": "warning",
      "message": "extension warning"
    }
  ],
  "metrics": {
    "duration_ms": 27
  }
}
```

Return rules:

- `status` enum: `ok | ignored | error`.
- `event_id` must match incoming payload `event_id`.
- Unknown action types are ignored and logged as protocol warnings.
- Allowed actions in v1:
  - `log_event`: append informational operational event.
  - `create_alert`: create warning/error alert.
- No action may directly transition task status, alter retries, or modify dependency
  evaluation.

## Timeout and Failure Semantics

- Per-extension, per-hook timeout default: `2000ms`.
- Maximum configurable timeout for any hook invocation: `10000ms`.
- On timeout, the invocation is cancelled and treated as `status=error`.
- Hook failures are fail-open:
  - task execution continues;
  - no task status change is caused by extension failure;
  - core logs an `extension_invocation_failed` operational event.
- Invalid JSON or schema mismatch is treated as extension failure for that invocation.
- No in-hook retry is attempted for the same event emission.

## Compatibility Guarantees

Contract stability is strict for each major `schema_version`:

- Existing event names are never renamed or removed within the same major version.
- Existing required envelope fields and their types are never changed in-place.
- Existing required `context` fields for a named event are never removed in-place.
- Additions are additive only (new optional fields, new optional event names,
  new optional action fields).
- Breaking changes require a new major `schema_version`.

Expected consumer behavior:

- Extensions must ignore unknown fields and unknown event names.
- Core must ignore unknown action types and continue processing.

## Explicit Non-Requirement of External Services

Yeehaw core remains self-contained:

- no required message broker,
- no required webhook endpoint,
- no required hosted control plane.

Extensions may call external services, but those integrations are optional and
isolated by timeout + fail-open behavior.
