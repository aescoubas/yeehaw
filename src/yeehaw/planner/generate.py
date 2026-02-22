"""Natural-language roadmap generation via planner agents over MCP."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from yeehaw.store.store import Store


@dataclass(frozen=True)
class AgentRunResult:
    """Result of a planner agent subprocess execution."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RoadmapGenerationResult:
    """Outcome of a roadmap generation request."""

    success: bool
    message: str
    roadmap_id: int | None
    phases: int
    tasks: int
    stdout: str
    stderr: str


def generate_roadmap_from_prompt(
    db_path: Path,
    project_name: str,
    prompt_text: str,
    agent: str = "codex",
    timeout_sec: int = 300,
) -> RoadmapGenerationResult:
    """Generate and persist a roadmap from natural-language text."""
    store = Store(db_path)
    try:
        project = store.get_project(project_name)
        if not project:
            return RoadmapGenerationResult(
                success=False,
                message=f"Project '{project_name}' not found",
                roadmap_id=None,
                phases=0,
                tasks=0,
                stdout="",
                stderr="",
            )
        before = store.get_active_roadmap(project["id"])
        before_id = int(before["id"]) if before else None
    finally:
        store.close()

    prompt = _build_generation_prompt(project_name, prompt_text)
    run_result = _run_agent_prompt(agent, db_path, prompt, timeout_sec)
    if run_result.returncode != 0:
        return RoadmapGenerationResult(
            success=False,
            message=f"Agent exited with code {run_result.returncode}",
            roadmap_id=None,
            phases=0,
            tasks=0,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
        )

    store = Store(db_path)
    try:
        project = store.get_project(project_name)
        assert project is not None
        after = store.get_active_roadmap(project["id"])
        if after is None:
            return RoadmapGenerationResult(
                success=False,
                message="Agent finished but no active roadmap exists for the project",
                roadmap_id=None,
                phases=0,
                tasks=0,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
            )

        after_id = int(after["id"])
        if before_id is not None and after_id == before_id:
            return RoadmapGenerationResult(
                success=False,
                message="Agent did not create a new roadmap",
                roadmap_id=None,
                phases=0,
                tasks=0,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
            )

        phases = store.list_phases(after_id)
        tasks = sum(len(store.list_tasks_by_phase(phase["id"])) for phase in phases)
        return RoadmapGenerationResult(
            success=True,
            message="Roadmap generated",
            roadmap_id=after_id,
            phases=len(phases),
            tasks=tasks,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
        )
    finally:
        store.close()


def _build_generation_prompt(project_name: str, user_text: str) -> str:
    """Build strict planning prompt for the selected project."""
    return "\n".join(
        [
            "You are a project planner connected to the yeehaw MCP server.",
            f"Generate a roadmap for project '{project_name}' from the user's natural-language request.",
            "",
            "Requirements:",
            "- Use MCP tools (not plain text-only output) to persist data.",
            f"- Call create_roadmap(project_name='{project_name}', markdown=<roadmap markdown>).",
            "- The markdown must use exactly this structure:",
            "  # Roadmap: <project-name>",
            "  ## Phase N: <title>",
            "  **Verify:** `<command>` (optional)",
            "  ### Task N.M: <title>",
            "  <description>",
            "- Number phases sequentially starting at 1.",
            "- Number tasks sequentially per phase as N.1, N.2, ...",
            "- Keep each task concrete and implementation-oriented.",
            "",
            "After calling create_roadmap, return a concise summary.",
            "",
            "User request:",
            user_text.strip(),
        ]
    )


def _run_agent_prompt(
    agent: str,
    db_path: Path,
    prompt: str,
    timeout_sec: int,
) -> AgentRunResult:
    """Run a single-shot planning prompt via selected agent."""
    if agent == "claude":
        return _run_claude_prompt(db_path, prompt, timeout_sec)
    if agent == "codex":
        return _run_codex_prompt(db_path, prompt, timeout_sec)
    if agent == "gemini":
        return _run_gemini_prompt(db_path, prompt, timeout_sec)
    return AgentRunResult(returncode=2, stdout="", stderr=f"Unsupported agent: {agent}")


def _run_claude_prompt(db_path: Path, prompt: str, timeout_sec: int) -> AgentRunResult:
    config_payload = {
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
        json.dump(config_payload, temp_file)
        config_path = temp_file.name

    try:
        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "--mcp-config",
            config_path,
            "-p",
            prompt,
        ]
        return _run_command(cmd, timeout_sec)
    finally:
        Path(config_path).unlink(missing_ok=True)


def _run_codex_prompt(db_path: Path, prompt: str, timeout_sec: int) -> AgentRunResult:
    args = ["-m", "yeehaw.mcp.server", "--db", str(db_path)]
    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-c",
        f"mcp_servers.yeehaw.command={json.dumps(sys.executable)}",
        "-c",
        f"mcp_servers.yeehaw.args={json.dumps(args)}",
        prompt,
    ]
    return _run_command(cmd, timeout_sec)


def _run_gemini_prompt(db_path: Path, prompt: str, timeout_sec: int) -> AgentRunResult:
    server_name = f"yeehaw-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="yeehaw-gemini-") as temp_dir:
        cwd = Path(temp_dir)
        add_cmd = [
            "gemini",
            "mcp",
            "add",
            server_name,
            sys.executable,
            "-m",
            "yeehaw.mcp.server",
            "--db",
            str(db_path),
        ]
        added = _run_command(add_cmd, timeout_sec=30, cwd=cwd)
        if added.returncode != 0:
            return AgentRunResult(
                returncode=added.returncode,
                stdout=added.stdout,
                stderr=f"Failed to register Gemini MCP server: {added.stderr}",
            )

        run_cmd = [
            "gemini",
            "-p",
            prompt,
            "--allowed-mcp-server-names",
            server_name,
            "-y",
            "--output-format",
            "text",
        ]
        try:
            result = _run_command(run_cmd, timeout_sec=timeout_sec, cwd=cwd)
        finally:
            remove_cmd = ["gemini", "mcp", "remove", server_name]
            removed = _run_command(remove_cmd, timeout_sec=30, cwd=cwd)
            if removed.returncode != 0:
                result = AgentRunResult(
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=f"{result.stderr}\nMCP cleanup warning: {removed.stderr}".strip(),
                )

        return result


def _run_command(
    cmd: list[str],
    timeout_sec: int,
    cwd: Path | None = None,
) -> AgentRunResult:
    """Run subprocess and normalize timeout/not-found handling."""
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
            check=False,
        )
        return AgentRunResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except FileNotFoundError as exc:
        return AgentRunResult(
            returncode=127,
            stdout="",
            stderr=str(exc),
        )
    except subprocess.TimeoutExpired as exc:
        return AgentRunResult(
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout_sec} seconds",
        )
