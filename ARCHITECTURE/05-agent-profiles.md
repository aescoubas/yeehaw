# 05 - Agent Profiles

Each coding agent has a profile defining how to invoke it.

## Supported Agents

| Agent | CLI Command | Prompt Flag |
|-------|-----------|-------------|
| Claude Code | `claude` | `--dangerously-skip-permissions -p` |
| Gemini CLI | `gemini` | `-p` |
| Codex | `codex` | `--prompt` |

## Profile Fields

- `Name` - identifier (e.g., "claude")
- `Command` - base CLI command
- `PromptFlag` - how to pass the prompt
- `RequiresTmux` - whether to run in tmux (all do currently)
- `TimeoutMinutes` - default timeout for this agent

## Task Prompt Template

The prompt includes:
1. Task description from the roadmap
2. Signal directory path and protocol instructions
3. Verification command (if any)
4. Previous attempt failure output (if retry)
