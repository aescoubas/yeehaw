# Roadmap: yeehaw

## Phase 0: Contracts, Feature Flags, and Non-Regression Baseline
**Verify:** `uv run --extra dev pytest -q`

### Task 0.1: Define extension architecture contract and event payload schema
**Depends on:** none
**Repo:** yeehaw
**Files:**
- `ARCHITECTURE/12-extensions.md` — document extension boundaries and event hook contracts
- `README.md` — add extension model overview and compatibility guarantees
**Description:**
Define the architecture contract for keeping the core orchestration engine lean while enabling optional augmentation through extension points.
Specify hook event names, payload shape, return shape, timeout behavior, and failure semantics.
Document strict backward-compatibility rules and the non-goal of adding mandatory external services.
**Done when:**
- [ ] `ARCHITECTURE/12-extensions.md` exists with hook event contract and payload schema
- [ ] Compatibility guarantees are documented in `README.md`
- [ ] No existing command behavior changes by default

### Task 0.2: Add global runtime feature flags with safe defaults
**Depends on:** 0.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/runtime.py` — add config root/path helpers for feature flags
- `src/yeehaw/config/loader.py` — parse runtime config flags with defaults
- `src/yeehaw/config/models.py` — typed feature flag model
- `tests/test_config.py` — defaults, override, invalid config coverage
**Description:**
Introduce runtime flags to toggle optional subsystems (`hooks`, `policies`, `conflict_scheduler`, `budgets`, `notifications`, `pr_automation`, `memory_packs`).
Flags must default to disabled so current orchestration behavior is preserved.
Config parser must be strict on types and provide actionable errors.
**Done when:**
- [ ] Feature flags can be loaded from runtime config file
- [ ] All flags default to disabled when config file is missing
- [ ] Invalid config values raise clear user-facing errors
- [ ] Tests cover default and override paths

### Task 0.3: Add CLI config command and baseline regression gate
**Depends on:** 0.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/main.py` — register `config` command tree
- `src/yeehaw/cli/config.py` — implement `show` and `set` handlers
- `tests/test_cli_more.py` — command routing and output tests
- `tests/test_cli.py` — CLI behavior assertions
**Description:**
Expose runtime configuration controls via CLI to avoid manual file editing and to make extension rollout reproducible.
Add regression checks proving that with all feature flags disabled, current behavior remains unchanged.
**Done when:**
- [ ] `yeehaw config show` prints effective feature flags
- [ ] `yeehaw config set` updates supported keys safely
- [ ] CLI tests cover command routing and validation errors
- [ ] Baseline behavior remains unchanged with extensions disabled

## Phase 1: Hook Framework (Core Extensibility Primitive)
**Verify:** `uv run --extra dev pytest tests/test_hooks.py tests/test_orchestrator.py -q`

### Task 1.1: Implement hook models, discovery, and loading
**Depends on:** 0.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/hooks/models.py` — hook request/response dataclasses
- `src/yeehaw/hooks/loader.py` — discover hooks from runtime and optional project directories
- `src/yeehaw/hooks/__init__.py` — package exports
- `tests/test_hooks.py` — discovery and validation tests
**Description:**
Create a hook subsystem that discovers executable hooks from configured directories and validates metadata.
Hook discovery must be deterministic and support both global and project-scoped hooks under explicit opt-in.
**Done when:**
- [ ] Hook loader discovers hooks from runtime directory
- [ ] Duplicate hook names are handled deterministically
- [ ] Invalid hook metadata is reported with actionable errors
- [ ] Unit tests cover discovery and load validation

### Task 1.2: Implement hook runner with timeout, isolation, and structured output
**Depends on:** 1.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/hooks/runner.py` — execute hooks with timeout and output capture
- `src/yeehaw/hooks/errors.py` — typed hook runtime errors
- `tests/test_hooks.py` — timeout/error/output tests
**Description:**
Execute hooks in isolated subprocesses with strict per-hook timeout and bounded payload size.
Hook failures must not crash orchestrator by default; strict mode should optionally hard-fail.
Hook output must follow structured JSON response semantics.
**Done when:**
- [ ] Hook execution is timeout-bounded
- [ ] Hook non-zero exit is captured and classified
- [ ] JSON response parsing validates expected schema
- [ ] Error paths are fully covered by tests

### Task 1.3: Persist hook run telemetry and wire orchestrator events
**Depends on:** 1.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/store/schema.py` — add `hook_runs` table migration
- `src/yeehaw/store/store.py` — add CRUD helpers for hook run records
- `src/yeehaw/orchestrator/engine.py` — invoke hooks at key lifecycle events
- `tests/test_store.py` — hook run persistence tests
- `tests/test_orchestrator.py` — hook integration tests
**Description:**
Add hook lifecycle telemetry so extension behavior is observable and debuggable.
Wire events into orchestrator call sites (`pre_dispatch`, `post_dispatch`, `pre_merge`, `post_merge`, `on_fail`, `on_phase_complete`, `on_roadmap_complete`).
**Done when:**
- [ ] Hook runs are persisted with status, duration, and summary
- [ ] Orchestrator invokes configured events in correct order
- [ ] Hook failures generate useful event/alert diagnostics
- [ ] Tests verify hook event integration with orchestrator

## Phase 2: Policy Packs (Guardrails as Configuration)
**Verify:** `uv run --extra dev pytest tests/test_policy.py tests/test_orchestrator.py -q`

### Task 2.1: Build policy engine and project policy loader
**Depends on:** 1.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/policy/models.py` — policy config model
- `src/yeehaw/policy/engine.py` — policy evaluation orchestration
- `src/yeehaw/policy/loader.py` — load per-project policy packs
- `tests/test_policy.py` — loader and model validation tests
**Description:**
Introduce policy packs as project-scoped runtime configuration controlling quality and safety constraints.
Policy loading should support defaults + per-project overrides with strict validation.
**Done when:**
- [ ] Policy files load from configured runtime path
- [ ] Invalid policy schema yields actionable validation errors
- [ ] Policy evaluation entrypoint exists and is test-covered

### Task 2.2: Implement built-in policy checks for done-accept and pre-merge
**Depends on:** 2.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/policy/checks.py` — built-in checks (paths, commit format, file count)
- `src/yeehaw/orchestrator/engine.py` — enforce checks before done acceptance/merge
- `tests/test_policy.py` — check-level behavior tests
- `tests/test_orchestrator.py` — enforcement integration tests
**Description:**
Add built-in checks: required commit message regex, allowed path prefixes, forbidden path patterns, max changed files threshold.
Checks should fail tasks with precise, human-readable reasons and generate alerts/events.
**Done when:**
- [ ] Built-in checks run before task done acceptance and merge
- [ ] Violations fail with clear reason in task failure metadata
- [ ] Alerts/events include policy violation context
- [ ] Integration tests verify enforcement behavior

### Task 2.3: Add policy CLI tooling for lint and explain
**Depends on:** 2.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/main.py` — register `policy` command
- `src/yeehaw/cli/policy.py` — implement `lint` and `explain`
- `README.md` — policy usage docs
- `tests/test_cli_more.py` — policy CLI tests
**Description:**
Provide operator tooling to validate policy packs before execution and to explain why a task was blocked.
Tooling must reduce policy debugging friction and avoid manual DB inspection.
**Done when:**
- [ ] `yeehaw policy lint --project <name>` validates policy pack
- [ ] `yeehaw policy explain --task <id>` shows evaluated checks and outcomes
- [ ] README includes policy quickstart and troubleshooting

## Phase 3: Conflict-Aware Scheduler
**Verify:** `uv run --extra dev pytest tests/test_roadmap.py tests/test_orchestrator.py tests/test_store.py -q`

### Task 3.1: Parse and persist task file targets from roadmap metadata
**Depends on:** 1.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/roadmap/parser.py` — parse `**Files:**` block entries
- `src/yeehaw/store/schema.py` — add `task_file_targets` table
- `src/yeehaw/store/store.py` — persist and query file targets
- `tests/test_roadmap.py` — parser metadata coverage
- `tests/test_store.py` — persistence coverage
**Description:**
Use `**Files:**` metadata to represent expected edit surfaces for each task.
Persist normalized file targets to support scheduler conflict detection.
**Done when:**
- [ ] Parser extracts `**Files:**` entries into normalized targets
- [ ] Targets are stored per task and retrievable
- [ ] Parsing/storage paths are covered by tests

### Task 3.2: Enforce overlap-aware dispatch gating
**Depends on:** 3.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/orchestrator/engine.py` — add overlap-aware dispatch checks
- `src/yeehaw/store/store.py` — add overlap query helpers
- `tests/test_orchestrator.py` — conflict gating behavior tests
**Description:**
Block dispatch of queued tasks whose file target sets overlap with in-progress tasks, unless explicitly marked safe.
This reduces merge conflicts without reducing global throughput for independent tasks.
**Done when:**
- [ ] Overlapping tasks are held in queued state while conflicting task is active
- [ ] Non-overlapping tasks still dispatch in parallel
- [ ] Tests verify dispatch gating correctness

### Task 3.3: Add conflict visibility in status output
**Depends on:** 3.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/status.py` — display conflict hold reason
- `README.md` — status column semantics update
- `tests/test_cli_more.py` — status rendering tests
**Description:**
Expose why queued tasks are not launching to reduce operator guesswork.
Display conflict blocker details in status output and JSON mode.
**Done when:**
- [ ] `status` output includes conflict hold reason for queued tasks
- [ ] JSON status includes machine-readable hold metadata
- [ ] Docs and tests are updated accordingly

## Phase 4: Merge Intelligence and Diagnostics
**Verify:** `uv run --extra dev pytest tests/test_orchestrator.py tests/test_cli_more.py -q`

### Task 4.1: Persist structured rebase/merge attempt records
**Depends on:** 1.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/store/schema.py` — add `task_merge_attempts` table
- `src/yeehaw/store/store.py` — insert/query merge attempt records
- `src/yeehaw/orchestrator/engine.py` — record attempt lifecycle
- `tests/test_store.py` — merge attempt persistence tests
**Description:**
Capture each rebase/merge attempt with status, conflict type, files, and sha context.
This creates an audit trail for failure analysis and future automation.
**Done when:**
- [ ] Merge attempt records are persisted for success/failure paths
- [ ] Conflict metadata is captured when available
- [ ] Tests cover persistence and retrieval paths

### Task 4.2: Add merge history and diagnostics in CLI logs/status
**Depends on:** 4.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/logs.py` — add merge history view option
- `src/yeehaw/cli/status.py` — expose latest merge diagnostic summary
- `README.md` — operator troubleshooting docs
- `tests/test_cli_more.py` — output coverage
**Description:**
Surface merge diagnostics in first-class operator commands so failures are understandable without manual DB access.
**Done when:**
- [ ] `logs` can show merge history for a task
- [ ] `status` includes latest merge/rebase diagnostic summary where relevant
- [ ] Tests verify output formatting and content

### Task 4.3: Add optional trivial conflict auto-resolver pass
**Depends on:** 4.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/orchestrator/merge_resolver.py` — trivial resolver implementation
- `src/yeehaw/orchestrator/engine.py` — gated resolver invocation
- `src/yeehaw/config/models.py` — feature flag for resolver
- `tests/test_orchestrator.py` — resolver behavior tests
**Description:**
Implement a guarded resolver for non-semantic conflicts (for example whitespace/import order/lockfile regeneration).
Resolver must be disabled by default and only apply when safety criteria are met.
**Done when:**
- [ ] Resolver can be toggled via feature flag
- [ ] Resolver only applies to known safe conflict classes
- [ ] Failed resolver attempts fall back to existing retry behavior
- [ ] Tests cover enabled/disabled and failure paths

## Phase 5: Budget Controls and Reconcile Workflow
**Verify:** `uv run --extra dev pytest tests/test_store.py tests/test_orchestrator.py tests/test_cli_more.py -q`

### Task 5.1: Add per-task budget metadata and enforcement
**Depends on:** 0.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/store/schema.py` — task budget columns (`max_tokens`, `max_runtime_min`, etc.)
- `src/yeehaw/store/store.py` — CRUD helpers for budget metadata
- `src/yeehaw/orchestrator/engine.py` — enforce budgets during monitoring
- `tests/test_store.py` — budget persistence tests
- `tests/test_orchestrator.py` — budget enforcement tests
**Description:**
Bound runaway tasks by enforcing resource budgets at the task level.
Budget violations should fail tasks with explicit reason and alerts.
**Done when:**
- [ ] Task budget metadata is persisted and retrievable
- [ ] Runtime/token budget breaches fail task deterministically
- [ ] Failure reason and alert output are actionable

### Task 5.2: Auto-create reconcile tasks after repeated failures
**Depends on:** 5.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/orchestrator/engine.py` — reconcile task creation trigger
- `src/yeehaw/store/store.py` — helper for linked reconcile task creation
- `tests/test_orchestrator.py` — repeated-failure reconciliation tests
**Description:**
Replace blind repeated retries with a dedicated reconcile task after configurable failure threshold.
Reconcile tasks should summarize prior failures and dependency context.
**Done when:**
- [ ] Reconcile tasks are automatically created after threshold failures
- [ ] Reconcile task descriptions include prior failure context
- [ ] Orchestrator queues reconcile task predictably

### Task 5.3: Expose budget and reconcile state in status output
**Depends on:** 5.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/status.py` — add budget/reconcile indicators
- `README.md` — status semantics update
- `tests/test_cli_more.py` — output validation tests
**Description:**
Ensure operators can quickly identify budget pressure and reconcile workflows in progress.
**Done when:**
- [ ] Status output includes budget and reconcile indicators
- [ ] JSON status includes machine-readable budget/reconcile fields
- [ ] CLI output tests cover new columns/fields

## Phase 6: Notification Sinks
**Verify:** `uv run --extra dev pytest tests/test_notifications.py tests/test_orchestrator.py -q`

### Task 6.1: Build notification sink framework and webhook sink
**Depends on:** 1.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/notify/models.py` — sink config model
- `src/yeehaw/notify/dispatcher.py` — sink dispatch orchestration
- `src/yeehaw/notify/webhook.py` — webhook implementation
- `tests/test_notifications.py` — sink dispatch tests
**Description:**
Introduce optional notification framework with webhook as first sink type.
Dispatch should be async-safe for orchestrator loop and tolerant of transient sink failures.
**Done when:**
- [ ] Notification sinks can be configured and dispatched
- [ ] Webhook sink supports retries/backoff within bounded limits
- [ ] Sink failures do not crash orchestrator

### Task 6.2: Wire high-value event notifications
**Depends on:** 6.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/orchestrator/engine.py` — emit notifications for key events
- `README.md` — notification event docs
- `tests/test_orchestrator.py` — event emission coverage
**Description:**
Emit notifications for blocked tasks, exhausted retries, phase completion, roadmap completion, and daemon failures.
**Done when:**
- [ ] Key events trigger notifications when feature is enabled
- [ ] Notification payload contains project/task identifiers and reason
- [ ] Tests validate event-to-notification mapping

### Task 6.3: Add CLI notification config and dry-run test mode
**Depends on:** 6.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/main.py` — register `notify` command tree
- `src/yeehaw/cli/notify.py` — manage sink config and test dispatch
- `tests/test_cli_more.py` — notification CLI tests
**Description:**
Provide operator commands to configure sinks and test notification delivery without waiting for runtime events.
**Done when:**
- [ ] `notify` CLI can show/set sink config
- [ ] `notify test` sends synthetic event payload
- [ ] CLI tests cover success and failure paths

## Phase 7: PR Automation Adapter Layer
**Verify:** `uv run --extra dev pytest tests/test_scm.py tests/test_cli_more.py -q`

### Task 7.1: Implement SCM adapter interface and git-only adapter
**Depends on:** 4.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/scm/base.py` — adapter interface
- `src/yeehaw/scm/git_local.py` — local git adapter
- `tests/test_scm.py` — adapter tests
**Description:**
Define a minimal adapter surface for publishing roadmap integration branches and summaries.
Start with a local git adapter as baseline.
**Done when:**
- [ ] SCM adapter interface is stable and test-covered
- [ ] Local adapter supports publish-ready operations
- [ ] No direct GitHub coupling in core logic

### Task 7.2: Add GitHub adapter for create/update PR workflows
**Depends on:** 7.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/scm/github.py` — GitHub PR adapter
- `src/yeehaw/scm/models.py` — publish request/response models
- `tests/test_scm.py` — GitHub adapter tests with mocks
**Description:**
Implement optional GitHub adapter to create/update roadmap PR with task and phase summaries.
Adapter must be feature-flagged and optional.
**Done when:**
- [ ] GitHub adapter can create and update PRs
- [ ] Errors are captured and reported as alerts/events
- [ ] Tests validate API request composition and error handling

### Task 7.3: Add roadmap publish CLI and completion hook
**Depends on:** 7.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/main.py` — register `roadmap publish` command
- `src/yeehaw/cli/roadmap.py` — implement publish handler
- `src/yeehaw/orchestrator/engine.py` — optional auto-publish on roadmap completion
- `README.md` — publish workflow docs
- `tests/test_cli_more.py` — publish command tests
**Description:**
Allow operators to publish integrated roadmap output on demand and optionally on completion.
**Done when:**
- [ ] `roadmap publish --project <name>` works with configured adapter
- [ ] Publish output includes generated URL/id for traceability
- [ ] Optional auto-publish on roadmap completion is feature-flagged

## Phase 8: Context Memory Packs for Workers
**Verify:** `uv run --extra dev pytest tests/test_agent.py tests/test_worker_runtime_config.py tests/test_cli_more.py -q`

### Task 8.1: Add project memory pack loader and validation
**Depends on:** 0.3
**Repo:** yeehaw
**Files:**
- `src/yeehaw/context/loader.py` — load project memory pack markdown
- `src/yeehaw/context/models.py` — context pack model/limits
- `tests/test_context.py` — loader/validation tests
**Description:**
Support project-scoped memory packs containing conventions, architecture constraints, and coding standards.
Memory pack loading should be bounded and deterministic.
**Done when:**
- [ ] Project memory packs load from runtime path
- [ ] Size and content guards prevent prompt bloat
- [ ] Loader behavior is test-covered

### Task 8.2: Inject memory packs into worker prompt pipeline
**Depends on:** 8.1
**Repo:** yeehaw
**Files:**
- `src/yeehaw/agent/launcher.py` — include memory pack section in prompt
- `src/yeehaw/orchestrator/engine.py` — pass project context into prompt build
- `tests/test_agent.py` — prompt composition tests
**Description:**
Inject memory pack context before task-specific instructions so workers consistently apply project conventions.
Injection must remain optional and disabled by default.
**Done when:**
- [ ] Memory pack content appears in prompt when feature enabled
- [ ] Prompt file references remain intact for context-window recovery
- [ ] Tests verify prompt composition with and without memory packs

### Task 8.3: Add context CLI management commands
**Depends on:** 8.2
**Repo:** yeehaw
**Files:**
- `src/yeehaw/cli/main.py` — register `context` command tree
- `src/yeehaw/cli/context.py` — `show`, `set`, `edit`, `validate`
- `README.md` — context management docs
- `tests/test_cli_more.py` — context CLI tests
**Description:**
Provide ergonomic commands for managing memory packs without manual file editing.
**Done when:**
- [ ] `context show/set/validate` are available and documented
- [ ] Validation catches oversized/invalid context packs
- [ ] CLI tests cover command behavior and validation errors
