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
) -> str:
    """Build full worker prompt including mandatory signal protocol."""
    parts = [
        f"# Task {task['task_number']}: {task['title']}",
        "",
        task["description"],
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
    return f"{profile.command} {profile.prompt_flag} {shlex.quote(prompt)}"


def write_launcher(script_path: Path, profile: AgentProfile, prompt: str) -> None:
    """Write a launcher script that feeds long prompts via heredoc."""
    script_path.write_text(
        "#!/bin/bash\n"
        f"exec {profile.command} {profile.prompt_flag} "
        f"\"$(cat <<'YEEHAW_PROMPT_EOF'\n{prompt}\nYEEHAW_PROMPT_EOF\n)\"\n",
    )
    script_path.chmod(0o755)


def _iso_now_placeholder() -> str:
    """Return template placeholder for ISO timestamp in prompts."""
    return "ISO-8601-timestamp-here"
