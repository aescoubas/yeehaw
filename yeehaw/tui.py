from __future__ import annotations

import curses
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import db
from .coach import start_roadmap_coach
from .git_repo import GitRepoError, detect_repo
from .orchestrator import GlobalScheduler, create_batch_from_task_list
from .roadmap import RoadmapValidationError, load_roadmap
from .tmux import TmuxError, capture_pane, kill_session, list_windows, send_text


FOCUS_PROJECTS = "projects"
FOCUS_RUNS = "runs"


def _trim(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _safe_add(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    max_w = w - x - 1
    if max_w <= 0:
        return
    try:
        win.addnstr(y, x, _trim(text, max_w), max_w, attr)
    except curses.error:
        return


def _new_panel(stdscr: curses.window, y: int, x: int, h: int, w: int, title: str, border_attr: int) -> curses.window | None:
    if h < 3 or w < 8:
        return None
    panel = stdscr.derwin(h, w, y, x)
    panel.erase()
    try:
        panel.attron(border_attr)
        panel.box()
        panel.attroff(border_attr)
    except curses.error:
        pass
    _safe_add(panel, 0, 2, f" {title} ", border_attr | curses.A_BOLD)
    return panel


def _init_colors() -> dict[str, int]:
    if not curses.has_colors():
        return {
            "header": curses.A_BOLD,
            "border": curses.A_NORMAL,
            "selected": curses.A_REVERSE,
            "focused": curses.A_BOLD,
            "muted": curses.A_DIM,
            "running": curses.A_BOLD,
            "awaiting_input": curses.A_BOLD,
            "completed": curses.A_NORMAL,
            "failed": curses.A_BOLD,
            "warn": curses.A_BOLD,
            "error": curses.A_BOLD,
            "info": curses.A_NORMAL,
        }

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # header
    curses.init_pair(2, curses.COLOR_CYAN, -1)                   # borders
    curses.init_pair(3, curses.COLOR_GREEN, -1)                  # running
    curses.init_pair(4, curses.COLOR_YELLOW, -1)                 # awaiting_input / warn
    curses.init_pair(5, curses.COLOR_BLUE, -1)                   # completed
    curses.init_pair(6, curses.COLOR_RED, -1)                    # failed / error
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)   # selected row
    curses.init_pair(8, curses.COLOR_WHITE, -1)                  # info/muted
    curses.init_pair(9, curses.COLOR_MAGENTA, -1)                # focused panel title

    return {
        "header": curses.color_pair(1) | curses.A_BOLD,
        "border": curses.color_pair(2),
        "selected": curses.color_pair(7) | curses.A_BOLD,
        "focused": curses.color_pair(9) | curses.A_BOLD,
        "muted": curses.color_pair(8) | curses.A_DIM,
        "running": curses.color_pair(3) | curses.A_BOLD,
        "awaiting_input": curses.color_pair(4) | curses.A_BOLD,
        "completed": curses.color_pair(5),
        "failed": curses.color_pair(6) | curses.A_BOLD,
        "warn": curses.color_pair(4) | curses.A_BOLD,
        "error": curses.color_pair(6) | curses.A_BOLD,
        "info": curses.color_pair(8),
    }


def _status_attr(status: str, palette: dict[str, int]) -> int:
    key = status.strip().lower()
    if key in {"running", "in_progress", "ready"}:
        return palette["running"]
    if key in {"awaiting_input"}:
        return palette["awaiting_input"]
    if key in {"completed"}:
        return palette["completed"]
    if key in {"failed", "error"}:
        return palette["failed"]
    return palette["muted"]


def _fetch_status_counts(conn) -> dict[str, int]:
    counts = {
        "running": 0,
        "awaiting_input": 0,
        "completed": 0,
        "failed": 0,
        "other": 0,
    }
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM runs GROUP BY status").fetchall()
    for row in rows:
        status = str(row["status"]).lower()
        c = int(row["c"])
        if status in counts:
            counts[status] = c
        else:
            counts["other"] += c
    return counts


def _fetch_runs(conn, project_name: str | None, limit: int = 200) -> list[Any]:
    if project_name is None:
        return conn.execute(
            """
            SELECT r.id, p.name AS project_name, rm.name AS roadmap_name,
                   r.status, r.tmux_session, r.created_at, r.updated_at, r.finished_at
            FROM runs r
            JOIN projects p ON p.id = r.project_id
            JOIN roadmaps rm ON rm.id = r.roadmap_id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return conn.execute(
        """
        SELECT r.id, p.name AS project_name, rm.name AS roadmap_name,
               r.status, r.tmux_session, r.created_at, r.updated_at, r.finished_at
        FROM runs r
        JOIN projects p ON p.id = r.project_id
        JOIN roadmaps rm ON rm.id = r.roadmap_id
        WHERE p.name = ?
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (project_name, limit),
    ).fetchall()


def _fetch_run_counts_per_project(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT p.name AS project_name, COUNT(r.id) AS c
        FROM projects p
        LEFT JOIN runs r ON r.project_id = p.id
        GROUP BY p.id
        """
    ).fetchall()
    return {str(r["project_name"]): int(r["c"]) for r in rows}


def _fetch_tasks(conn, project_name: str | None, limit: int = 250) -> list[Any]:
    if project_name is None:
        return conn.execute(
            """
            SELECT t.id, t.project_id, p.name AS project_name, t.batch_id, t.title, t.priority,
                   t.status, t.assigned_agent, t.preferred_agent, t.blocked_question,
                   t.branch_name, t.updated_at
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return conn.execute(
        """
        SELECT t.id, t.project_id, p.name AS project_name, t.batch_id, t.title, t.priority,
               t.status, t.assigned_agent, t.preferred_agent, t.blocked_question,
               t.branch_name, t.updated_at
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE p.name = ?
        ORDER BY t.id DESC
        LIMIT ?
        """,
        (project_name, limit),
    ).fetchall()


def _fetch_task_status_counts(conn) -> dict[str, int]:
    counts = {
        "queued": 0,
        "running": 0,
        "awaiting_input": 0,
        "stuck": 0,
        "completed": 0,
        "failed": 0,
        "other": 0,
    }
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status").fetchall()
    for row in rows:
        status = str(row["status"]).lower()
        c = int(row["c"])
        if status in counts:
            counts[status] = c
        else:
            counts["other"] += c
    return counts


def _fetch_open_alerts(conn, limit: int = 100) -> list[Any]:
    return conn.execute(
        """
        SELECT a.id, a.task_id, a.level, a.kind, a.message, a.created_at, p.name AS project_name
        FROM alerts a
        LEFT JOIN projects p ON p.id = a.project_id
        WHERE a.status = 'open'
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _fetch_task_events(conn, task_id: int, limit: int = 60) -> list[Any]:
    return conn.execute(
        """
        SELECT created_at, level, message
        FROM task_events
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (task_id, limit),
    ).fetchall()


def _window_start(selected_idx: int, total: int, max_rows: int) -> int:
    if total <= max_rows:
        return 0
    start = selected_idx - max_rows // 2
    start = max(0, start)
    return min(start, total - max_rows)


def _default_roadmap_path(project_root: str) -> str:
    root = Path(project_root)
    candidates = ("roadmap.md", "roadmap.yaml", "roadmap.yml")
    for candidate in candidates:
        if (root / candidate).exists():
            return candidate
    return "roadmap.md"


def _resolve_roadmap_path(project_root: str, roadmap_path_input: str) -> Path:
    roadmap_path = Path(roadmap_path_input).expanduser()
    if not roadmap_path.is_absolute():
        roadmap_path = (Path(project_root) / roadmap_path).resolve()
    else:
        roadmap_path = roadmap_path.resolve()
    return roadmap_path


def _prompt_new_run_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    project_name: str,
    roadmap_default: str,
    agent_default: str = "codex",
) -> tuple[str, str] | None:
    values = [roadmap_default, agent_default]
    labels = ["Roadmap path", "Default agent"]
    active = 0
    error_message = ""

    stdscr.nodelay(False)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(84, len(project_name) + 28), max(30, w - 4))
            box_h = 11
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, "Create Run", palette["border"])
            if panel is None:
                return None
            panel.keypad(True)

            _safe_add(panel, 1, 2, f"Project: {project_name}", palette["info"])
            _safe_add(panel, 8, 2, "Tab/Up/Down switch field | Enter launch | Esc cancel", palette["muted"])

            if error_message:
                _safe_add(panel, 9, 2, error_message, palette["failed"])

            for i, label in enumerate(labels):
                y_field = 3 + (i * 2)
                _safe_add(panel, y_field, 2, f"{label}:", palette["muted"])
                field_x = 18
                field_w = max(12, box_w - field_x - 3)
                attr = palette["selected"] if i == active else palette["info"]
                _safe_add(panel, y_field, field_x, " " * field_w, attr)
                _safe_add(panel, y_field, field_x, values[i], attr)

                if i == active:
                    cursor_x = field_x + min(len(values[i]), max(0, field_w - 1))
                    try:
                        panel.move(y_field, cursor_x)
                    except curses.error:
                        pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()

            if key in (27,):  # Esc
                return None
            if key in (9, curses.KEY_DOWN):
                active = (active + 1) % len(values)
                error_message = ""
                continue
            if key == curses.KEY_UP:
                active = (active - 1) % len(values)
                error_message = ""
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                roadmap = values[0].strip()
                agent = values[1].strip()
                if not roadmap:
                    error_message = "Roadmap path cannot be empty."
                    active = 0
                    continue
                if not agent:
                    error_message = "Default agent cannot be empty."
                    active = 1
                    continue
                return roadmap, agent
            if key in (curses.KEY_BACKSPACE, 127, 8):
                values[active] = values[active][:-1]
                error_message = ""
                continue
            if key == 21:  # Ctrl+U
                values[active] = ""
                error_message = ""
                continue
            if 32 <= key <= 126:
                values[active] += chr(key)
                error_message = ""
                continue
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _prompt_workflow_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    project_name: str,
    roadmap_default: str,
    coach_agent_default: str = "codex",
    coding_agent_default: str = "codex",
) -> tuple[str, str, str] | None:
    values = [roadmap_default, coach_agent_default, coding_agent_default]
    labels = ["Roadmap path", "Coach agent", "Coding agent"]
    active = 0
    error_message = ""

    stdscr.nodelay(False)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(88, len(project_name) + 32), max(30, w - 4))
            box_h = 13
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, "Roadmap Workflow", palette["border"])
            if panel is None:
                return None
            panel.keypad(True)

            _safe_add(panel, 1, 2, f"Project: {project_name}", palette["info"])
            _safe_add(panel, 10, 2, "Tab/Up/Down switch field | Enter continue | Esc cancel", palette["muted"])

            if error_message:
                _safe_add(panel, 11, 2, error_message, palette["failed"])

            for i, label in enumerate(labels):
                y_field = 3 + (i * 2)
                _safe_add(panel, y_field, 2, f"{label}:", palette["muted"])
                field_x = 18
                field_w = max(12, box_w - field_x - 3)
                attr = palette["selected"] if i == active else palette["info"]
                _safe_add(panel, y_field, field_x, " " * field_w, attr)
                _safe_add(panel, y_field, field_x, values[i], attr)

                if i == active:
                    cursor_x = field_x + min(len(values[i]), max(0, field_w - 1))
                    try:
                        panel.move(y_field, cursor_x)
                    except curses.error:
                        pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()

            if key in (27,):  # Esc
                return None
            if key in (9, curses.KEY_DOWN):
                active = (active + 1) % len(values)
                error_message = ""
                continue
            if key == curses.KEY_UP:
                active = (active - 1) % len(values)
                error_message = ""
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                roadmap = values[0].strip()
                coach_agent = values[1].strip()
                coding_agent = values[2].strip()
                if not roadmap:
                    error_message = "Roadmap path cannot be empty."
                    active = 0
                    continue
                if not coach_agent:
                    error_message = "Coach agent cannot be empty."
                    active = 1
                    continue
                if not coding_agent:
                    error_message = "Coding agent cannot be empty."
                    active = 2
                    continue
                return roadmap, coach_agent, coding_agent
            if key in (curses.KEY_BACKSPACE, 127, 8):
                values[active] = values[active][:-1]
                error_message = ""
                continue
            if key == 21:  # Ctrl+U
                values[active] = ""
                error_message = ""
                continue
            if 32 <= key <= 126:
                values[active] += chr(key)
                error_message = ""
                continue
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _prompt_confirm_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    title: str,
    lines: list[str],
    hint: str = "Enter confirm | Esc cancel",
) -> bool:
    stdscr.nodelay(False)
    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(80, max((len(line) for line in lines), default=0) + 8), max(30, w - 4))
            box_h = max(8, len(lines) + 5)
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, title, palette["border"])
            if panel is None:
                return False
            panel.keypad(True)

            for idx, line in enumerate(lines):
                _safe_add(panel, 1 + idx, 2, line, palette["info"])
            _safe_add(panel, box_h - 2, 2, hint, palette["muted"])

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()

            if key in (27, ord("n"), ord("N")):
                return False
            if key in (10, 13, curses.KEY_ENTER, 343, ord("y"), ord("Y")):
                return True
    finally:
        stdscr.nodelay(True)


def _prompt_add_project_modal(
    stdscr: curses.window,
    palette: dict[str, int],
) -> tuple[str, str, str] | None:
    values = ["", "", ""]
    labels = ["Project root path", "Project name (optional)", "Guidelines file (optional)"]
    active = 0
    error_message = ""

    stdscr.nodelay(False)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(96, 86), max(30, w - 4))
            box_h = 13
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)
            panel = _new_panel(stdscr, y, x, box_h, box_w, "Add Project", palette["border"])
            if panel is None:
                return None
            panel.keypad(True)

            _safe_add(panel, 1, 2, "Enter repository path. Name and guidelines are optional.", palette["info"])
            _safe_add(panel, 10, 2, "Tab/Up/Down switch field | Enter save | Esc cancel", palette["muted"])
            if error_message:
                _safe_add(panel, 11, 2, error_message, palette["failed"])

            for i, label in enumerate(labels):
                y_field = 3 + (i * 2)
                _safe_add(panel, y_field, 2, f"{label}:", palette["muted"])
                field_x = 30
                field_w = max(12, box_w - field_x - 3)
                attr = palette["selected"] if i == active else palette["info"]
                _safe_add(panel, y_field, field_x, " " * field_w, attr)
                _safe_add(panel, y_field, field_x, values[i], attr)
                if i == active:
                    cursor_x = field_x + min(len(values[i]), max(0, field_w - 1))
                    try:
                        panel.move(y_field, cursor_x)
                    except curses.error:
                        pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()
            if key in (27,):
                return None
            if key in (9, curses.KEY_DOWN):
                active = (active + 1) % len(values)
                error_message = ""
                continue
            if key == curses.KEY_UP:
                active = (active - 1) % len(values)
                error_message = ""
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                root = values[0].strip()
                name = values[1].strip()
                guidelines_file = values[2].strip()
                if not root:
                    error_message = "Project root path cannot be empty."
                    active = 0
                    continue
                return root, name, guidelines_file
            if key in (curses.KEY_BACKSPACE, 127, 8):
                values[active] = values[active][:-1]
                error_message = ""
                continue
            if key == 21:
                values[active] = ""
                error_message = ""
                continue
            if 32 <= key <= 126:
                values[active] += chr(key)
                error_message = ""
                continue
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _prompt_batch_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    project_name: str,
) -> tuple[str, str, str] | None:
    values = ["Roadmap Batch", "codex", ""]
    labels = ["Batch name", "Planner agent", "Task list (use ';' between items)"]
    active = 0
    error_message = ""

    stdscr.nodelay(False)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(106, len(project_name) + 44), max(30, w - 4))
            box_h = 13
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)
            panel = _new_panel(stdscr, y, x, box_h, box_w, "Create Batch", palette["border"])
            if panel is None:
                return None
            panel.keypad(True)

            _safe_add(panel, 1, 2, f"Project: {project_name}", palette["info"])
            _safe_add(panel, 10, 2, "Tab/Up/Down switch field | Enter create | Esc cancel", palette["muted"])
            if error_message:
                _safe_add(panel, 11, 2, error_message, palette["failed"])

            for i, label in enumerate(labels):
                y_field = 3 + (i * 2)
                _safe_add(panel, y_field, 2, f"{label}:", palette["muted"])
                field_x = 36
                field_w = max(12, box_w - field_x - 3)
                attr = palette["selected"] if i == active else palette["info"]
                _safe_add(panel, y_field, field_x, " " * field_w, attr)
                _safe_add(panel, y_field, field_x, values[i], attr)
                if i == active:
                    cursor_x = field_x + min(len(values[i]), max(0, field_w - 1))
                    try:
                        panel.move(y_field, cursor_x)
                    except curses.error:
                        pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()
            if key in (27,):
                return None
            if key in (9, curses.KEY_DOWN):
                active = (active + 1) % len(values)
                error_message = ""
                continue
            if key == curses.KEY_UP:
                active = (active - 1) % len(values)
                error_message = ""
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                name = values[0].strip()
                agent = values[1].strip()
                task_text = values[2].strip()
                if not name:
                    error_message = "Batch name cannot be empty."
                    active = 0
                    continue
                if not agent:
                    error_message = "Planner agent cannot be empty."
                    active = 1
                    continue
                if not task_text:
                    error_message = "Task list cannot be empty."
                    active = 2
                    continue
                normalized = "\n".join(
                    [item.strip() for item in task_text.split(";") if item.strip()]
                )
                return name, agent, normalized
            if key in (curses.KEY_BACKSPACE, 127, 8):
                values[active] = values[active][:-1]
                error_message = ""
                continue
            if key == 21:
                values[active] = ""
                error_message = ""
                continue
            if 32 <= key <= 126:
                values[active] += chr(key)
                error_message = ""
                continue
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _prompt_task_reply_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    task_id: int,
    question: str,
) -> str | None:
    value = ""
    error_message = ""
    stdscr.nodelay(False)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_w = min(max(92, len(question) + 12), max(30, w - 4))
            box_h = 10
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)
            panel = _new_panel(stdscr, y, x, box_h, box_w, f"Reply Task #{task_id}", palette["border"])
            if panel is None:
                return None
            panel.keypad(True)
            _safe_add(panel, 1, 2, f"Question: {question}", palette["warn"])
            _safe_add(panel, 6, 2, "Enter send | Esc cancel", palette["muted"])
            if error_message:
                _safe_add(panel, 7, 2, error_message, palette["failed"])
            field_x = 2
            field_w = max(12, box_w - field_x - 3)
            _safe_add(panel, 3, field_x, " " * field_w, palette["selected"])
            _safe_add(panel, 3, field_x, value, palette["selected"])
            try:
                panel.move(3, field_x + min(len(value), max(0, field_w - 1)))
            except curses.error:
                pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()
            if key in (27,):
                return None
            if key in (10, 13, curses.KEY_ENTER, 343):
                answer = value.strip()
                if not answer:
                    error_message = "Reply cannot be empty."
                    continue
                return answer
            if key in (curses.KEY_BACKSPACE, 127, 8):
                value = value[:-1]
                error_message = ""
                continue
            if key == 21:
                value = ""
                error_message = ""
                continue
            if 32 <= key <= 126:
                value += chr(key)
                error_message = ""
                continue
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _with_curses_paused(stdscr: curses.window, fn):
    try:
        curses.def_prog_mode()
        curses.endwin()
    except curses.error:
        pass
    try:
        return fn()
    finally:
        try:
            curses.reset_prog_mode()
        except curses.error:
            pass
        try:
            stdscr.erase()
            stdscr.refresh()
        except curses.error:
            pass


def _run_roadmap_coach_inline(
    stdscr: curses.window,
    palette: dict[str, int],
    session_name: str,
    project_name: str,
) -> tuple[bool, str]:
    target = f"{session_name}:coach.0"
    input_text = ""
    pane_text = ""
    note = "Esc continue workflow | Enter send message | Ctrl+U clear"

    stdscr.nodelay(True)
    try:
        curses.curs_set(1)
    except curses.error:
        pass

    try:
        while True:
            h, w = stdscr.getmaxyx()
            box_h = max(12, h - 4)
            box_w = max(50, w - 4)
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, f"Roadmap Coach [{project_name}]", palette["border"])
            if panel is None:
                return False, "Terminal too small for inline coach chat."
            panel.keypad(True)
            panel.nodelay(True)

            coach_alive = True
            try:
                coach_alive = "coach" in set(list_windows(session_name))
            except TmuxError:
                coach_alive = False

            if coach_alive:
                try:
                    pane_text = capture_pane(target, lines=1800)
                except TmuxError as exc:
                    coach_alive = False
                    note = f"Coach capture failed: {exc}. Press Esc to continue."
            else:
                note = "Coach session ended. Press Esc to continue workflow."

            inner_h, inner_w = panel.getmaxyx()
            _safe_add(panel, 1, 2, note, palette["muted"] if coach_alive else palette["warn"])

            log_top = 3
            input_y = inner_h - 3
            max_log_rows = max(1, input_y - log_top)
            lines = pane_text.splitlines()
            visible = lines[-max_log_rows:]
            for idx, line in enumerate(visible):
                _safe_add(panel, log_top + idx, 2, line, palette["info"])

            _safe_add(panel, input_y, 2, "You:", palette["focused"])
            field_x = 7
            field_w = max(8, inner_w - field_x - 3)
            _safe_add(panel, input_y, field_x, " " * field_w, palette["selected"])
            _safe_add(panel, input_y, field_x, input_text, palette["selected"])
            try:
                panel.move(input_y, field_x + min(len(input_text), max(0, field_w - 1)))
            except curses.error:
                pass

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()

            if key in (27,):  # Esc
                return True, "Inline roadmap coach conversation ended."
            if key in (10, 13, curses.KEY_ENTER, 343):
                if not coach_alive:
                    return True, "Coach session already ended."
                answer = input_text.strip()
                if answer:
                    send_text(target, answer, press_enter=True)
                    input_text = ""
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                input_text = input_text[:-1]
                continue
            if key == 21:  # Ctrl+U
                input_text = ""
                continue
            if 32 <= key <= 126:
                input_text += chr(key)
                continue
            time.sleep(0.05)
    finally:
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def _start_and_validate_workflow(
    stdscr: curses.window,
    palette: dict[str, int],
    resolved_db: Path,
    project_name: str,
    project_root: str,
    roadmap_path_input: str,
    coach_agent: str,
    coding_agent: str,
) -> tuple[bool, str, str]:
    roadmap_path = _resolve_roadmap_path(project_root, roadmap_path_input)
    session_name = ""

    try:
        session_name = start_roadmap_coach(
            project_name=project_name,
            output_path=roadmap_path,
            agent=coach_agent,
            db_path=resolved_db,
            attach=False,
        )
        inline_ok, inline_msg = _run_roadmap_coach_inline(
            stdscr=stdscr,
            palette=palette,
            session_name=session_name,
            project_name=project_name,
        )
        if not inline_ok:
            return False, f"Roadmap coach failed: {inline_msg}", str(roadmap_path)
    except Exception as exc:
        return False, f"Roadmap coach failed: {exc}", str(roadmap_path)
    finally:
        if session_name:
            try:
                kill_session(session_name)
            except Exception:
                pass

    if not roadmap_path.exists():
        return False, f"Roadmap not found after coach session {session_name}: {roadmap_path}", str(roadmap_path)

    try:
        roadmap = load_roadmap(roadmap_path, default_agent=coding_agent)
    except RoadmapValidationError as exc:
        return False, f"Roadmap validation failed: {exc}", str(roadmap_path)

    stage_count = sum(len(track.stages) for track in roadmap.tracks)
    return (
        True,
        f"Validated roadmap '{roadmap.name}' ({len(roadmap.tracks)} tracks, {stage_count} stages).",
        str(roadmap_path),
    )


def _launch_run_in_background(
    resolved_db: Path,
    project_name: str,
    project_root: str,
    roadmap_path_input: str,
    default_agent: str,
) -> tuple[bool, str]:
    roadmap_path = _resolve_roadmap_path(project_root, roadmap_path_input)

    if not roadmap_path.exists():
        return False, f"Roadmap not found: {roadmap_path}"

    package_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(package_root)
    )

    log_dir = Path(project_root) / ".yeehaw"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "run-launch.log"

    cmd = [
        sys.executable,
        "-m",
        "yeehaw.cli",
        "--db",
        str(resolved_db),
        "run",
        "start",
        "--project",
        project_name,
        "--roadmap",
        str(roadmap_path),
        "--default-agent",
        default_agent,
    ]
    cmd_line = " ".join(cmd)
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Launch run\n")
            log_file.write(f"project={project_name}\n")
            log_file.write(f"project_root={project_root}\n")
            log_file.write(f"roadmap={roadmap_path}\n")
            log_file.write(f"cmd={cmd_line}\n\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=project_root,
                stdout=log_file,
                stderr=log_file,
                env=env,
                start_new_session=True,
            )
        time.sleep(0.35)
        rc = proc.poll()
        if rc is not None and rc != 0:
            return False, f"Run launch failed (exit {rc}). See {log_path}"
    except Exception as exc:
        return False, f"Failed to launch run: {exc}"

    return True, f"Launched run for {project_name} (pid {proc.pid})"


def _add_project_from_root(conn, root_input: str, name: str, guidelines_file: str) -> tuple[bool, str]:
    root_path = Path(root_input).expanduser().resolve()
    if not root_path.exists():
        return False, f"Path does not exist: {root_path}"

    guidelines = ""
    if guidelines_file.strip():
        gpath = Path(guidelines_file).expanduser().resolve()
        if not gpath.exists():
            return False, f"Guidelines file not found: {gpath}"
        guidelines = gpath.read_text(encoding="utf-8").strip()

    try:
        repo_info = detect_repo(root_path)
    except GitRepoError:
        repo_info = None

    if repo_info is None:
        project_root = str(root_path)
        project_name = name.strip() or root_path.name
        db.create_project(conn, project_name, project_root, guidelines)
        return True, f"Project added: {project_name} (non-git)"

    project_root = repo_info.root_path
    project_name = name.strip() or Path(project_root).name
    db.create_project(
        conn,
        project_name,
        project_root,
        guidelines,
        git_remote_url=repo_info.remote_url,
        default_branch=repo_info.default_branch,
        head_sha=repo_info.head_sha,
    )
    return True, f"Project added: {project_name}"


def run_tui(db_path: str | Path | None = None, refresh_seconds: float = 1.0) -> None:
    resolved_db = Path(db_path).resolve() if db_path else db.default_db_path()
    conn = db.connect(resolved_db)
    scheduler_box: dict[str, GlobalScheduler | None] = {"obj": None}

    def _draw(stdscr: curses.window) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        palette = _init_colors()

        selected_project_idx = 0
        selected_run_idx = 0
        selected_task_idx = 0
        focus = FOCUS_PROJECTS
        list_mode = "runs"  # runs|tasks
        auto_scheduler = False

        force_refresh = True
        last_refresh = 0.0

        projects: list[Any] = []
        runs: list[Any] = []
        tasks: list[Any] = []
        tracks: list[Any] = []
        events: list[Any] = []
        task_events: list[Any] = []
        alerts: list[Any] = []
        run_counts_by_project: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        task_status_counts: dict[str, int] = {}
        total_runs = 0
        total_tasks = 0
        ui_message = ""
        ui_message_attr = palette["muted"]

        while True:
            now = time.monotonic()
            if auto_scheduler:
                try:
                    scheduler_box["obj"] = scheduler_box["obj"] or GlobalScheduler(db_path=resolved_db)
                    stats = scheduler_box["obj"].tick()
                    if any([stats.dispatched, stats.completed, stats.awaiting_input, stats.reassigned, stats.failed]):
                        ui_message = (
                            f"Scheduler tick: +{stats.dispatched} dispatched, "
                            f"+{stats.completed} completed, "
                            f"+{stats.awaiting_input} awaiting input, "
                            f"+{stats.reassigned} reassigned, "
                            f"+{stats.failed} failed"
                        )
                        ui_message_attr = palette["info"]
                except Exception as exc:
                    ui_message = f"Scheduler tick failed: {exc}"
                    ui_message_attr = palette["failed"]
                    auto_scheduler = False

            if force_refresh or now - last_refresh >= max(0.2, refresh_seconds):
                projects = db.list_projects(conn)
                run_counts_by_project = _fetch_run_counts_per_project(conn)

                project_options_count = len(projects) + 1  # +1 = All Projects
                selected_project_idx = max(0, min(selected_project_idx, project_options_count - 1))

                selected_project_name = None
                if selected_project_idx > 0 and projects:
                    selected_project_name = str(projects[selected_project_idx - 1]["name"])

                runs = _fetch_runs(conn, selected_project_name, limit=200)
                selected_run_idx = max(0, min(selected_run_idx, len(runs) - 1)) if runs else 0
                tasks = _fetch_tasks(conn, selected_project_name, limit=300)
                selected_task_idx = max(0, min(selected_task_idx, len(tasks) - 1)) if tasks else 0

                status_counts = _fetch_status_counts(conn)
                task_status_counts = _fetch_task_status_counts(conn)
                total_runs = int(conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"])
                total_tasks = int(conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"])
                alerts = _fetch_open_alerts(conn, limit=100)

                if list_mode == "runs" and runs:
                    selected_run_id = int(runs[selected_run_idx]["id"])
                    tracks = db.run_tracks(conn, selected_run_id)
                    events = db.run_events(conn, selected_run_id, limit=60)
                    task_events = []
                elif list_mode == "tasks" and tasks:
                    selected_task_id = int(tasks[selected_task_idx]["id"])
                    tracks = []
                    task_events = _fetch_task_events(conn, selected_task_id, limit=60)
                    events = []
                else:
                    tracks = []
                    events = []
                    task_events = []

                last_refresh = now
                force_refresh = False

            h, w = stdscr.getmaxyx()
            stdscr.erase()

            if h < 20 or w < 110:
                _safe_add(stdscr, 0, 0, "Terminal too small for dashboard. Resize to at least 110x20.", palette["failed"])
                _safe_add(
                    stdscr,
                    2,
                    0,
                    "Controls: Tab switch focus | n new run | w roadmap workflow | q quit | r refresh",
                    palette["muted"],
                )
                stdscr.refresh()
                key = stdscr.getch()
                if key in (ord("q"), ord("Q")):
                    return
                if key in (ord("r"), ord("R")):
                    force_refresh = True
                time.sleep(0.08)
                continue

            header_h = 3
            stats_h = 5
            footer_h = 3
            body_y = header_h + stats_h
            body_h = h - body_y - footer_h

            projects_w = max(28, int(w * 0.24))
            runs_w = max(40, int(w * 0.34))
            right_w = w - projects_w - runs_w

            tracks_h = max(8, body_h // 2)
            events_h = body_h - tracks_h

            projects_title = "Projects"
            runs_title = "Runs"
            if list_mode == "tasks":
                runs_title = "Tasks"
            if focus == FOCUS_PROJECTS:
                projects_title += " [FOCUS]"
            else:
                runs_title += " [FOCUS]"

            header = _new_panel(stdscr, 0, 0, header_h, w, "YEEHAW DASHBOARD", palette["border"])
            stats = _new_panel(stdscr, header_h, 0, stats_h, w, "System", palette["border"])
            projects_panel = _new_panel(stdscr, body_y, 0, body_h, projects_w, projects_title, palette["border"])
            runs_panel = _new_panel(stdscr, body_y, projects_w, body_h, runs_w, runs_title, palette["border"])
            tracks_panel = _new_panel(stdscr, body_y, projects_w + runs_w, tracks_h, right_w, "Tracks", palette["border"])
            events_panel = _new_panel(
                stdscr,
                body_y + tracks_h,
                projects_w + runs_w,
                events_h,
                right_w,
                "Events",
                palette["border"],
            )

            if header:
                current_project_label = "All Projects"
                if selected_project_idx > 0 and projects:
                    current_project_label = str(projects[selected_project_idx - 1]["name"])

                _safe_add(
                    header,
                    1,
                    2,
                    (
                        f"DB: {resolved_db} | Filter: {current_project_label} | Updated: {time.strftime('%H:%M:%S')} | "
                        "Tab focus  v runs/tasks  j/k move  Enter apply  n run  w workflow  b batch  a add project  y reply  s tick  z auto  r refresh  q quit"
                    ),
                    palette["header"],
                )

            if stats:
                cards = [
                    ("Projects", str(len(projects)), palette["info"]),
                    ("Runs", str(total_runs), palette["info"]),
                    ("Tasks", str(total_tasks), palette["info"]),
                    ("Running", str(status_counts.get("running", 0)), palette["running"]),
                    ("Awaiting", str(status_counts.get("awaiting_input", 0)), palette["awaiting_input"]),
                    ("Completed", str(status_counts.get("completed", 0)), palette["completed"]),
                    ("Failed", str(status_counts.get("failed", 0)), palette["failed"]),
                ]
                inner_w = stats.getmaxyx()[1] - 2
                card_w = max(10, inner_w // len(cards))
                for i, (label, value, attr) in enumerate(cards):
                    x = 1 + i * card_w
                    _safe_add(stats, 1, x, label, palette["muted"])
                    _safe_add(stats, 2, x, value, attr | curses.A_BOLD)
                    if i < len(cards) - 1 and x + card_w - 1 < stats.getmaxyx()[1] - 1:
                        _safe_add(stats, 1, x + card_w - 1, "|", palette["border"])
                        _safe_add(stats, 2, x + card_w - 1, "|", palette["border"])

            if projects_panel:
                rows_available = projects_panel.getmaxyx()[0] - 2
                total_options = len(projects) + 1
                start = _window_start(selected_project_idx, total_options, rows_available)

                for visible in range(rows_available):
                    idx = start + visible
                    if idx >= total_options:
                        break

                    y = 1 + visible
                    if idx == 0:
                        label = "All Projects"
                        run_count = len(_fetch_runs(conn, None, limit=1000000)) if False else total_runs
                    else:
                        project = projects[idx - 1]
                        name = str(project["name"])
                        label = name
                        run_count = run_counts_by_project.get(name, 0)

                    line = f"{label} ({run_count})"
                    if idx == selected_project_idx:
                        attr = palette["selected"] if focus == FOCUS_PROJECTS else (palette["selected"] | curses.A_DIM)
                        _safe_add(projects_panel, y, 2, line, attr)
                    else:
                        _safe_add(projects_panel, y, 2, line, palette["info"])

            if runs_panel:
                if list_mode == "runs" and not runs:
                    _safe_add(runs_panel, 1, 2, "No runs for selected project.", palette["muted"])
                elif list_mode == "tasks" and not tasks:
                    _safe_add(runs_panel, 1, 2, "No tasks for selected project.", palette["muted"])
                else:
                    rows_available = runs_panel.getmaxyx()[0] - 2
                    selected_idx = selected_run_idx if list_mode == "runs" else selected_task_idx
                    collection = runs if list_mode == "runs" else tasks
                    start = _window_start(selected_idx, len(collection), rows_available)

                    for visible in range(rows_available):
                        idx = start + visible
                        if idx >= len(collection):
                            break
                        row = collection[idx]
                        status = str(row["status"])
                        if list_mode == "runs":
                            line = f"#{row['id']:<4} {status:<14} {row['roadmap_name']}"
                        else:
                            agent = row["assigned_agent"] or row["preferred_agent"] or "-"
                            line = f"#{row['id']:<4} {status:<14} {agent:<8} {row['title']}"

                        y = 1 + visible
                        if idx == selected_idx:
                            attr = palette["selected"] if focus == FOCUS_RUNS else (palette["selected"] | curses.A_DIM)
                            _safe_add(runs_panel, y, 2, line, attr)
                        else:
                            _safe_add(runs_panel, y, 2, line, _status_attr(status, palette))

            if tracks_panel:
                if list_mode == "runs":
                    if not runs:
                        _safe_add(tracks_panel, 1, 2, "No run selected.", palette["muted"])
                    elif not tracks:
                        _safe_add(tracks_panel, 1, 2, "No track data.", palette["muted"])
                    else:
                        row = 1
                        max_rows = tracks_panel.getmaxyx()[0] - 2
                        for track in tracks:
                            if row >= max_rows:
                                break
                            status = str(track["status"])
                            line = (
                                f"[{status}] {track['track_id']}  "
                                f"agent={track['agent']}  stage={track['current_stage_index']}"
                            )
                            _safe_add(tracks_panel, row, 2, line, _status_attr(status, palette))
                            row += 1
                            if track["waiting_question"] and row < max_rows:
                                _safe_add(tracks_panel, row, 4, f"Q: {track['waiting_question']}", palette["awaiting_input"])
                                row += 1
                else:
                    if not alerts:
                        _safe_add(tracks_panel, 1, 2, "No open alerts.", palette["muted"])
                    else:
                        max_rows = tracks_panel.getmaxyx()[0] - 2
                        for idx, alert in enumerate(alerts[:max_rows]):
                            level = str(alert["level"]).lower()
                            attr = palette["warn"] if level == "warn" else palette["error"] if level == "error" else palette["info"]
                            line = f"#{alert['id']} [{level}] task={alert['task_id'] or '-'} {alert['kind']}: {alert['message']}"
                            _safe_add(tracks_panel, 1 + idx, 2, line, attr)

            if events_panel:
                if list_mode == "runs" and not runs:
                    _safe_add(events_panel, 1, 2, "No events.", palette["muted"])
                elif list_mode == "tasks" and not tasks:
                    _safe_add(events_panel, 1, 2, "No task events.", palette["muted"])
                else:
                    max_rows = events_panel.getmaxyx()[0] - 2
                    active_events = events if list_mode == "runs" else task_events
                    for idx, event in enumerate(active_events[:max_rows]):
                        ts = str(event["created_at"])[11:19]
                        level = str(event["level"]).lower()
                        level_attr = palette["info"]
                        if level == "warn":
                            level_attr = palette["warn"]
                        elif level == "error":
                            level_attr = palette["error"]
                        line = f"{ts} [{level.upper():5}] {event['message']}"
                        _safe_add(events_panel, 1 + idx, 2, line, level_attr)

            footer = _new_panel(stdscr, h - footer_h, 0, footer_h, w, "", palette["border"])
            if footer:
                if ui_message:
                    _safe_add(footer, 1, 2, ui_message, ui_message_attr)
                elif list_mode == "runs" and runs:
                    selected = runs[selected_run_idx]
                    detail = (
                        f"Run #{selected['id']} | project={selected['project_name']} | "
                        f"status={selected['status']} | session={selected['tmux_session']}"
                    )
                    _safe_add(footer, 1, 2, detail, _status_attr(str(selected["status"]), palette))
                elif list_mode == "tasks" and tasks:
                    selected_task = tasks[selected_task_idx]
                    detail = (
                        f"Task #{selected_task['id']} | project={selected_task['project_name']} | "
                        f"status={selected_task['status']} | agent={selected_task['assigned_agent'] or selected_task['preferred_agent'] or '-'} "
                        f"| auto_scheduler={'on' if auto_scheduler else 'off'}"
                    )
                    _safe_add(footer, 1, 2, detail, _status_attr(str(selected_task["status"]), palette))
                else:
                    _safe_add(footer, 1, 2, "No items for selected project", palette["muted"])

            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("r"), ord("R")):
                force_refresh = True
                continue
            if key == 9:  # Tab
                focus = FOCUS_RUNS if focus == FOCUS_PROJECTS else FOCUS_PROJECTS
                continue
            if key in (ord("v"), ord("V")):
                list_mode = "tasks" if list_mode == "runs" else "runs"
                focus = FOCUS_RUNS
                force_refresh = True
                continue
            if key in (ord("s"),):
                try:
                    scheduler_box["obj"] = scheduler_box["obj"] or GlobalScheduler(db_path=resolved_db)
                    stats = scheduler_box["obj"].tick()
                    ui_message = (
                        f"Scheduler tick: +{stats.dispatched} dispatched, +{stats.completed} completed, "
                        f"+{stats.awaiting_input} awaiting, +{stats.reassigned} reassigned, +{stats.failed} failed"
                    )
                    ui_message_attr = palette["info"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Scheduler tick failed: {exc}"
                    ui_message_attr = palette["failed"]
                continue
            if key in (ord("z"), ord("Z")):
                auto_scheduler = not auto_scheduler
                ui_message = f"Auto scheduler {'enabled' if auto_scheduler else 'disabled'}."
                ui_message_attr = palette["running"] if auto_scheduler else palette["muted"]
                continue
            if key in (ord("a"), ord("A")):
                modal = _prompt_add_project_modal(stdscr, palette)
                if modal is None:
                    ui_message = "Add project cancelled."
                    ui_message_attr = palette["muted"]
                    continue
                root_path, name_value, guidelines_file = modal
                success, msg = _add_project_from_root(conn, root_path, name_value, guidelines_file)
                ui_message = msg
                ui_message_attr = palette["running"] if success else palette["failed"]
                if success:
                    force_refresh = True
                continue
            if key in (ord("b"), ord("B")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a specific project first to create a task batch."
                    ui_message_attr = palette["warn"]
                    continue
                project = projects[selected_project_idx - 1]
                project_name = str(project["name"])
                batch_modal = _prompt_batch_modal(stdscr, palette, project_name)
                if batch_modal is None:
                    ui_message = "Batch creation cancelled."
                    ui_message_attr = palette["muted"]
                    continue
                batch_name, planner_agent, task_text = batch_modal
                try:
                    batch_id = create_batch_from_task_list(
                        project_name=project_name,
                        batch_name=batch_name,
                        task_list_text=task_text,
                        planner_agent=planner_agent,
                        db_path=resolved_db,
                    )
                    ui_message = f"Batch #{batch_id} created and queued for project {project_name}."
                    ui_message_attr = palette["running"]
                    force_refresh = True
                    list_mode = "tasks"
                    focus = FOCUS_RUNS
                except Exception as exc:
                    ui_message = f"Batch creation failed: {exc}"
                    ui_message_attr = palette["failed"]
                continue
            if key in (ord("y"), ord("Y")):
                if list_mode != "tasks" or not tasks:
                    ui_message = "Switch to tasks view and select an awaiting_input task."
                    ui_message_attr = palette["warn"]
                    continue
                selected_task = tasks[selected_task_idx]
                if str(selected_task["status"]) != "awaiting_input":
                    ui_message = "Selected task is not awaiting input."
                    ui_message_attr = palette["warn"]
                    continue
                question = str(selected_task["blocked_question"] or "").strip() or "Agent requested input."
                answer = _prompt_task_reply_modal(
                    stdscr=stdscr,
                    palette=palette,
                    task_id=int(selected_task["id"]),
                    question=question,
                )
                if answer is None:
                    ui_message = "Reply cancelled."
                    ui_message_attr = palette["muted"]
                    continue
                try:
                    scheduler_box["obj"] = scheduler_box["obj"] or GlobalScheduler(db_path=resolved_db)
                    scheduler_box["obj"].reply_to_task(int(selected_task["id"]), answer)
                    ui_message = f"Reply sent to task #{selected_task['id']}."
                    ui_message_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to send reply: {exc}"
                    ui_message_attr = palette["failed"]
                continue
            if key in (ord("n"), ord("N")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a specific project first to create a run."
                    ui_message_attr = palette["warn"]
                    continue

                project = projects[selected_project_idx - 1]
                project_name = str(project["name"])
                project_root = str(project["root_path"])

                roadmap_default = _default_roadmap_path(project_root)
                modal_result = _prompt_new_run_modal(
                    stdscr=stdscr,
                    palette=palette,
                    project_name=project_name,
                    roadmap_default=roadmap_default,
                    agent_default="codex",
                )
                if modal_result is None:
                    ui_message = "Run creation cancelled."
                    ui_message_attr = palette["muted"]
                    continue

                roadmap_value, agent_value = modal_result

                success, msg = _launch_run_in_background(
                    resolved_db=resolved_db,
                    project_name=project_name,
                    project_root=project_root,
                    roadmap_path_input=roadmap_value,
                    default_agent=agent_value,
                )
                ui_message = msg
                ui_message_attr = palette["running"] if success else palette["failed"]
                if success:
                    focus = FOCUS_RUNS
                    selected_run_idx = 0
                    force_refresh = True
                continue

            if key in (ord("w"), ord("W")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a specific project first to run roadmap workflow."
                    ui_message_attr = palette["warn"]
                    continue

                project = projects[selected_project_idx - 1]
                project_name = str(project["name"])
                project_root = str(project["root_path"])

                roadmap_default = _default_roadmap_path(project_root)
                workflow_result = _prompt_workflow_modal(
                    stdscr=stdscr,
                    palette=palette,
                    project_name=project_name,
                    roadmap_default=roadmap_default,
                    coach_agent_default="codex",
                    coding_agent_default="codex",
                )
                if workflow_result is None:
                    ui_message = "Roadmap workflow cancelled."
                    ui_message_attr = palette["muted"]
                    continue

                roadmap_value, coach_agent, coding_agent = workflow_result
                validated, msg, resolved_roadmap = _start_and_validate_workflow(
                    stdscr=stdscr,
                    palette=palette,
                    resolved_db=resolved_db,
                    project_name=project_name,
                    project_root=project_root,
                    roadmap_path_input=roadmap_value,
                    coach_agent=coach_agent,
                    coding_agent=coding_agent,
                )
                if not validated:
                    ui_message = msg
                    ui_message_attr = palette["failed"]
                    continue

                should_launch = _prompt_confirm_modal(
                    stdscr=stdscr,
                    palette=palette,
                    title="Roadmap Validated",
                    lines=[
                        msg,
                        f"Roadmap path: {resolved_roadmap}",
                        f"Coding agent: {coding_agent}",
                    ],
                    hint="Enter hand off to coding agent | Esc cancel",
                )
                if not should_launch:
                    ui_message = "Roadmap validated; handoff cancelled."
                    ui_message_attr = palette["warn"]
                    continue

                success, launch_msg = _launch_run_in_background(
                    resolved_db=resolved_db,
                    project_name=project_name,
                    project_root=project_root,
                    roadmap_path_input=resolved_roadmap,
                    default_agent=coding_agent,
                )
                ui_message = launch_msg
                ui_message_attr = palette["running"] if success else palette["failed"]
                if success:
                    focus = FOCUS_RUNS
                    selected_run_idx = 0
                    force_refresh = True
                continue

            if key in (10, 13):
                if focus == FOCUS_PROJECTS:
                    selected_run_idx = 0
                    focus = FOCUS_RUNS
                    force_refresh = True
                continue

            if focus == FOCUS_PROJECTS:
                total_options = len(projects) + 1
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    selected_project_idx = min(total_options - 1, selected_project_idx + 1)
                    selected_run_idx = 0
                    force_refresh = True
                elif key in (curses.KEY_UP, ord("k"), ord("K")):
                    selected_project_idx = max(0, selected_project_idx - 1)
                    selected_run_idx = 0
                    force_refresh = True
                elif key in (curses.KEY_NPAGE,):
                    selected_project_idx = min(total_options - 1, selected_project_idx + 10)
                    selected_run_idx = 0
                    force_refresh = True
                elif key in (curses.KEY_PPAGE,):
                    selected_project_idx = max(0, selected_project_idx - 10)
                    selected_run_idx = 0
                    force_refresh = True
                elif key in (ord("g"),):
                    selected_project_idx = 0
                    selected_run_idx = 0
                    force_refresh = True
                elif key in (ord("G"),):
                    selected_project_idx = total_options - 1
                    selected_run_idx = 0
                    force_refresh = True
            else:
                collection = runs if list_mode == "runs" else tasks
                if collection:
                    if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                        if list_mode == "runs":
                            selected_run_idx = min(len(collection) - 1, selected_run_idx + 1)
                        else:
                            selected_task_idx = min(len(collection) - 1, selected_task_idx + 1)
                        force_refresh = True
                    elif key in (curses.KEY_UP, ord("k"), ord("K")):
                        if list_mode == "runs":
                            selected_run_idx = max(0, selected_run_idx - 1)
                        else:
                            selected_task_idx = max(0, selected_task_idx - 1)
                        force_refresh = True
                    elif key in (curses.KEY_NPAGE,):
                        if list_mode == "runs":
                            selected_run_idx = min(len(collection) - 1, selected_run_idx + 10)
                        else:
                            selected_task_idx = min(len(collection) - 1, selected_task_idx + 10)
                        force_refresh = True
                    elif key in (curses.KEY_PPAGE,):
                        if list_mode == "runs":
                            selected_run_idx = max(0, selected_run_idx - 10)
                        else:
                            selected_task_idx = max(0, selected_task_idx - 10)
                        force_refresh = True
                    elif key in (ord("g"),):
                        if list_mode == "runs":
                            selected_run_idx = 0
                        else:
                            selected_task_idx = 0
                        force_refresh = True
                    elif key in (ord("G"),):
                        if list_mode == "runs":
                            selected_run_idx = len(collection) - 1
                        else:
                            selected_task_idx = len(collection) - 1
                        force_refresh = True

            time.sleep(0.05)

    curses.wrapper(_draw)
