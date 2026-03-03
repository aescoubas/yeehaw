"""Task prompt construction and agent launch command building."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from yeehaw.agent.profiles import AgentProfile


def build_task_prompt(
    task: dict[str, Any],
    signal_dir: str,
    previous_failure: str | None = None,
    prompt_file: str | None = None,
    project_context: str | None = None,
) -> str:
    """Build full worker prompt including mandatory signal protocol."""
    parts = [f"# Task {task['task_number']}: {task['title']}"]

    normalized_project_context = project_context.strip() if isinstance(project_context, str) else ""
    if normalized_project_context:
        parts.extend(
            [
                "",
                "## Project Memory Pack",
                "",
                "Apply these project conventions before following task-specific instructions.",
                "",
                normalized_project_context,
            ]
        )

    parts.extend(["", task["description"]])

    if prompt_file:
        parts.extend(
            [
                "",
                "## Persistent Task Context",
                "",
                (
                    "The complete task prompt is saved at "
                    f"`{prompt_file}`. If context gets long, reopen this file to refresh "
                    "the original instructions."
                ),
            ]
        )

    parts.extend(
        [
            "",
            "## Completion Requirements",
            "",
            "Before writing the signal file, you MUST:",
            "",
            "1. Stage and commit your task changes on the current task branch.",
            "2. Confirm the worktree is clean by running:",
            "   `git status --porcelain`",
            "3. Ensure that command returns no output.",
            "",
            "If you cannot satisfy these requirements, set signal `status` to `\"failed\"`",
            "or `\"blocked\"` with a clear summary instead of reporting done.",
        ]
    )

    parts.extend(
        [
            "",
            "## Signal Protocol",
            "",
            (
                "When you are finished, create the file "
                f"`{signal_dir}/signal.json` with this format:"
            ),
            "",
            "```json",
            "{",
            f'  "task_id": {task["id"]},',
            '  "status": "done",',
            '  "summary": "Brief description of what you did",',
            '  "artifacts": ["list", "of", "key", "files", "changed"],',
            f'  "timestamp": "{_iso_now_placeholder()}"',
            "}",
            "```",
            "",
            'Set `status` to `"done"` on success, `"failed"` if you cannot complete the task,',
            'or `"blocked"` if you need external input.',
            "",
            "**This signal file is mandatory.** Without it, your task will time out.",
        ]
    )

    if previous_failure:
        parts.extend(
            [
                "",
                "## Previous Attempt Failed",
                "",
                f"The previous attempt failed with: {previous_failure}",
                "",
                "Please fix the issues and try again.",
            ]
        )

    return "\n".join(parts)


def build_launch_command(profile: AgentProfile, prompt: str) -> str:
    """Build shell-safe agent launch command."""
    parts = [profile.command]
    if profile.prompt_flag:
        parts.append(profile.prompt_flag)
    parts.append(shlex.quote(prompt))
    return " ".join(parts)


def write_launcher(
    script_path: Path,
    profile: AgentProfile,
    prompt: str,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Write a launcher script that feeds long prompts via heredoc."""
    extra_args = extra_args or []
    env = env or {}
    delimiter = _heredoc_delimiter(prompt)
    command_parts = [
        *shlex.split(profile.command),
        *extra_args,
        *shlex.split(profile.prompt_flag),
    ]
    quoted_cmd = " ".join(shlex.quote(part) for part in command_parts)
    env_exports = "".join(
        f"export {key}={shlex.quote(value)}\n"
        for key, value in sorted(env.items())
    )
    script_path.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"{env_exports}"
        f"PROMPT=\"$(cat <<'{delimiter}'\n{prompt}\n{delimiter}\n)\"\n"
        f"exec {quoted_cmd} \"$PROMPT\"\n",
    )
    script_path.chmod(0o755)


def _iso_now_placeholder() -> str:
    """Return template placeholder for ISO timestamp in prompts."""
    return "ISO-8601-timestamp-here"


def _heredoc_delimiter(prompt: str) -> str:
    """Generate a delimiter that is guaranteed not to occur in prompt."""
    base = "YEEHAW_PROMPT_EOF"
    delimiter = base
    suffix = 0
    while delimiter in prompt:
        suffix += 1
        delimiter = f"{base}_{suffix}"
    return delimiter
