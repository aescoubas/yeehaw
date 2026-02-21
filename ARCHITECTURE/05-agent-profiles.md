# 05 — Agent Profiles

## Supported Agents

| Agent | Command | Prompt Flag | Default Timeout |
|-------|---------|-------------|-----------------|
| Claude Code | `claude` | `--dangerously-skip-permissions -p` | 60 min |
| Gemini CLI | `gemini` | `-p` | 60 min |
| Codex | `codex` | `--prompt` | 60 min |

## Profile Definition

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class AgentProfile:
    name: str
    command: str
    prompt_flag: str
    timeout_minutes: int = 60

AGENT_REGISTRY: dict[str, AgentProfile] = {
    "claude": AgentProfile(name="claude", command="claude",
                           prompt_flag="--dangerously-skip-permissions -p"),
    "gemini": AgentProfile(name="gemini", command="gemini", prompt_flag="-p"),
    "codex":  AgentProfile(name="codex", command="codex", prompt_flag="--prompt"),
}
```

## Agent Selection

1. **Explicit assignment** — `assigned_agent` set by Planner or user
2. **Project default** — configurable per-project default
3. **Global default** — falls back to `"claude"`

## Task Prompt Construction

```python
def build_task_prompt(task: dict, signal_dir: str, previous_failure: str | None = None) -> str:
    parts = [
        f"# Task {task['task_number']}: {task['title']}",
        "", task["description"], "",
        "## Signal Protocol",
        f"When finished, write `{signal_dir}/signal.json`:",
        '```json', '{',
        f'  "task_id": {task["id"]},',
        '  "status": "done",',
        '  "summary": "what you did",',
        '  "artifacts": ["files", "changed"],',
        '  "timestamp": "ISO-8601"',
        '}', '```', "",
        'Status must be "done", "failed", or "blocked".',
    ]
    if previous_failure:
        parts.extend(["", "## Previous Attempt Failed",
                       f"Failure reason: {previous_failure}",
                       "Fix the issues and try again."])
    return "\n".join(parts)
```

## Shell Command Construction

```python
import shlex

def build_launch_command(profile: AgentProfile, prompt: str) -> str:
    return f"{profile.command} {profile.prompt_flag} {shlex.quote(prompt)}"
```

## Launcher Script (for long prompts)

```python
def write_launcher(script_path: Path, profile: AgentProfile, prompt: str) -> None:
    script_path.write_text(
        f"#!/bin/bash\nexec {profile.command} {profile.prompt_flag} "
        f"\"$(cat <<'YEEHAW_PROMPT_EOF'\n{prompt}\nYEEHAW_PROMPT_EOF\n)\"\n"
    )
    script_path.chmod(0o755)
```
