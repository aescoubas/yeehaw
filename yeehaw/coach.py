from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from . import db
from .agents import resolve_command
from .git_repo import GitRepoInfo
from .tmux import attach_session, ensure_session, ensure_window, list_windows, send_text


def _safe_session_name(prefix: str, stem: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem.strip()).strip("-") or "session"
    suffix = uuid.uuid4().hex[:6]
    return f"{prefix}-{slug}-{suffix}"[:60]


def _ensure_window_alive(session_name: str, window_name: str, agent: str) -> None:
    windows = set(list_windows(session_name))
    if window_name in windows:
        return
    raise RuntimeError(
        f"Agent '{agent}' exited before initialization in session '{session_name}'. "
        "Check agent CLI installation/auth configuration."
    )


def _roadmap_coach_prompt(project_name: str, project_root: str, output_path: str, guidelines: str) -> str:
    return (
        "You are a roadmap design assistant.\n"
        "Your job is to interview the user and produce a roadmap markdown file in this exact structure:\n"
        "\n"
        "## 2. Execution Phases\n"
        "\n"
        "### Phase N: <Title>\n"
        "**Status:** TODO\n"
        "**Token Budget:** Low|Medium|High\n"
        "**Prerequisites:** None|Phase X\n"
        "\n"
        "**Objective:**\n"
        "<short objective paragraph>\n"
        "\n"
        "**Tasks:**\n"
        "- [ ] Task item\n"
        "\n"
        "**Verification:**\n"
        "- [ ] Check item\n"
        "\n"
        "---\n"
        "\n"
        f"Project name: {project_name}\n"
        f"Project root: {project_root}\n"
        f"Write the roadmap to: {output_path}\n"
        "\n"
        "Authoring rules:\n"
        "- Ask targeted follow-up questions when details are missing.\n"
        "- Keep phase objectives crisp and implementation-oriented.\n"
        "- Include concrete verification bullets for each phase.\n"
        "- Keep prerequisites coherent and acyclic.\n"
        "- When complete, save the roadmap file at the specified path.\n"
        "\n"
        "Existing project guidelines:\n"
        f"{guidelines.strip() or '(none)'}\n"
    )


def _project_coach_prompt(
    repo: GitRepoInfo,
    name_hint: str | None,
    guidelines_output: str,
    allow_non_git: bool,
) -> str:
    return (
        "You are a project setup assistant for the yeehaw harness.\n"
        "Your job is to interview the user and finalize project identity + guidelines.\n"
        "\n"
        "Context:\n"
        f"- Repository root: {repo.root_path}\n"
        f"- Origin remote: {repo.remote_url or '(none)'}\n"
        f"- Default branch: {repo.default_branch or '(unknown)'}\n"
        f"- HEAD SHA: {repo.head_sha or '(unknown)'}\n"
        f"- Name hint: {name_hint or '(none)'}\n"
        f"- Write guidelines markdown to: {guidelines_output}\n"
        f"- Non-git mode allowed: {allow_non_git}\n"
        "\n"
        "Workflow:\n"
        "1) Ask concise questions to determine the best project name and working conventions.\n"
        "2) Write a practical guidelines markdown file to the target path.\n"
        "3) Run a `yeehaw project create` command to register/update the project.\n"
        "4) Print one final line: PROJECT_REGISTERED <name>\n"
    )


def start_project_coach(
    repo: GitRepoInfo,
    agent: str,
    guidelines_output: str | Path,
    name_hint: str | None = None,
    session_prefix: str = "yeehaw-project-coach",
    attach: bool = True,
    command_override: str | None = None,
    allow_non_git: bool = False,
) -> str:
    session_name = _safe_session_name(session_prefix, name_hint or Path(repo.root_path).name)
    command, warmup_seconds = resolve_command(agent, command_override)

    ensure_session(session_name, repo.root_path)
    ensure_window(session_name, "project-coach", repo.root_path, command)
    target = f"{session_name}:project-coach.0"

    time.sleep(warmup_seconds)
    _ensure_window_alive(session_name, "project-coach", agent)

    out_path = Path(guidelines_output)
    if not out_path.is_absolute():
        out_path = (Path(repo.root_path) / out_path).resolve()

    prompt = _project_coach_prompt(
        repo=repo,
        name_hint=name_hint,
        guidelines_output=str(out_path),
        allow_non_git=allow_non_git,
    )
    send_text(target, prompt, press_enter=True)
    time.sleep(0.25)
    _ensure_window_alive(session_name, "project-coach", agent)

    if attach:
        attach_session(session_name)

    return session_name


def start_roadmap_coach(
    project_name: str,
    output_path: str | Path,
    agent: str,
    db_path: str | Path | None = None,
    session_prefix: str = "yeehaw-coach",
    attach: bool = True,
    command_override: str | None = None,
) -> str:
    conn = db.connect(db_path)
    project = db.get_project(conn, project_name)
    if project is None:
        raise ValueError(f"Project '{project_name}' not found. Create it first with 'yeehaw project create'.")

    project_root = str(project["root_path"])
    session_name = _safe_session_name(session_prefix, project_name)

    command, warmup_seconds = resolve_command(agent, command_override)
    ensure_session(session_name, project_root)
    ensure_window(session_name, "coach", project_root, command)
    target = f"{session_name}:coach.0"

    time.sleep(warmup_seconds)
    _ensure_window_alive(session_name, "coach", agent)

    out_path = Path(output_path)
    if not out_path.is_absolute():
        out_path = (Path(project_root) / out_path).resolve()

    prompt = _roadmap_coach_prompt(
        project_name=str(project["name"]),
        project_root=project_root,
        output_path=str(out_path),
        guidelines=str(project["guidelines"]),
    )
    send_text(target, prompt, press_enter=True)
    time.sleep(0.25)
    _ensure_window_alive(session_name, "coach", agent)

    if attach:
        attach_session(session_name)

    return session_name
