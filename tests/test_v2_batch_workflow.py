from __future__ import annotations

from pathlib import Path

import pytest

from yeehaw_v2.db import connect as connect_db
from yeehaw_v2.models import RuntimeKind
from yeehaw_v2.store import create_batch_from_task_text, create_project, parse_freeform_tasks, replace_batch_open_tasks


def test_parse_freeform_tasks_supports_directives_and_bullets() -> None:
    text = "\n".join(
        [
            "# comment",
            "- [ ] Build API client @priority=80 @runtime=local_pty @agent=claude",
            "2. Add tests @p=65",
            "* Ship release notes @rt=tmux @a=codex",
            "",
        ]
    )
    tasks = parse_freeform_tasks(
        task_text=text,
        default_runtime=RuntimeKind.TMUX,
        default_agent="codex",
        default_priority=50,
    )
    assert len(tasks) == 3
    assert tasks[0]["title"] == "Build API client"
    assert tasks[0]["priority"] == 80
    assert tasks[0]["runtime_kind"] == RuntimeKind.LOCAL_PTY
    assert tasks[0]["preferred_agent"] == "claude"

    assert tasks[1]["title"] == "Add tests"
    assert tasks[1]["priority"] == 65
    assert tasks[1]["runtime_kind"] == RuntimeKind.TMUX
    assert tasks[1]["preferred_agent"] == "codex"

    assert tasks[2]["title"] == "Ship release notes"
    assert tasks[2]["priority"] == 50
    assert tasks[2]["runtime_kind"] == RuntimeKind.TMUX
    assert tasks[2]["preferred_agent"] == "codex"


def test_create_batch_from_task_text_creates_queued_tasks(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_batch.db")
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id, task_ids = create_batch_from_task_text(
        conn=conn,
        project_id=project_id,
        batch_name="feature-batch",
        task_text="Task A @priority=70\nTask B @runtime=local_pty",
        default_runtime=RuntimeKind.TMUX,
        default_agent="codex",
        default_priority=40,
    )
    assert batch_id > 0
    assert len(task_ids) == 2

    batch_row = conn.execute("SELECT name, status FROM task_batches WHERE id = ?", (batch_id,)).fetchone()
    assert batch_row is not None
    assert batch_row["name"] == "feature-batch"
    assert batch_row["status"] == "queued"

    rows = conn.execute(
        "SELECT title, status, priority, runtime_kind, preferred_agent FROM tasks WHERE batch_id = ? ORDER BY id ASC",
        (batch_id,),
    ).fetchall()
    assert [row["title"] for row in rows] == ["Task A", "Task B"]
    assert all(row["status"] == "queued" for row in rows)
    assert int(rows[0]["priority"]) == 70
    assert rows[0]["runtime_kind"] == "tmux"
    assert rows[0]["preferred_agent"] == "codex"
    assert int(rows[1]["priority"]) == 40
    assert rows[1]["runtime_kind"] == "local_pty"
    assert rows[1]["preferred_agent"] == "codex"


def test_create_batch_from_task_text_requires_nonempty_input(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_batch_empty.db")
    project_id = create_project(conn, "demo", tmp_path / "repo")
    with pytest.raises(ValueError, match="no tasks found"):
        create_batch_from_task_text(
            conn=conn,
            project_id=project_id,
            batch_name="empty",
            task_text="# only comments\n\n",
            default_runtime=RuntimeKind.TMUX,
            default_agent="codex",
            default_priority=50,
        )


def test_replace_batch_open_tasks_preempts_open_and_adds_new(tmp_path: Path) -> None:
    conn = connect_db(tmp_path / "v2_replace_batch.db")
    project_id = create_project(conn, "demo", tmp_path / "repo")
    batch_id, task_ids = create_batch_from_task_text(
        conn=conn,
        project_id=project_id,
        batch_name="b",
        task_text="T1\nT2",
        default_runtime=RuntimeKind.TMUX,
        default_agent="codex",
        default_priority=50,
    )
    conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (task_ids[0],))
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_ids[1],))
    conn.commit()

    new_ids = replace_batch_open_tasks(
        conn=conn,
        batch_id=batch_id,
        task_text="N1 @priority=80 @runtime=local_pty @agent=claude\nN2",
        default_runtime=RuntimeKind.TMUX,
        default_agent="codex",
        default_priority=50,
    )
    assert len(new_ids) == 2

    rows = conn.execute(
        "SELECT id, title, status, priority, runtime_kind, preferred_agent FROM tasks WHERE batch_id = ? ORDER BY id ASC",
        (batch_id,),
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    assert by_id[task_ids[0]]["status"] == "preempted"
    assert by_id[task_ids[1]]["status"] == "completed"
    assert by_id[new_ids[0]]["title"] == "N1"
    assert by_id[new_ids[0]]["status"] == "queued"
    assert int(by_id[new_ids[0]]["priority"]) == 80
    assert by_id[new_ids[0]]["runtime_kind"] == "local_pty"
    assert by_id[new_ids[0]]["preferred_agent"] == "claude"
    assert by_id[new_ids[1]]["title"] == "N2"
