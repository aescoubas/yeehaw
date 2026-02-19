from __future__ import annotations

import curses
import tempfile
import time
from pathlib import Path

from yeehaw.tmux import attach_session

from .config import ControlPlaneConfig
from .control_plane import ControlPlane
from .editor_bridge import open_in_editor
from .models import RuntimeKind, SessionHandle
from .roadmap_workflow import edit_roadmap_for_project, validate_roadmap
from .store import (
    apply_dispatcher_decision,
    create_project,
    create_batch_from_task_text,
    list_projects,
    queue_demo_task_for_project,
    replace_batch_open_tasks,
)


FOCUS_PROJECTS = "projects"
FOCUS_BATCHES = "batches"
FOCUS_TASKS = "tasks"
FOCUS_SESSIONS = "sessions"


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
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_GREEN, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_BLUE, -1)
    curses.init_pair(6, curses.COLOR_RED, -1)
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(8, curses.COLOR_WHITE, -1)
    curses.init_pair(9, curses.COLOR_MAGENTA, -1)
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
    if key in {"running", "active", "starting", "queued", "draft", "edited", "validated", "approved"}:
        return palette["running"]
    if key in {"awaiting_input", "paused", "preempted", "stuck"}:
        return palette["awaiting_input"]
    if key in {"completed", "ended"}:
        return palette["completed"]
    if key in {"failed", "crashed", "error"}:
        return palette["failed"]
    return palette["muted"]


def _fetch_batches(conn, project_id: int | None, limit: int = 250):
    if project_id is None:
        return conn.execute(
            """
            SELECT b.id, b.project_id, p.name AS project_name, b.name, b.status, b.created_at,
                   COUNT(t.id) AS task_total,
                   SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
                   SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_count,
                   SUM(CASE WHEN t.status = 'paused' THEN 1 ELSE 0 END) AS paused_count,
                   SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                   SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM task_batches b
            JOIN projects p ON p.id = b.project_id
            LEFT JOIN tasks t ON t.batch_id = b.id
            GROUP BY b.id, b.project_id, p.name, b.name, b.status, b.created_at
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT b.id, b.project_id, p.name AS project_name, b.name, b.status, b.created_at,
               COUNT(t.id) AS task_total,
               SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
               SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_count,
               SUM(CASE WHEN t.status = 'paused' THEN 1 ELSE 0 END) AS paused_count,
               SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
               SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM task_batches b
        JOIN projects p ON p.id = b.project_id
        LEFT JOIN tasks t ON t.batch_id = b.id
        WHERE b.project_id = ?
        GROUP BY b.id, b.project_id, p.name, b.name, b.status, b.created_at
        ORDER BY b.id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()


def _fetch_tasks(conn, project_id: int | None, batch_id: int | None = None, limit: int = 250):
    if project_id is None and batch_id is None:
        return conn.execute(
            """
            SELECT t.id, t.batch_id, t.project_id, p.name AS project_name, t.title, t.status, t.priority,
                   t.runtime_kind, t.assigned_agent, t.preferred_agent, t.updated_at
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if project_id is not None and batch_id is None:
        return conn.execute(
            """
            SELECT t.id, t.batch_id, t.project_id, p.name AS project_name, t.title, t.status, t.priority,
                   t.runtime_kind, t.assigned_agent, t.preferred_agent, t.updated_at
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.project_id = ?
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    if project_id is None and batch_id is not None:
        return conn.execute(
            """
            SELECT t.id, t.batch_id, t.project_id, p.name AS project_name, t.title, t.status, t.priority,
                   t.runtime_kind, t.assigned_agent, t.preferred_agent, t.updated_at
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.batch_id = ?
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (batch_id, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT t.id, t.batch_id, t.project_id, p.name AS project_name, t.title, t.status, t.priority,
               t.runtime_kind, t.assigned_agent, t.preferred_agent, t.updated_at
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE t.project_id = ? AND t.batch_id = ?
        ORDER BY t.id DESC
        LIMIT ?
        """,
        (project_id, batch_id, limit),
    ).fetchall()


def _fetch_sessions(conn, project_id: int | None, batch_id: int | None = None, limit: int = 250):
    if project_id is None and batch_id is None:
        return conn.execute(
            """
            SELECT s.id, s.task_id, t.batch_id, s.project_id, p.name AS project_name, s.runtime_kind,
                   s.transport_session_id, s.transport_target, s.status, s.started_at
            FROM agent_sessions s
            LEFT JOIN tasks t ON t.id = s.task_id
            JOIN projects p ON p.id = s.project_id
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if project_id is not None and batch_id is None:
        return conn.execute(
            """
            SELECT s.id, s.task_id, t.batch_id, s.project_id, p.name AS project_name, s.runtime_kind,
                   s.transport_session_id, s.transport_target, s.status, s.started_at
            FROM agent_sessions s
            LEFT JOIN tasks t ON t.id = s.task_id
            JOIN projects p ON p.id = s.project_id
            WHERE s.project_id = ?
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    if project_id is None and batch_id is not None:
        return conn.execute(
            """
            SELECT s.id, s.task_id, t.batch_id, s.project_id, p.name AS project_name, s.runtime_kind,
                   s.transport_session_id, s.transport_target, s.status, s.started_at
            FROM agent_sessions s
            JOIN tasks t ON t.id = s.task_id
            JOIN projects p ON p.id = s.project_id
            WHERE t.batch_id = ?
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (batch_id, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT s.id, s.task_id, t.batch_id, s.project_id, p.name AS project_name, s.runtime_kind,
               s.transport_session_id, s.transport_target, s.status, s.started_at
        FROM agent_sessions s
        JOIN tasks t ON t.id = s.task_id
        JOIN projects p ON p.id = s.project_id
        WHERE s.project_id = ? AND t.batch_id = ?
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (project_id, batch_id, limit),
    ).fetchall()


def _fetch_open_alerts(conn, project_id: int | None, limit: int = 250):
    if project_id is None:
        return conn.execute(
            """
            SELECT a.id, a.task_id, t.project_id, p.name AS project_name, a.level, a.kind, a.message, a.created_at
            FROM alerts a
            LEFT JOIN tasks t ON t.id = a.task_id
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE a.status = 'open'
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT a.id, a.task_id, t.project_id, p.name AS project_name, a.level, a.kind, a.message, a.created_at
        FROM alerts a
        JOIN tasks t ON t.id = a.task_id
        JOIN projects p ON p.id = t.project_id
        WHERE a.status = 'open' AND t.project_id = ?
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()


def _upsert_project_from_onboarding(
    conn,
    name: str,
    root_path: str,
    guidelines_file: str | None = None,
) -> int:
    project_name = name.strip()
    if not project_name:
        raise ValueError("project name cannot be empty")
    root = Path(root_path).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"project root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    if not (root / ".git").exists():
        raise ValueError(f"project root is not a git repository: {root}")
    guidelines = ""
    if guidelines_file:
        gpath = Path(guidelines_file).expanduser().resolve()
        if not gpath.exists():
            raise ValueError(f"guidelines file does not exist: {gpath}")
        guidelines = gpath.read_text(encoding="utf-8")
    return create_project(conn, name=project_name, root_path=root, guidelines=guidelines)


def _fetch_batch_detail(conn, batch_id: int):
    batch = conn.execute(
        """
        SELECT b.id, b.project_id, p.name AS project_name, b.name, b.status, b.created_at,
               COUNT(t.id) AS task_total,
               SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
               SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_count,
               SUM(CASE WHEN t.status = 'paused' THEN 1 ELSE 0 END) AS paused_count,
               SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
               SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM task_batches b
        JOIN projects p ON p.id = b.project_id
        LEFT JOIN tasks t ON t.batch_id = b.id
        WHERE b.id = ?
        GROUP BY b.id, b.project_id, p.name, b.name, b.status, b.created_at
        """,
        (batch_id,),
    ).fetchone()
    if batch is None:
        return None

    tasks = conn.execute(
        """
        SELECT id, batch_id, project_id, title, description, status, priority, runtime_kind, preferred_agent, assigned_agent, updated_at
        FROM tasks
        WHERE batch_id = ?
        ORDER BY id DESC
        """,
        (batch_id,),
    ).fetchall()

    timeline = conn.execute(
        """
        SELECT created_at, source, level, task_id, session_id, message
        FROM (
            SELECT se.created_at AS created_at,
                   ('session:' || se.kind) AS source,
                   se.level AS level,
                   s.task_id AS task_id,
                   s.id AS session_id,
                   se.message AS message
            FROM session_events se
            JOIN agent_sessions s ON s.id = se.session_id
            JOIN tasks t ON t.id = s.task_id
            WHERE t.batch_id = ?

            UNION ALL

            SELECT a.created_at AS created_at,
                   ('alert:' || a.kind) AS source,
                   a.level AS level,
                   a.task_id AS task_id,
                   NULL AS session_id,
                   a.message AS message
            FROM alerts a
            JOIN tasks t ON t.id = a.task_id
            WHERE t.batch_id = ?

            UNION ALL

            SELECT om.created_at AS created_at,
                   ('operator:' || om.direction) AS source,
                   'info' AS level,
                   s.task_id AS task_id,
                   s.id AS session_id,
                   om.body AS message
            FROM operator_messages om
            JOIN agent_sessions s ON s.id = om.session_id
            JOIN tasks t ON t.id = s.task_id
            WHERE t.batch_id = ?
        ) all_rows
        ORDER BY created_at DESC
        LIMIT 250
        """,
        (batch_id, batch_id, batch_id),
    ).fetchall()
    return {"batch": batch, "tasks": tasks, "timeline": timeline}


def _fetch_pending_dispatcher_decisions(conn, project_id: int | None, limit: int = 250):
    if project_id is None:
        return conn.execute(
            """
            SELECT d.id, d.task_id, t.project_id, p.name AS project_name, d.rationale, d.confidence, d.proposal_json, d.created_at
            FROM dispatcher_decisions d
            LEFT JOIN tasks t ON t.id = d.task_id
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE d.applied = 0 AND d.overridden = 0
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT d.id, d.task_id, t.project_id, p.name AS project_name, d.rationale, d.confidence, d.proposal_json, d.created_at
        FROM dispatcher_decisions d
        JOIN tasks t ON t.id = d.task_id
        JOIN projects p ON p.id = t.project_id
        WHERE d.applied = 0 AND d.overridden = 0 AND t.project_id = ?
        ORDER BY d.id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()


def _resolve_alert(conn, alert_id: int) -> None:
    conn.execute(
        """
        UPDATE alerts
        SET status = 'resolved',
            resolved_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        WHERE id = ?
        """,
        (alert_id,),
    )
    conn.commit()


def _fetch_session_usage(conn, session_ids: list[int]) -> dict[int, dict[str, object]]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    totals = conn.execute(
        f"""
        SELECT session_id,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cost_usd) AS cost_usd
        FROM usage_records
        WHERE session_id IN ({placeholders})
        GROUP BY session_id
        """,
        session_ids,
    ).fetchall()
    latest = conn.execute(
        f"""
        SELECT u.session_id, u.provider, u.model
        FROM usage_records u
        JOIN (
            SELECT session_id, MAX(id) AS max_id
            FROM usage_records
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
        ) last ON last.max_id = u.id
        """,
        session_ids,
    ).fetchall()

    data: dict[int, dict[str, object]] = {}
    for row in totals:
        sid = int(row["session_id"])
        data[sid] = {
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "cost_usd": float(row["cost_usd"] or 0.0),
            "provider": "unknown",
            "model": "unknown",
        }
    for row in latest:
        sid = int(row["session_id"])
        entry = data.setdefault(
            sid,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "provider": "unknown",
                "model": "unknown",
            },
        )
        entry["provider"] = str(row["provider"] or "unknown")
        entry["model"] = str(row["model"] or "unknown")
    return data


def _fetch_dashboard_signals(conn, project_id: int | None) -> dict[str, int]:
    if project_id is None:
        queued = int(conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'queued'").fetchone()["c"])
        running = int(conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'running'").fetchone()["c"])
        awaiting = int(conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'awaiting_input'").fetchone()["c"])
        sessions_active = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM agent_sessions
                WHERE status IN ('starting', 'active', 'paused')
                """
            ).fetchone()["c"]
        )
        pending_dispatch = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM dispatcher_decisions
                WHERE applied = 0 AND overridden = 0
                """
            ).fetchone()["c"]
        )
        alerts_open = int(conn.execute("SELECT COUNT(*) AS c FROM alerts WHERE status = 'open'").fetchone()["c"])
        stuck_open = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM alerts
                WHERE status = 'open' AND kind = 'task_stuck'
                """
            ).fetchone()["c"]
        )
    else:
        queued = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE project_id = ? AND status = 'queued'",
                (project_id,),
            ).fetchone()["c"]
        )
        running = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE project_id = ? AND status = 'running'",
                (project_id,),
            ).fetchone()["c"]
        )
        awaiting = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE project_id = ? AND status = 'awaiting_input'",
                (project_id,),
            ).fetchone()["c"]
        )
        sessions_active = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM agent_sessions
                WHERE project_id = ? AND status IN ('starting', 'active', 'paused')
                """,
                (project_id,),
            ).fetchone()["c"]
        )
        pending_dispatch = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM dispatcher_decisions d
                JOIN tasks t ON t.id = d.task_id
                WHERE t.project_id = ? AND d.applied = 0 AND d.overridden = 0
                """,
                (project_id,),
            ).fetchone()["c"]
        )
        alerts_open = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM alerts a
                JOIN tasks t ON t.id = a.task_id
                WHERE t.project_id = ? AND a.status = 'open'
                """,
                (project_id,),
            ).fetchone()["c"]
        )
        stuck_open = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM alerts a
                JOIN tasks t ON t.id = a.task_id
                WHERE t.project_id = ? AND a.status = 'open' AND a.kind = 'task_stuck'
                """,
                (project_id,),
            ).fetchone()["c"]
        )
    return {
        "queued": queued,
        "running": running,
        "awaiting": awaiting,
        "sessions_active": sessions_active,
        "pending_dispatch": pending_dispatch,
        "alerts_open": alerts_open,
        "stuck_open": stuck_open,
    }


def _window_start(selected_idx: int, total: int, max_rows: int) -> int:
    if total <= max_rows:
        return 0
    start = selected_idx - max_rows // 2
    start = max(0, start)
    return min(start, total - max_rows)


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


def _prompt_fields(
    stdscr: curses.window,
    palette: dict[str, int],
    title: str,
    labels: list[str],
    defaults: list[str],
    hint: str,
) -> list[str] | None:
    values = list(defaults)
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
            box_w = min(max(90, max((len(label) for label in labels), default=0) + 48), max(30, w - 4))
            box_h = max(8, len(labels) * 2 + 6)
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, title, palette["border"])
            if panel is None:
                return None
            panel.keypad(True)

            _safe_add(panel, box_h - 3, 2, hint, palette["muted"])
            if error_message:
                _safe_add(panel, box_h - 2, 2, error_message, palette["failed"])

            for i, label in enumerate(labels):
                y_field = 2 + (i * 2)
                _safe_add(panel, y_field, 2, f"{label}:", palette["muted"])
                field_x = 28
                field_w = max(8, box_w - field_x - 3)
                attr = palette["selected"] if i == active else palette["info"]
                _safe_add(panel, y_field, field_x, " " * field_w, attr)
                _safe_add(panel, y_field, field_x, values[i], attr)
                if i == active:
                    try:
                        panel.move(y_field, field_x + min(len(values[i]), max(0, field_w - 1)))
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
                if not values[0].strip():
                    error_message = f"{labels[0]} cannot be empty."
                    active = 0
                    continue
                return [v.strip() for v in values]
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


def _edit_batch_tasks_in_editor(project_name: str, editor: str | None = None) -> str:
    seed = (
        f"# Batch task list for project: {project_name}\n"
        "# One task per line. Directives are optional.\n"
        "# Syntax: <task title> @priority=70 @runtime=tmux @agent=codex\n"
        "# Examples:\n"
        "# Build API client @priority=80 @runtime=local_pty @agent=codex\n"
        "# Add integration tests @priority=65\n"
        "\n"
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yeehaw-tasklist.md", delete=False) as tmp:
        path = Path(tmp.name)
        tmp.write(seed)
    try:
        open_in_editor(path, editor=editor)
        return path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _edit_replan_tasks_in_editor(
    project_name: str,
    batch_name: str,
    existing_tasks: list[str],
    editor: str | None = None,
) -> str:
    lines = "\n".join(existing_tasks) if existing_tasks else ""
    seed = (
        f"# Replan task list for project: {project_name}\n"
        f"# Batch: {batch_name}\n"
        "# Replace task list content with the new plan. One task per line.\n"
        "# Directives: @priority=70 @runtime=tmux|local_pty @agent=codex\n"
        "\n"
        f"{lines}\n"
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yeehaw-replan.md", delete=False) as tmp:
        path = Path(tmp.name)
        tmp.write(seed)
    try:
        open_in_editor(path, editor=editor)
        return path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _alerts_dispatcher_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    conn,
    project_id: int | None,
) -> tuple[bool, str, int]:
    mode = "alerts"
    selected_alert_idx = 0
    selected_decision_idx = 0
    note = "Tab switch view | Enter action | Esc close"

    stdscr.nodelay(False)
    try:
        while True:
            alerts = _fetch_open_alerts(conn, project_id)
            decisions = _fetch_pending_dispatcher_decisions(conn, project_id)
            selected_alert_idx = min(max(0, selected_alert_idx), max(0, len(alerts) - 1))
            selected_decision_idx = min(max(0, selected_decision_idx), max(0, len(decisions) - 1))

            h, w = stdscr.getmaxyx()
            box_h = min(h, max(10, h - 2))
            box_w = min(w, max(40, w - 2))
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, "Alerts & Dispatcher", palette["border"])
            if panel is None:
                return False, "Terminal too small for alerts/dispatcher modal.", palette["failed"]
            panel.keypad(True)

            inner_h, _inner_w = panel.getmaxyx()
            _safe_add(
                panel,
                1,
                2,
                f"[{ 'ALERTS' if mode == 'alerts' else 'alerts' }] [{ 'DECISIONS' if mode == 'decisions' else 'decisions' }]",
                palette["focused"],
            )
            _safe_add(panel, 2, 2, note, palette["muted"])

            list_top = 4
            list_bottom = max(list_top, inner_h - 4)
            rows_avail = max(1, list_bottom - list_top)

            if mode == "alerts":
                if not alerts:
                    _safe_add(panel, list_top, 2, "No open alerts in scope.", palette["muted"])
                else:
                    start = _window_start(selected_alert_idx, len(alerts), rows_avail)
                    for i in range(rows_avail):
                        idx = start + i
                        if idx >= len(alerts):
                            break
                        row = alerts[idx]
                        line = (
                            f"#{row['id']:<4} task={row['task_id'] or '-':<4} "
                            f"{row['kind']:<24} {row['message']}"
                        )
                        attr = palette["selected"] if idx == selected_alert_idx else palette["info"]
                        _safe_add(panel, list_top + i, 2, line, attr)
                    _safe_add(panel, inner_h - 2, 2, "Enter: resolve selected alert", palette["warn"])
            else:
                if not decisions:
                    _safe_add(panel, list_top, 2, "No pending dispatcher decisions in scope.", palette["muted"])
                else:
                    start = _window_start(selected_decision_idx, len(decisions), rows_avail)
                    for i in range(rows_avail):
                        idx = start + i
                        if idx >= len(decisions):
                            break
                        row = decisions[idx]
                        line = (
                            f"#{row['id']:<4} task={row['task_id'] or '-':<4} "
                            f"conf={row['confidence'] if row['confidence'] is not None else '-':<5} "
                            f"{row['proposal_json']}"
                        )
                        attr = palette["selected"] if idx == selected_decision_idx else palette["info"]
                        _safe_add(panel, list_top + i, 2, line, attr)
                    _safe_add(panel, inner_h - 2, 2, "Enter: apply selected decision", palette["warn"])

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()
            if key in (27, ord("q"), ord("Q")):
                return True, "Closed alerts/dispatcher modal.", palette["muted"]
            if key == 9:
                mode = "decisions" if mode == "alerts" else "alerts"
                continue
            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                if mode == "alerts":
                    selected_alert_idx = min(selected_alert_idx + 1, max(0, len(alerts) - 1))
                else:
                    selected_decision_idx = min(selected_decision_idx + 1, max(0, len(decisions) - 1))
                continue
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                if mode == "alerts":
                    selected_alert_idx = max(selected_alert_idx - 1, 0)
                else:
                    selected_decision_idx = max(selected_decision_idx - 1, 0)
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                if mode == "alerts":
                    if not alerts:
                        note = "No alert selected."
                        continue
                    selected = alerts[selected_alert_idx]
                    _resolve_alert(conn, int(selected["id"]))
                    return True, f"Resolved alert #{selected['id']}.", palette["running"]
                if not decisions:
                    note = "No decision selected."
                    continue
                selected = decisions[selected_decision_idx]
                decision_id = int(selected["id"])
                try:
                    apply_dispatcher_decision(conn, decision_id)
                except Exception as exc:
                    return False, f"Failed to apply dispatcher decision #{decision_id}: {exc}", palette["failed"]
                return True, f"Applied dispatcher decision #{decision_id}.", palette["running"]
    finally:
        stdscr.nodelay(True)


def _batch_detail_modal(
    stdscr: curses.window,
    palette: dict[str, int],
    control_plane: ControlPlane,
    conn,
    batch_id: int,
) -> tuple[bool, str, int]:
    selected_task_idx = 0

    stdscr.nodelay(False)
    try:
        while True:
            detail = _fetch_batch_detail(conn, batch_id)
            if detail is None:
                return False, f"Batch #{batch_id} not found.", palette["failed"]
            batch = detail["batch"]
            tasks = detail["tasks"]
            timeline = detail["timeline"]
            selected_task_idx = min(max(selected_task_idx, 0), max(0, len(tasks) - 1))

            h, w = stdscr.getmaxyx()
            box_h = min(h, max(14, h - 2))
            box_w = min(w, max(60, w - 2))
            y = max(0, (h - box_h) // 2)
            x = max(0, (w - box_w) // 2)

            panel = _new_panel(stdscr, y, x, box_h, box_w, f"Batch #{batch['id']} Detail", palette["border"])
            if panel is None:
                return False, "Terminal too small for batch detail modal.", palette["failed"]
            panel.keypad(True)

            inner_h, inner_w = panel.getmaxyx()
            summary = (
                f"name={batch['name']} status={batch['status']} total={int(batch['task_total'] or 0)} "
                f"q={int(batch['queued_count'] or 0)} r={int(batch['running_count'] or 0)} "
                f"p={int(batch['paused_count'] or 0)} c={int(batch['completed_count'] or 0)} "
                f"f={int(batch['failed_count'] or 0)}"
            )
            _safe_add(panel, 1, 2, summary, palette["focused"])
            _safe_add(panel, 2, 2, "Esc close | j/k task select | r replan | p pause | u resume | x preempt", palette["muted"])

            split_y = max(6, inner_h // 2)
            _safe_add(panel, 4, 2, "Tasks", palette["focused"])
            task_rows = max(1, split_y - 5)
            if not tasks:
                _safe_add(panel, 5, 2, "No tasks in batch.", palette["muted"])
            else:
                start = _window_start(selected_task_idx, len(tasks), task_rows)
                for i in range(task_rows):
                    idx = start + i
                    if idx >= len(tasks):
                        break
                    row = tasks[idx]
                    agent = row["assigned_agent"] or row["preferred_agent"] or "-"
                    line = (
                        f"#{row['id']:<4} {row['status']:<12} prio={row['priority']:<3} "
                        f"rt={row['runtime_kind']:<9} a={agent:<10} {row['title']}"
                    )
                    attr = palette["selected"] if idx == selected_task_idx else _status_attr(str(row["status"]), palette)
                    _safe_add(panel, 5 + i, 2, line, attr)

            _safe_add(panel, split_y, 2, "Timeline (alerts/events/messages)", palette["focused"])
            timeline_rows = max(1, inner_h - split_y - 2)
            for i in range(timeline_rows):
                if i >= len(timeline):
                    break
                row = timeline[i]
                line = (
                    f"{row['created_at'] or '-'} {row['source']:<22} "
                    f"task={row['task_id'] or '-':<4} {row['message']}"
                )
                _safe_add(panel, split_y + 1 + i, 2, line, palette["info"])

            stdscr.refresh()
            panel.refresh()
            key = panel.getch()
            if key in (27, ord("q"), ord("Q")):
                return True, f"Closed batch #{batch_id} detail.", palette["muted"]
            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                selected_task_idx = min(selected_task_idx + 1, max(0, len(tasks) - 1))
                continue
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                selected_task_idx = max(selected_task_idx - 1, 0)
                continue
            if key in (ord("p"), ord("P")):
                result = control_plane.pause_batch(batch_id)
                return (
                    True,
                    f"Paused batch #{batch_id}: tasks_changed={result.task_rows_changed} sessions_ended={result.sessions_ended}",
                    palette["running"],
                )
            if key in (ord("u"), ord("U")):
                result = control_plane.resume_batch(batch_id)
                return True, f"Resumed batch #{batch_id}: tasks_changed={result.task_rows_changed}", palette["running"]
            if key in (ord("x"), ord("X")):
                result = control_plane.preempt_batch(batch_id)
                return (
                    True,
                    f"Preempted batch #{batch_id}: tasks_changed={result.task_rows_changed} sessions_ended={result.sessions_ended}",
                    palette["running"],
                )
            if key in (ord("r"), ord("R")):
                default_task = tasks[selected_task_idx] if tasks else None
                default_runtime = str(default_task["runtime_kind"]) if default_task else RuntimeKind.TMUX.value
                default_agent = str(default_task["preferred_agent"] or default_task["assigned_agent"] or "codex") if default_task else "codex"
                default_priority = str(default_task["priority"] if default_task is not None else 50)
                defaults = [
                    default_runtime,
                    default_agent,
                    default_priority,
                    "y",
                    "",
                ]
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title=f"Replan Batch #{batch_id}",
                    labels=[
                        "Default runtime (tmux|local_pty)",
                        "Default agent command",
                        "Base priority (0-100)",
                        "Preempt active first? (y/n)",
                        "Editor command (optional)",
                    ],
                    defaults=defaults,
                    hint="Enter opens editor with current task seed | Esc cancel",
                )
                if fields is None:
                    continue
                runtime_raw, agent, priority_raw, preempt_raw, editor = fields
                runtime_raw = runtime_raw.strip().lower() or RuntimeKind.TMUX.value
                if runtime_raw not in {RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value}:
                    return False, f"Unsupported runtime: {runtime_raw}", palette["failed"]
                try:
                    base_priority = max(0, min(100, int(priority_raw)))
                except ValueError:
                    return False, f"Invalid base priority: {priority_raw}", palette["failed"]

                seed_tasks = [
                    str(row["title"])
                    for row in tasks
                    if str(row["status"]) in {"queued", "running", "paused", "awaiting_input"}
                ]
                if not seed_tasks:
                    seed_tasks = [str(row["title"]) for row in tasks[:10]]
                try:
                    task_text = _with_curses_paused(
                        stdscr,
                        lambda: _edit_replan_tasks_in_editor(
                            project_name=str(batch["project_name"]),
                            batch_name=str(batch["name"]),
                            existing_tasks=seed_tasks,
                            editor=(editor or None),
                        ),
                    )
                    if preempt_raw.strip().lower() in {"y", "yes", "1", "true"}:
                        control_plane.preempt_batch(batch_id)
                    new_task_ids = replace_batch_open_tasks(
                        conn=conn,
                        batch_id=batch_id,
                        task_text=task_text,
                        default_runtime=RuntimeKind(runtime_raw),
                        default_agent=agent or None,
                        default_priority=base_priority,
                    )
                    return True, f"Replanned batch #{batch_id}: queued {len(new_task_ids)} tasks.", palette["running"]
                except Exception as exc:
                    return False, f"Failed to replan batch #{batch_id}: {exc}", palette["failed"]
    finally:
        stdscr.nodelay(True)


def _run_local_pty_workspace(
    stdscr: curses.window,
    palette: dict[str, int],
    control_plane: ControlPlane,
    session_row,
) -> tuple[bool, str]:
    runtime = control_plane.runtimes.get(RuntimeKind.LOCAL_PTY)
    handle = SessionHandle(
        runtime_kind=RuntimeKind.LOCAL_PTY,
        session_id=str(session_row["transport_session_id"]),
        target=str(session_row["transport_target"]),
        pid=None,
    )
    output_lines: list[str] = []
    input_text = ""
    note = "Esc back to ops | Enter send | Ctrl+U clear"

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
            panel = _new_panel(stdscr, y, x, box_h, box_w, "Local PTY Workspace", palette["border"])
            if panel is None:
                return False, "Terminal too small for local PTY workspace."
            panel.keypad(True)
            panel.nodelay(True)

            alive = True
            try:
                chunk = runtime.capture_output(handle, lines=500)
                if chunk:
                    output_lines.extend(chunk.splitlines())
                    output_lines = output_lines[-2200:]
            except Exception as exc:
                alive = False
                note = f"capture failed: {exc} | Esc back"
            try:
                alive = alive and runtime.is_session_alive(handle)
            except Exception as exc:
                alive = False
                note = f"session lookup failed: {exc} | Esc back"
            if not alive and "Esc back" not in note:
                note = "Session ended. Esc back to ops."

            inner_h, inner_w = panel.getmaxyx()
            _safe_add(panel, 1, 2, note, palette["muted"] if alive else palette["warn"])

            log_top = 3
            input_y = inner_h - 3
            max_log_rows = max(1, input_y - log_top)
            visible = output_lines[-max_log_rows:]
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
            if key in (27,):
                return True, "Returned from local PTY workspace."
            if key in (10, 13, curses.KEY_ENTER, 343):
                if not alive:
                    return True, "Local PTY session already ended."
                msg = input_text.strip()
                if msg:
                    try:
                        runtime.send_user_input(handle, msg)
                    except Exception as exc:
                        note = f"send failed: {exc}"
                    input_text = ""
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                input_text = input_text[:-1]
                continue
            if key == 21:
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


def _open_workspace(stdscr: curses.window, palette: dict[str, int], control_plane: ControlPlane, session_row) -> tuple[bool, str]:
    runtime_kind = str(session_row["runtime_kind"]).strip().lower()
    if runtime_kind == RuntimeKind.TMUX.value:
        session_name = str(session_row["transport_session_id"])
        try:
            _with_curses_paused(stdscr, lambda: attach_session(session_name))
            return True, f"Returned from tmux workspace {session_name}."
        except Exception as exc:
            return False, f"Failed to open tmux workspace: {exc}"
    if runtime_kind == RuntimeKind.LOCAL_PTY.value:
        return _run_local_pty_workspace(stdscr, palette, control_plane, session_row)
    return False, f"Unsupported runtime kind: {runtime_kind}"


def run_tui(db_path: str | Path = ".yeehaw/yeehaw_v2.db", refresh_seconds: float = 1.0, poll_seconds: float = 1.0) -> None:
    resolved_db = Path(db_path).expanduser().resolve()
    control_plane = ControlPlane(ControlPlaneConfig(db_path=resolved_db, poll_seconds=poll_seconds))
    conn = control_plane.conn

    def _main(stdscr: curses.window) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        palette = _init_colors()

        focus = FOCUS_PROJECTS
        selected_project_idx = 0
        selected_batch_idx = 0
        selected_task_idx = 0
        selected_session_idx = 0
        auto_tick = False
        ui_message = "Ready."
        ui_attr = palette["muted"]

        projects = []
        batches = []
        tasks = []
        sessions = []
        dashboard_signals = {
            "queued": 0,
            "running": 0,
            "awaiting": 0,
            "sessions_active": 0,
            "pending_dispatch": 0,
            "alerts_open": 0,
            "stuck_open": 0,
        }
        session_usage: dict[int, dict[str, object]] = {}
        last_refresh = 0.0
        last_tick = 0.0
        force_refresh = True

        while True:
            now = time.monotonic()
            if auto_tick and now - last_tick >= max(0.2, poll_seconds):
                stats = control_plane.tick()
                last_tick = now
                ui_message = (
                    f"tick: dispatched={stats.dispatched} completed={stats.completed} "
                    f"failed={stats.failed} stuck={stats.stuck}"
                )
                ui_attr = palette["info"]
                force_refresh = True

            if force_refresh or now - last_refresh >= max(0.1, refresh_seconds):
                projects = list_projects(conn)
                project_id = None if selected_project_idx == 0 else int(projects[selected_project_idx - 1]["id"]) if projects else None
                batches = _fetch_batches(conn, project_id)
                selected_batch_idx = min(max(selected_batch_idx, 0), len(batches))
                batch_id = None if selected_batch_idx == 0 else int(batches[selected_batch_idx - 1]["id"]) if batches else None
                tasks = _fetch_tasks(conn, project_id, batch_id=batch_id)
                sessions = _fetch_sessions(conn, project_id, batch_id=batch_id)
                dashboard_signals = _fetch_dashboard_signals(conn, project_id)
                session_usage = _fetch_session_usage(conn, [int(row["id"]) for row in sessions])
                selected_task_idx = min(max(selected_task_idx, 0), max(0, len(tasks) - 1))
                selected_session_idx = min(max(selected_session_idx, 0), max(0, len(sessions) - 1))
                selected_project_idx = min(max(selected_project_idx, 0), len(projects))
                last_refresh = now
                force_refresh = False

            h, w = stdscr.getmaxyx()
            stdscr.erase()
            if h < 24 or w < 140:
                _safe_add(stdscr, 0, 0, "Terminal too small for v2 dashboard. Resize to at least 140x24.", palette["failed"])
                _safe_add(stdscr, 2, 0, "Controls: q quit | r refresh", palette["muted"])
                stdscr.refresh()
                key = stdscr.getch()
                if key in (ord("q"), ord("Q")):
                    return
                continue

            header_h = 4
            footer_h = 4
            body_y = header_h
            body_h = h - header_h - footer_h

            projects_w = max(22, w // 7)
            batches_w = max(28, w // 4)
            tasks_w = max(36, w // 3)
            sessions_w = w - projects_w - batches_w - tasks_w
            if sessions_w < 24:
                deficit = 24 - sessions_w
                tasks_w = max(30, tasks_w - deficit)
                sessions_w = w - projects_w - batches_w - tasks_w

            header = _new_panel(stdscr, 0, 0, header_h, w, "YEEHAW V2 OPS", palette["border"])
            if header:
                _safe_add(
                    header,
                    1,
                    2,
                    (
                        f"DB: {resolved_db} | Tab focus | j/k move | o onboard project | n queue task | b queue batch | y reply task | p pause-batch | "
                        f"u resume-batch | x preempt-batch | Enter batch detail | e edit roadmap | "
                        f"a alerts/dispatch | t tick | z auto-tick | w workspace | r refresh | q quit"
                    ),
                    palette["header"],
                )
                signals_line = (
                    f"queued={dashboard_signals['queued']} running={dashboard_signals['running']} "
                    f"awaiting_input={dashboard_signals['awaiting']} active_sessions={dashboard_signals['sessions_active']} "
                    f"pending_dispatch={dashboard_signals['pending_dispatch']} open_alerts={dashboard_signals['alerts_open']} "
                    f"stuck={dashboard_signals['stuck_open']}"
                )
                signal_attr = palette["info"]
                if dashboard_signals["alerts_open"] > 0 or dashboard_signals["pending_dispatch"] > 0:
                    signal_attr = palette["warn"]
                _safe_add(header, 2, 2, signals_line, signal_attr)

            projects_title = "Projects" + (" [FOCUS]" if focus == FOCUS_PROJECTS else "")
            batches_title = "Batches" + (" [FOCUS]" if focus == FOCUS_BATCHES else "")
            tasks_title = "Tasks" + (" [FOCUS]" if focus == FOCUS_TASKS else "")
            sessions_title = "Sessions" + (" [FOCUS]" if focus == FOCUS_SESSIONS else "")

            projects_panel = _new_panel(stdscr, body_y, 0, body_h, projects_w, projects_title, palette["border"])
            batches_panel = _new_panel(stdscr, body_y, projects_w, body_h, batches_w, batches_title, palette["border"])
            tasks_panel = _new_panel(stdscr, body_y, projects_w + batches_w, body_h, tasks_w, tasks_title, palette["border"])
            sessions_panel = _new_panel(
                stdscr,
                body_y,
                projects_w + batches_w + tasks_w,
                body_h,
                sessions_w,
                sessions_title,
                palette["border"],
            )

            if projects_panel:
                options = ["All Projects"] + [str(p["name"]) for p in projects]
                rows_avail = projects_panel.getmaxyx()[0] - 2
                start = _window_start(selected_project_idx, len(options), rows_avail)
                for i in range(rows_avail):
                    idx = start + i
                    if idx >= len(options):
                        break
                    name = options[idx]
                    line = name
                    attr = palette["info"]
                    if idx == selected_project_idx:
                        attr = palette["selected"] if focus == FOCUS_PROJECTS else (palette["selected"] | curses.A_DIM)
                    _safe_add(projects_panel, 1 + i, 2, line, attr)

            if batches_panel:
                options = ["All Batches"] + [f"#{row['id']} {row['name']}" for row in batches]
                rows_avail = batches_panel.getmaxyx()[0] - 2
                start = _window_start(selected_batch_idx, len(options), rows_avail)
                for i in range(rows_avail):
                    idx = start + i
                    if idx >= len(options):
                        break
                    if idx == 0:
                        line = options[idx]
                    else:
                        row = batches[idx - 1]
                        line = (
                            f"#{row['id']:<4} q={int(row['queued_count'] or 0):<2} "
                            f"r={int(row['running_count'] or 0):<2} "
                            f"p={int(row['paused_count'] or 0):<2} "
                            f"c={int(row['completed_count'] or 0):<2} "
                            f"f={int(row['failed_count'] or 0):<2} {row['name']}"
                        )
                    attr = palette["info"]
                    if idx == selected_batch_idx:
                        attr = palette["selected"] if focus == FOCUS_BATCHES else (palette["selected"] | curses.A_DIM)
                    _safe_add(batches_panel, 1 + i, 2, line, attr)

            if tasks_panel:
                rows_avail = tasks_panel.getmaxyx()[0] - 2
                if not tasks:
                    _safe_add(tasks_panel, 1, 2, "No tasks for selection.", palette["muted"])
                else:
                    start = _window_start(selected_task_idx, len(tasks), rows_avail)
                    for i in range(rows_avail):
                        idx = start + i
                        if idx >= len(tasks):
                            break
                        row = tasks[idx]
                        agent = row["assigned_agent"] or row["preferred_agent"] or "-"
                        line = (
                            f"#{row['id']:<4} b={row['batch_id']:<4} {row['status']:<12} "
                            f"rt={row['runtime_kind']:<9} a={agent:<10} {row['title']}"
                        )
                        attr = _status_attr(str(row["status"]), palette)
                        if idx == selected_task_idx:
                            attr = palette["selected"] if focus == FOCUS_TASKS else (palette["selected"] | curses.A_DIM)
                        _safe_add(tasks_panel, 1 + i, 2, line, attr)

            if sessions_panel:
                rows_avail = sessions_panel.getmaxyx()[0] - 2
                if not sessions:
                    _safe_add(sessions_panel, 1, 2, "No sessions for selection.", palette["muted"])
                else:
                    start = _window_start(selected_session_idx, len(sessions), rows_avail)
                    for i in range(rows_avail):
                        idx = start + i
                        if idx >= len(sessions):
                            break
                        row = sessions[idx]
                        usage = session_usage.get(int(row["id"]))
                        if usage is None:
                            usage_suffix = "tok=-/- cost=$0.0000"
                        else:
                            usage_suffix = (
                                f"tok={usage['input_tokens']}/{usage['output_tokens']} "
                                f"cost=${float(usage['cost_usd']):.4f} "
                                f"{usage['provider']}:{usage['model']}"
                            )
                        line = (
                            f"#{row['id']:<4} batch={row['batch_id'] or '-':<4} task={row['task_id'] or '-':<4} {row['status']:<10} "
                            f"rt={row['runtime_kind']:<9} sid={row['transport_session_id']} {usage_suffix}"
                        )
                        attr = _status_attr(str(row["status"]), palette)
                        if idx == selected_session_idx:
                            attr = palette["selected"] if focus == FOCUS_SESSIONS else (palette["selected"] | curses.A_DIM)
                        _safe_add(sessions_panel, 1 + i, 2, line, attr)

            footer = _new_panel(stdscr, h - footer_h, 0, footer_h, w, "", palette["border"])
            if footer:
                _safe_add(footer, 1, 2, ui_message, ui_attr)
                selected_usage_line = "Selected session usage: none"
                if sessions:
                    selected_row = sessions[selected_session_idx]
                    usage = session_usage.get(int(selected_row["id"]))
                    if usage is None:
                        selected_usage_line = f"Selected session #{selected_row['id']}: no usage records yet."
                    else:
                        selected_usage_line = (
                            f"Selected session #{selected_row['id']} task={selected_row['task_id'] or '-'} "
                            f"provider={usage['provider']} model={usage['model']} "
                            f"in={usage['input_tokens']} out={usage['output_tokens']} "
                            f"cost=${float(usage['cost_usd']):.6f}"
                        )
                _safe_add(footer, 2, 2, selected_usage_line, palette["info"])
                if auto_tick:
                    _safe_add(footer, 1, max(2, w - 20), "auto-tick=on", palette["running"])

            stdscr.refresh()

            key = stdscr.getch()
            if key == -1:
                time.sleep(0.03)
                continue
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("r"), ord("R")):
                force_refresh = True
                continue
            if key in (10, 13, curses.KEY_ENTER, 343):
                if focus == FOCUS_BATCHES:
                    if selected_batch_idx == 0 or not batches:
                        ui_message = "Select a concrete batch first."
                        ui_attr = palette["warn"]
                        continue
                    batch = batches[selected_batch_idx - 1]
                    ok, msg, attr = _batch_detail_modal(stdscr, palette, control_plane, conn, int(batch["id"]))
                    ui_message = msg
                    ui_attr = attr if ok else palette["failed"]
                    force_refresh = True
                    continue
            if key == 9:  # Tab
                if focus == FOCUS_PROJECTS:
                    focus = FOCUS_BATCHES
                elif focus == FOCUS_BATCHES:
                    focus = FOCUS_TASKS
                elif focus == FOCUS_TASKS:
                    focus = FOCUS_SESSIONS
                else:
                    focus = FOCUS_PROJECTS
                continue
            if key in (ord("t"), ord("T")):
                stats = control_plane.tick()
                ui_message = (
                    f"tick: dispatched={stats.dispatched} completed={stats.completed} "
                    f"failed={stats.failed} stuck={stats.stuck}"
                )
                ui_attr = palette["info"]
                force_refresh = True
                continue
            if key in (ord("z"), ord("Z")):
                auto_tick = not auto_tick
                ui_message = f"auto-tick {'enabled' if auto_tick else 'disabled'}"
                ui_attr = palette["running"] if auto_tick else palette["muted"]
                continue
            if key in (ord("a"), ord("A")):
                project_id = None if selected_project_idx == 0 else int(projects[selected_project_idx - 1]["id"]) if projects else None
                ok, msg, attr = _alerts_dispatcher_modal(stdscr, palette, conn, project_id)
                ui_message = msg
                if ok:
                    ui_attr = attr
                else:
                    ui_attr = palette["failed"]
                force_refresh = True
                continue

            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                if focus == FOCUS_PROJECTS:
                    selected_project_idx = min(selected_project_idx + 1, len(projects))
                    selected_batch_idx = 0
                    force_refresh = True
                elif focus == FOCUS_BATCHES:
                    selected_batch_idx = min(selected_batch_idx + 1, len(batches))
                    force_refresh = True
                elif focus == FOCUS_TASKS:
                    selected_task_idx = min(selected_task_idx + 1, max(0, len(tasks) - 1))
                else:
                    selected_session_idx = min(selected_session_idx + 1, max(0, len(sessions) - 1))
                continue
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                if focus == FOCUS_PROJECTS:
                    selected_project_idx = max(selected_project_idx - 1, 0)
                    selected_batch_idx = 0
                    force_refresh = True
                elif focus == FOCUS_BATCHES:
                    selected_batch_idx = max(selected_batch_idx - 1, 0)
                    force_refresh = True
                elif focus == FOCUS_TASKS:
                    selected_task_idx = max(selected_task_idx - 1, 0)
                else:
                    selected_session_idx = max(selected_session_idx - 1, 0)
                continue

            if key in (ord("n"), ord("N")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a concrete project first."
                    ui_attr = palette["warn"]
                    continue
                project = projects[selected_project_idx - 1]
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title=f"Queue Task [{project['name']}]",
                    labels=["Title", "Description", "Runtime (tmux|local_pty)", "Agent Command"],
                    defaults=["New task", "", "tmux", "codex"],
                    hint="Enter queue task | Esc cancel",
                )
                if fields is None:
                    ui_message = "Task queue cancelled."
                    ui_attr = palette["muted"]
                    continue
                title, desc, runtime_raw, agent = fields
                runtime_raw = runtime_raw.strip().lower() or "tmux"
                if runtime_raw not in {RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value}:
                    ui_message = f"Unsupported runtime: {runtime_raw}"
                    ui_attr = palette["failed"]
                    continue
                try:
                    task_id = queue_demo_task_for_project(
                        conn=conn,
                        project_name=str(project["name"]),
                        title=title,
                        description=desc,
                        runtime_kind=RuntimeKind(runtime_raw),
                        preferred_agent=agent or None,
                    )
                    ui_message = f"Queued task #{task_id}."
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to queue task: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("o"), ord("O")):
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title="Project Onboarding",
                    labels=["Project name", "Repo root path", "Guidelines file (optional)"],
                    defaults=["", str(Path.cwd()), ""],
                    hint="Enter adds/updates project | Esc cancel",
                )
                if fields is None:
                    ui_message = "Project onboarding cancelled."
                    ui_attr = palette["muted"]
                    continue
                name, root_path, guidelines_file = fields
                try:
                    project_id = _upsert_project_from_onboarding(
                        conn=conn,
                        name=name,
                        root_path=root_path,
                        guidelines_file=(guidelines_file or None),
                    )
                    ui_message = f"Project upserted: id={project_id} name={name}"
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Project onboarding failed: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("b"), ord("B")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a concrete project first."
                    ui_attr = palette["warn"]
                    continue
                project = projects[selected_project_idx - 1]
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title=f"Queue Batch [{project['name']}]",
                    labels=[
                        "Batch name",
                        "Default runtime (tmux|local_pty)",
                        "Default agent command",
                        "Base priority (0-100)",
                        "Auto-start now? (y/n)",
                        "Editor command (optional)",
                    ],
                    defaults=[f"batch-{int(time.time())}", "tmux", "codex", "50", "y", ""],
                    hint="Enter opens editor for task list | Esc cancel",
                )
                if fields is None:
                    ui_message = "Batch queue cancelled."
                    ui_attr = palette["muted"]
                    continue
                batch_name, runtime_raw, agent, priority_raw, auto_start_raw, editor = fields
                runtime_raw = runtime_raw.strip().lower() or "tmux"
                if runtime_raw not in {RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value}:
                    ui_message = f"Unsupported runtime: {runtime_raw}"
                    ui_attr = palette["failed"]
                    continue
                try:
                    base_priority = int(priority_raw)
                except ValueError:
                    ui_message = f"Invalid base priority: {priority_raw}"
                    ui_attr = palette["failed"]
                    continue
                base_priority = max(0, min(100, base_priority))
                auto_start = auto_start_raw.strip().lower() in {"y", "yes", "1", "true"}
                try:
                    task_text = _with_curses_paused(
                        stdscr,
                        lambda: _edit_batch_tasks_in_editor(str(project["name"]), editor=(editor or None)),
                    )
                    batch_id, task_ids = create_batch_from_task_text(
                        conn=conn,
                        project_id=int(project["id"]),
                        batch_name=batch_name,
                        task_text=task_text,
                        default_runtime=RuntimeKind(runtime_raw),
                        default_agent=agent or None,
                        default_priority=base_priority,
                    )
                    if auto_start:
                        stats = control_plane.tick()
                        ui_message = (
                            f"Queued batch #{batch_id} ({len(task_ids)} tasks). "
                            f"tick: dispatched={stats.dispatched} completed={stats.completed} "
                            f"failed={stats.failed} stuck={stats.stuck}"
                        )
                    else:
                        ui_message = f"Queued batch #{batch_id} with {len(task_ids)} tasks."
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to queue batch: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("y"), ord("Y")):
                if not tasks:
                    ui_message = "No task selected."
                    ui_attr = palette["warn"]
                    continue
                task = tasks[selected_task_idx]
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title=f"Reply Task #{task['id']}",
                    labels=["Reply"],
                    defaults=[""],
                    hint="Enter sends operator reply | Esc cancel",
                )
                if fields is None:
                    ui_message = "Task reply cancelled."
                    ui_attr = palette["muted"]
                    continue
                reply = fields[0]
                try:
                    control_plane.reply_to_task(int(task["id"]), reply)
                    ui_message = f"Sent reply to task #{task['id']}."
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to reply to task #{task['id']}: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("e"), ord("E")):
                if selected_project_idx == 0 or not projects:
                    ui_message = "Select a concrete project first."
                    ui_attr = palette["warn"]
                    continue
                project = projects[selected_project_idx - 1]
                fields = _prompt_fields(
                    stdscr=stdscr,
                    palette=palette,
                    title=f"Roadmap Edit [{project['name']}]",
                    labels=["Roadmap path", "Roadmap name", "Editor command (optional)"],
                    defaults=["roadmap.md", "roadmap", ""],
                    hint="Enter open editor | Esc cancel",
                )
                if fields is None:
                    ui_message = "Roadmap edit cancelled."
                    ui_attr = palette["muted"]
                    continue
                roadmap_path, roadmap_name, editor = fields
                try:
                    roadmap_id, revision_id = _with_curses_paused(
                        stdscr,
                        lambda: edit_roadmap_for_project(
                            db_path=resolved_db,
                            project_name=str(project["name"]),
                            roadmap_path=roadmap_path,
                            roadmap_name=roadmap_name or "roadmap",
                            editor=(editor or None),
                        ),
                    )
                    project_root = Path(str(project["root_path"])).resolve()
                    effective_path = Path(roadmap_path)
                    if not effective_path.is_absolute():
                        effective_path = (project_root / effective_path).resolve()
                    ok, msg = validate_roadmap(effective_path)
                    ui_message = (
                        f"roadmap_id={roadmap_id} revision_id={revision_id} | "
                        + (msg if ok else f"invalid: {msg}")
                    )
                    ui_attr = palette["running"] if ok else palette["warn"]
                except Exception as exc:
                    ui_message = f"Roadmap edit failed: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("w"), ord("W")):
                if not sessions:
                    ui_message = "No session selected."
                    ui_attr = palette["warn"]
                    continue
                selected = sessions[selected_session_idx]
                ok, msg = _open_workspace(stdscr, palette, control_plane, selected)
                ui_message = msg
                ui_attr = palette["running"] if ok else palette["failed"]
                force_refresh = True
                continue

            if key in (ord("p"), ord("P")):
                if selected_batch_idx == 0 or not batches:
                    ui_message = "Select a concrete batch first."
                    ui_attr = palette["warn"]
                    continue
                batch = batches[selected_batch_idx - 1]
                try:
                    result = control_plane.pause_batch(int(batch["id"]))
                    ui_message = (
                        f"Paused batch #{batch['id']} ({batch['name']}): "
                        f"tasks_changed={result.task_rows_changed} sessions_ended={result.sessions_ended}"
                    )
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to pause batch #{batch['id']}: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("u"), ord("U")):
                if selected_batch_idx == 0 or not batches:
                    ui_message = "Select a concrete batch first."
                    ui_attr = palette["warn"]
                    continue
                batch = batches[selected_batch_idx - 1]
                try:
                    result = control_plane.resume_batch(int(batch["id"]))
                    ui_message = f"Resumed batch #{batch['id']} ({batch['name']}): tasks_changed={result.task_rows_changed}"
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to resume batch #{batch['id']}: {exc}"
                    ui_attr = palette["failed"]
                continue

            if key in (ord("x"), ord("X")):
                if selected_batch_idx == 0 or not batches:
                    ui_message = "Select a concrete batch first."
                    ui_attr = palette["warn"]
                    continue
                batch = batches[selected_batch_idx - 1]
                try:
                    result = control_plane.preempt_batch(int(batch["id"]))
                    ui_message = (
                        f"Preempted batch #{batch['id']} ({batch['name']}): "
                        f"tasks_changed={result.task_rows_changed} sessions_ended={result.sessions_ended}"
                    )
                    ui_attr = palette["running"]
                    force_refresh = True
                except Exception as exc:
                    ui_message = f"Failed to preempt batch #{batch['id']}: {exc}"
                    ui_attr = palette["failed"]
                continue

    curses.wrapper(_main)
