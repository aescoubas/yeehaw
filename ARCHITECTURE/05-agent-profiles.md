# 05 — Agent Profiles and Worker Runtime Config

## Supported Worker Agents

| Agent | Command Template |
|---|---|
| `claude` | `claude --dangerously-skip-permissions -p "<prompt>"` |
| `gemini` | `gemini -p "<prompt>"` |
| `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox "<prompt>"` |

Registry is defined in `src/yeehaw/agent/profiles.py`.

- default worker agent: `claude`
- unknown agent names raise `ValueError`

## Prompt Construction

`build_task_prompt(...)` includes:

- task header + task description
- completion requirements:
  - commit changes
  - `git status --porcelain` must be clean before signaling `done`
- required signal file format and statuses
- previous failure context on retries

To preserve instructions when context grows, orchestrator writes the prompt to:

`<runtime_root>/signals/task-<id>/task-<id>-prompt.md`

and injects `YEEHAW_TASK_PROMPT_FILE` for workers.

## Launcher Script

`write_launcher(...)` writes a per-attempt script:

- exports configured env vars
- embeds prompt with heredoc
- executes profile command + args

The script is run inside task worktree via tmux.

## Worker Runtime Configuration (`workers.json`)

Resolved from:

`<runtime_root>/workers.json`

Schema:

- `disable_default_mcp: bool` (default `true`)
- `extra_args: string[]`
- `env: {string: string}`
- `agents.<name>.{disable_default_mcp, extra_args, env}` overrides

Resolution:

1. start from global config
2. apply per-agent override
3. concatenate args: global first, then per-agent
4. merge env map: per-agent keys win

## Default MCP Hardening for Workers

When `disable_default_mcp=true`, Yeehaw injects agent-specific args to disable any
preconfigured MCP servers by default:

- Claude: strict empty MCP config
- Gemini: sentinel allow-list server name
- Codex: disables each discovered configured MCP server via `-c` overrides

This keeps worker execution isolated unless explicitly opted in by runtime config.
