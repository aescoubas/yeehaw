"""Planner session launcher for AI agent + yeehaw MCP connectivity."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def start_planner_session(
    db_path: Path,
    briefing_file: Path | None = None,
    agent: str = "claude",
) -> None:
    """Start planner session attached to yeehaw MCP server."""
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
        prompt_parts = [
            "You are a project planner connected to the yeehaw task management system.",
            "You have access to MCP tools: create_project, create_roadmap, list_projects,",
            "list_tasks, get_project_status, approve_roadmap, update_task.",
            "",
            "Your job is to translate the human's briefing into structured projects and roadmaps.",
            "Use the create_roadmap tool with properly formatted markdown.",
            "",
            "Roadmap format:",
            "# Roadmap: project-name",
            "## Phase N: title",
            "**Verify:** `command`",
            "### Task N.M: title",
            "Description...",
        ]

        if briefing_file and briefing_file.exists():
            content = briefing_file.read_text()
            prompt_parts.extend(["", "## Briefing", "", content])

        prompt = "\n".join(prompt_parts)

        if agent == "claude":
            cmd = ["claude", "--mcp-config", config_path, "-p", prompt]
        elif agent == "gemini":
            cmd = ["gemini", "-p", prompt]
        else:
            raise ValueError(f"Unsupported planner agent: {agent}")

        os.execvp(cmd[0], cmd)

    finally:
        os.unlink(config_path)
