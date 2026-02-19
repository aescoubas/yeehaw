from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import db
from .agents import resolve_command
from .roadmap import RoadmapDef, StageDef, TrackDef, load_roadmap, render_stage_prompt
from .tmux import TmuxError, capture_pane, ensure_session, ensure_window, send_text


QUESTION_RE = re.compile(r"^Question:\s*(.+)$", re.IGNORECASE)
SUMMARY_RE = re.compile(r"^Summary:\s*$", re.IGNORECASE)
ARTIFACTS_RE = re.compile(r"^Artifacts:\s*$", re.IGNORECASE)


@dataclass(slots=True)
class TrackRuntime:
    track: TrackDef
    track_run_id: int
    target: str
    stage_index: int = 0
    mode: str = "ready"  # ready|waiting|awaiting_input|completed|failed
    stage_run_id: int | None = None
    done_marker: str = ""
    input_marker: str = ""
    baseline_done_count: int = 0
    baseline_input_count: int = 0
    deadline_monotonic: float = 0.0


def _safe_session_name(prefix: str, project_name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_name.strip()).strip("-") or "project"
    suffix = uuid.uuid4().hex[:6]
    return f"{prefix}-{stem}-{suffix}"[:60]


def _parse_summary_and_artifacts(pane_text: str, done_marker: str) -> tuple[str, str]:
    marker_pos = pane_text.rfind(done_marker)
    if marker_pos == -1:
        return "", ""

    tail = pane_text[marker_pos + len(done_marker) :].strip()
    lines = [line.rstrip() for line in tail.splitlines()]

    in_summary = False
    in_artifacts = False
    summary_lines: list[str] = []
    artifact_lines: list[str] = []

    for line in lines:
        if SUMMARY_RE.match(line):
            in_summary = True
            in_artifacts = False
            continue
        if ARTIFACTS_RE.match(line):
            in_artifacts = True
            in_summary = False
            continue

        if in_summary:
            if line:
                summary_lines.append(line)
        elif in_artifacts:
            if line:
                artifact_lines.append(line)

        if len(summary_lines) >= 8 and len(artifact_lines) >= 20:
            break

    summary = "\n".join(summary_lines[:8]).strip()
    artifacts = "\n".join(artifact_lines[:20]).strip()
    return summary, artifacts


def _extract_question(pane_text: str, input_marker: str) -> str:
    marker_pos = pane_text.rfind(input_marker)
    if marker_pos == -1:
        return ""
    tail = pane_text[marker_pos + len(input_marker) :].strip()
    for line in tail.splitlines()[:40]:
        match = QUESTION_RE.match(line.strip())
        if match:
            return match.group(1).strip()
    return "Agent requested input (no structured question found)."


def _marker_followed_by(pane_text: str, marker: str, expected_prefix: str) -> bool:
    marker_pos = pane_text.rfind(marker)
    if marker_pos == -1:
        return False
    tail = pane_text[marker_pos + len(marker) :]
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.lower().startswith(expected_prefix.lower())
    return False


def _state_terminal(mode: str) -> bool:
    return mode in {"completed", "awaiting_input", "failed"}


def run_roadmap(
    project_name: str,
    roadmap_path: str | Path,
    db_path: str | Path | None = None,
    default_agent: str | None = None,
    poll_seconds: float = 2.0,
    session_prefix: str = "yeehaw",
) -> int:
    conn = db.connect(db_path)
    project = db.get_project(conn, project_name)
    if project is None:
        raise ValueError(f"Project '{project_name}' not found. Create it first with 'yeehaw project create'.")

    roadmap = load_roadmap(roadmap_path, default_agent=default_agent)
    roadmap_id = db.insert_roadmap(conn, int(project["id"]), roadmap)

    tmux_session = _safe_session_name(session_prefix, project_name)
    run_id = db.create_run(conn, int(project["id"]), roadmap_id, tmux_session)
    db.add_event(conn, run_id, "info", f"Run created with session {tmux_session}")

    runtimes: list[TrackRuntime] = []
    project_root = str(project["root_path"])
    project_guidelines = str(project["guidelines"] or "")
    repo_meta: list[str] = []
    if "git_remote_url" in project.keys() and project["git_remote_url"]:
        repo_meta.append(f"- Remote: {project['git_remote_url']}")
    if "default_branch" in project.keys() and project["default_branch"]:
        repo_meta.append(f"- Default branch: {project['default_branch']}")
    if "head_sha" in project.keys() and project["head_sha"]:
        repo_meta.append(f"- HEAD: {project['head_sha']}")
    if repo_meta:
        project_guidelines = (
            (project_guidelines.strip() + "\n\n" if project_guidelines.strip() else "")
            + "Repository metadata:\n"
            + "\n".join(repo_meta)
        )

    try:
        ensure_session(tmux_session, project_root)

        for track in roadmap.tracks:
            window_name = track.id
            command, warmup_seconds = resolve_command(track.agent, track.command)

            track_run_id = db.create_track_run(conn, run_id, track, window_name)
            target = f"{tmux_session}:{window_name}.0"
            ensure_window(tmux_session, window_name, project_root, command)
            db.add_event(
                conn,
                run_id,
                "info",
                f"Track {track.id} launched in {target} with command: {command}",
                track_run_id=track_run_id,
            )

            # Let each CLI initialize before sending the first prompt.
            time.sleep(warmup_seconds)
            runtimes.append(TrackRuntime(track=track, track_run_id=track_run_id, target=target))
            db.set_track_run_state(conn, track_run_id, status="ready", current_stage_index=0)

        while True:
            active_count = 0

            for runtime in runtimes:
                if runtime.mode in {"completed", "failed", "awaiting_input"}:
                    continue

                if runtime.mode == "ready":
                    if runtime.stage_index >= len(runtime.track.stages):
                        runtime.mode = "completed"
                        db.set_track_run_state(
                            conn,
                            runtime.track_run_id,
                            status="completed",
                            current_stage_index=runtime.stage_index,
                        )
                        db.add_event(
                            conn,
                            run_id,
                            "info",
                            f"Track {runtime.track.id} completed",
                            track_run_id=runtime.track_run_id,
                        )
                        continue

                    stage = runtime.track.stages[runtime.stage_index]
                    token = uuid.uuid4().hex[:12]
                    done_marker = f"[[YEEHAW_DONE {token}]]"
                    input_marker = f"[[YEEHAW_NEEDS_INPUT {token}]]"

                    prior_summaries = db.get_stage_summaries(conn, runtime.track_run_id)
                    prompt = render_stage_prompt(
                        project_name=str(project["name"]),
                        project_root=project_root,
                        global_guidelines=project_guidelines,
                        roadmap=roadmap,
                        track=runtime.track,
                        stage=stage,
                        prior_summaries=prior_summaries,
                        done_marker=done_marker,
                        input_marker=input_marker,
                    )

                    send_text(runtime.target, prompt, press_enter=True)
                    time.sleep(0.4)
                    pane = capture_pane(runtime.target)

                    runtime.done_marker = done_marker
                    runtime.input_marker = input_marker
                    runtime.baseline_done_count = pane.count(done_marker)
                    runtime.baseline_input_count = pane.count(input_marker)
                    runtime.deadline_monotonic = time.monotonic() + (stage.timeout_minutes * 60)
                    runtime.mode = "waiting"

                    runtime.stage_run_id = db.create_stage_run(
                        conn,
                        runtime.track_run_id,
                        stage,
                        token,
                        runtime.baseline_done_count,
                        runtime.baseline_input_count,
                    )
                    db.set_track_run_state(
                        conn,
                        runtime.track_run_id,
                        status="in_progress",
                        current_stage_index=runtime.stage_index,
                    )
                    db.add_event(
                        conn,
                        run_id,
                        "info",
                        f"Track {runtime.track.id}: stage {stage.id} started",
                        track_run_id=runtime.track_run_id,
                        stage_run_id=runtime.stage_run_id,
                    )
                    active_count += 1
                    continue

                if runtime.mode == "waiting":
                    active_count += 1
                    pane = capture_pane(runtime.target)
                    if (
                        pane.count(runtime.input_marker) > runtime.baseline_input_count
                        or _marker_followed_by(pane, runtime.input_marker, "Question:")
                    ):
                        question = _extract_question(pane, runtime.input_marker)
                        runtime.mode = "awaiting_input"

                        if runtime.stage_run_id is not None:
                            db.set_stage_run_awaiting_input(conn, runtime.stage_run_id, pane_snapshot=pane[-8000:])
                        db.set_track_run_state(
                            conn,
                            runtime.track_run_id,
                            status="awaiting_input",
                            current_stage_index=runtime.stage_index,
                            waiting_question=question,
                            last_pane=pane[-4000:],
                        )
                        db.add_event(
                            conn,
                            run_id,
                            "warn",
                            f"Track {runtime.track.id}: stage requested input: {question}",
                            track_run_id=runtime.track_run_id,
                            stage_run_id=runtime.stage_run_id,
                        )
                        continue

                    if (
                        pane.count(runtime.done_marker) > runtime.baseline_done_count
                        or _marker_followed_by(pane, runtime.done_marker, "Summary:")
                    ):
                        summary, artifacts = _parse_summary_and_artifacts(pane, runtime.done_marker)
                        if runtime.stage_run_id is not None:
                            db.complete_stage_run(
                                conn,
                                runtime.stage_run_id,
                                status="completed",
                                summary=summary,
                                artifacts=artifacts,
                                pane_snapshot=pane[-8000:],
                            )

                        stage = runtime.track.stages[runtime.stage_index]
                        db.add_event(
                            conn,
                            run_id,
                            "info",
                            f"Track {runtime.track.id}: stage {stage.id} completed",
                            track_run_id=runtime.track_run_id,
                            stage_run_id=runtime.stage_run_id,
                        )
                        runtime.stage_index += 1
                        runtime.mode = "ready"
                        runtime.stage_run_id = None
                        db.set_track_run_state(
                            conn,
                            runtime.track_run_id,
                            status="ready",
                            current_stage_index=runtime.stage_index,
                            waiting_question="",
                        )
                        continue

                    if time.monotonic() > runtime.deadline_monotonic:
                        runtime.mode = "failed"
                        stage = runtime.track.stages[runtime.stage_index]
                        timeout_summary = f"Timed out after {stage.timeout_minutes} minutes"
                        if runtime.stage_run_id is not None:
                            db.complete_stage_run(
                                conn,
                                runtime.stage_run_id,
                                status="failed",
                                summary=timeout_summary,
                                artifacts="",
                                pane_snapshot=pane[-8000:],
                            )
                        db.set_track_run_state(
                            conn,
                            runtime.track_run_id,
                            status="failed",
                            current_stage_index=runtime.stage_index,
                            waiting_question="",
                            last_pane=pane[-4000:],
                        )
                        db.add_event(
                            conn,
                            run_id,
                            "error",
                            f"Track {runtime.track.id}: stage {stage.id} timed out",
                            track_run_id=runtime.track_run_id,
                            stage_run_id=runtime.stage_run_id,
                        )

            all_terminal = all(_state_terminal(rt.mode) for rt in runtimes)
            any_failed = any(rt.mode == "failed" for rt in runtimes)
            any_awaiting = any(rt.mode == "awaiting_input" for rt in runtimes)

            if all_terminal:
                if any_failed:
                    db.set_run_status(conn, run_id, "failed", finished=True)
                    db.add_event(conn, run_id, "error", "Run finished with failures")
                elif any_awaiting:
                    db.set_run_status(conn, run_id, "awaiting_input", finished=False)
                    db.add_event(conn, run_id, "warn", "Run paused waiting for user input")
                else:
                    db.set_run_status(conn, run_id, "completed", finished=True)
                    db.add_event(conn, run_id, "info", "Run completed successfully")
                break

            time.sleep(max(poll_seconds, 0.5))

    except (TmuxError, Exception) as exc:
        db.add_event(conn, run_id, "error", f"Run failed: {exc}")
        db.set_run_status(conn, run_id, "failed", finished=True)
        raise

    return run_id
