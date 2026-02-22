"""Planner session launcher for AI agent + yeehaw MCP connectivity."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def start_planner_session(
    db_path: Path,
    briefing_file: Path | None = None,
    agent: str = "codex",
    project_name: str | None = None,
) -> None:
    """Start interactive planner session attached to yeehaw MCP tools."""
    prompt = _build_planner_prompt(project_name=project_name, briefing_file=briefing_file)

    if agent == "claude":
        _start_claude_session(db_path=db_path, prompt=prompt)
        return
    if agent == "codex":
        _start_codex_session(db_path=db_path, prompt=prompt)
        return
    if agent == "gemini":
        _start_gemini_session(db_path=db_path, prompt=prompt)
        return

    raise ValueError(f"Unsupported planner agent: {agent}")


def _build_planner_prompt(project_name: str | None, briefing_file: Path | None) -> str:
    """Compose initial planner prompt for interactive roadmap conversations."""
    prompt_parts = [
        "You are a project planning assistant connected to the yeehaw MCP server.",
        "Have an interactive planning conversation with the human.",
        "",
        "Available MCP tools include: create_project, get_roadmap, create_roadmap,",
        "edit_roadmap, preview_roadmap, list_projects, list_tasks, get_project_status,",
        "approve_roadmap, pause_task, resume_task, update_task.",
        "",
        "Your objective:",
        "1) Ask clarifying questions until requirements are concrete.",
        "2) Draft a phased implementation roadmap in verbose task format.",
        "3) During discussion, call preview_roadmap(markdown=<draft>, color=True)",
        "   and show the returned preview so the human sees a colorized roadmap draft.",
        "   When you send an 'Updated colorized roadmap preview', print the full preview",
        "   field from preview_roadmap verbatim (all task metadata/details/checklists),",
        "   and never replace it with a summarized outline.",
        "4) After user confirmation, persist with create_roadmap (new roadmap)",
        "   or edit_roadmap (update current active roadmap in place).",
        "5) After persisting, call get_roadmap(project_name=<project>, color=True)",
        "   and show the full returned preview verbatim for final confirmation.",
        "",
        "Roadmap markdown format supported by yeehaw:",
        "# Roadmap: <project-name>",
        "## Phase N: <title>",
        "**Verify:** `<command>` (optional)",
        "### Task N.M: <title>  (or `### P0.1: <title>`)",
        "**Depends on:** <none|task refs>",
        "**Repo:** <repo-name>",
        "**Files:**",
        "- `<path>` — <change summary>",
        "**Description:**",
        "<implementation details>",
        "**Done when:**",
        "- [ ] <acceptance criterion>",
        "",
        "Phases may start at 0 or 1 and must remain sequential.",
        "Tasks per phase must be sequential for that phase (N.1, N.2, ...).",
    ]

    if project_name:
        prompt_parts.extend(
            [
                "",
                f"Target project: '{project_name}'.",
                "Do not create a new project; create/update roadmap for this project.",
                (
                    f"When persisting, call create_roadmap(project_name='{project_name}', markdown=...) "
                    f"for first creation, or edit_roadmap(project_name='{project_name}', markdown=...) "
                    "to update the active roadmap."
                ),
            ]
        )

    if briefing_file and briefing_file.exists():
        content = briefing_file.read_text()
        prompt_parts.extend(["", "## Initial Briefing", "", content])

    return "\n".join(prompt_parts)


def _start_claude_session(db_path: Path, prompt: str) -> None:
    """Launch interactive Claude session with transient MCP config."""
    mcp_config = {
        "mcpServers": {
            "yeehaw": {
                "command": sys.executable,
                "args": ["-m", "yeehaw.mcp.server", "--db", str(db_path)],
            }
        }
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="yeehaw-mcp-",
        delete=False,
    ) as temp_file:
        json.dump(mcp_config, temp_file)
        config_path = temp_file.name

    try:
        proc = subprocess.run(
            [
                "claude",
                "--dangerously-skip-permissions",
                "--mcp-config",
                config_path,
                prompt,
            ],
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Claude planner session exited with code {proc.returncode}")
    finally:
        Path(config_path).unlink(missing_ok=True)


def _start_codex_session(db_path: Path, prompt: str) -> None:
    """Launch interactive Codex session with runtime MCP server override."""
    mcp_args = ["-m", "yeehaw.mcp.server", "--db", str(db_path)]
    proc = subprocess.run(
        [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c",
            f"mcp_servers.yeehaw.command={json.dumps(sys.executable)}",
            "-c",
            f"mcp_servers.yeehaw.args={json.dumps(mcp_args)}",
            prompt,
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Codex planner session exited with code {proc.returncode}")


def _start_gemini_session(db_path: Path, prompt: str) -> None:
    """Launch interactive Gemini session with temporary MCP registration."""
    server_name = f"yeehaw-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="yeehaw-gemini-") as temp_dir:
        session_dir = Path(temp_dir)
        add_proc = subprocess.run(
            [
                "gemini",
                "mcp",
                "add",
                server_name,
                sys.executable,
                "-m",
                "yeehaw.mcp.server",
                "--db",
                str(db_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=session_dir,
        )
        if add_proc.returncode != 0:
            details = add_proc.stderr.strip() or add_proc.stdout.strip()
            raise RuntimeError(f"Failed to configure Gemini MCP server: {details}")

        try:
            proc = subprocess.run(
                [
                    "gemini",
                    "--prompt-interactive",
                    prompt,
                    "--allowed-mcp-server-names",
                    server_name,
                    "--include-directories",
                    str(Path.cwd()),
                ],
                check=False,
                cwd=session_dir,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Gemini planner session exited with code {proc.returncode}")
        finally:
            subprocess.run(
                ["gemini", "mcp", "remove", server_name],
                capture_output=True,
                text=True,
                check=False,
                cwd=session_dir,
            )
