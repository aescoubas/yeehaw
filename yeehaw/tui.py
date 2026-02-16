from __future__ import annotations

import curses
import time
from pathlib import Path
from typing import Any

from . import db


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

    return {
        "header": curses.color_pair(1) | curses.A_BOLD,
        "border": curses.color_pair(2),
        "selected": curses.color_pair(7) | curses.A_BOLD,
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


def run_tui(db_path: str | Path | None = None, refresh_seconds: float = 1.0) -> None:
    resolved_db = Path(db_path).resolve() if db_path else db.default_db_path()
    conn = db.connect(resolved_db)

    def _draw(stdscr: curses.window) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        palette = _init_colors()

        selected_idx = 0
        force_refresh = True
        last_refresh = 0.0

        runs: list[Any] = []
        tracks: list[Any] = []
        events: list[Any] = []
        status_counts: dict[str, int] = {}
        project_count = 0
        total_runs = 0

        while True:
            now = time.monotonic()
            if force_refresh or now - last_refresh >= max(0.2, refresh_seconds):
                runs = db.latest_runs(conn, limit=50)
                selected_idx = max(0, min(selected_idx, len(runs) - 1)) if runs else 0

                project_count = int(conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"])
                total_runs = int(conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"])
                status_counts = _fetch_status_counts(conn)

                if runs:
                    selected_run_id = int(runs[selected_idx]["id"])
                    tracks = db.run_tracks(conn, selected_run_id)
                    events = db.run_events(conn, selected_run_id, limit=60)
                else:
                    tracks = []
                    events = []

                last_refresh = now
                force_refresh = False

            h, w = stdscr.getmaxyx()
            stdscr.erase()

            if h < 20 or w < 90:
                _safe_add(stdscr, 0, 0, "Terminal too small for dashboard. Resize to at least 90x20.", palette["failed"])
                _safe_add(stdscr, 2, 0, "Controls: q quit | r refresh", palette["muted"])
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
            footer_h = 2
            body_y = header_h + stats_h
            body_h = h - body_y - footer_h

            left_w = int(w * 0.46)
            left_w = max(40, min(left_w, w - 36))
            right_w = w - left_w

            tracks_h = max(8, body_h // 2)
            events_h = body_h - tracks_h

            header = _new_panel(stdscr, 0, 0, header_h, w, "YEEHAW DASHBOARD", palette["border"])
            stats = _new_panel(stdscr, header_h, 0, stats_h, w, "System", palette["border"])
            runs_panel = _new_panel(stdscr, body_y, 0, body_h, left_w, "Runs", palette["border"])
            tracks_panel = _new_panel(stdscr, body_y, left_w, tracks_h, right_w, "Tracks (Selected Run)", palette["border"])
            events_panel = _new_panel(
                stdscr,
                body_y + tracks_h,
                left_w,
                events_h,
                right_w,
                "Recent Events (Selected Run)",
                palette["border"],
            )

            if header:
                _safe_add(
                    header,
                    1,
                    2,
                    f"DB: {resolved_db} | Updated: {time.strftime('%H:%M:%S')} | q quit  j/k move  r refresh",
                    palette["header"],
                )

            if stats:
                cards = [
                    ("Projects", str(project_count), palette["info"]),
                    ("Runs", str(total_runs), palette["info"]),
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

            if runs_panel:
                if not runs:
                    _safe_add(runs_panel, 1, 2, "No runs yet.", palette["muted"])
                else:
                    max_rows = runs_panel.getmaxyx()[0] - 2
                    for idx, run in enumerate(runs[:max_rows]):
                        status = str(run["status"])
                        line = (
                            f"#{run['id']:<4} {status:<14} "
                            f"{run['project_name']}/{run['roadmap_name']}"
                        )
                        if idx == selected_idx:
                            _safe_add(runs_panel, 1 + idx, 1, ">", palette["selected"])
                            _safe_add(runs_panel, 1 + idx, 3, line, palette["selected"])
                        else:
                            _safe_add(runs_panel, 1 + idx, 3, line, _status_attr(status, palette))

            if tracks_panel:
                if not runs:
                    _safe_add(tracks_panel, 1, 2, "Select a run to inspect tracks.", palette["muted"])
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

            if events_panel:
                if not runs:
                    _safe_add(events_panel, 1, 2, "No events yet.", palette["muted"])
                else:
                    max_rows = events_panel.getmaxyx()[0] - 2
                    for idx, event in enumerate(events[:max_rows]):
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
                if runs:
                    selected = runs[selected_idx]
                    detail = (
                        f"Selected run #{selected['id']} | status={selected['status']} | "
                        f"session={selected['tmux_session']}"
                    )
                    _safe_add(footer, 0, 2, detail, _status_attr(str(selected["status"]), palette))
                else:
                    _safe_add(footer, 0, 2, "No runs available", palette["muted"])

            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("r"), ord("R")):
                force_refresh = True
            elif key in (curses.KEY_DOWN, ord("j"), ord("J")) and runs:
                selected_idx = min(len(runs) - 1, selected_idx + 1)
                force_refresh = True
            elif key in (curses.KEY_UP, ord("k"), ord("K")) and runs:
                selected_idx = max(0, selected_idx - 1)
                force_refresh = True
            elif key in (curses.KEY_NPAGE,) and runs:
                selected_idx = min(len(runs) - 1, selected_idx + 10)
                force_refresh = True
            elif key in (curses.KEY_PPAGE,) and runs:
                selected_idx = max(0, selected_idx - 10)
                force_refresh = True
            elif key in (ord("g"),) and runs:
                selected_idx = 0
                force_refresh = True
            elif key in (ord("G"),) and runs:
                selected_idx = len(runs) - 1
                force_refresh = True

            time.sleep(0.06)

    curses.wrapper(_draw)
