from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db.schema import utc_now
from .models import RuntimeKind


def create_project(conn: sqlite3.Connection, name: str, root_path: str | Path, guidelines: str = "") -> int:
    root = str(Path(root_path).expanduser().resolve())
    conn.execute(
        f"""
        INSERT INTO projects(name, root_path, guidelines, created_at, updated_at)
        VALUES (?, ?, ?, ({utc_now()}), ({utc_now()}))
        ON CONFLICT(name) DO UPDATE SET
            root_path = excluded.root_path,
            guidelines = excluded.guidelines,
            updated_at = ({utc_now()})
        """,
        (name, root, guidelines),
    )
    row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError("failed to create or update project")
    conn.commit()
    return int(row["id"])


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name, root_path, guidelines, created_at, updated_at
        FROM projects
        ORDER BY name
        """
    ).fetchall()


def get_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name, root_path, guidelines, created_at, updated_at
        FROM projects
        WHERE name = ?
        """,
        (name,),
    ).fetchone()


def upsert_roadmap(conn: sqlite3.Connection, project_id: int, name: str, path: str | Path, status: str = "draft") -> int:
    resolved = str(Path(path).expanduser().resolve())
    row = conn.execute(
        """
        SELECT id
        FROM roadmaps
        WHERE project_id = ? AND path = ?
        """,
        (project_id, resolved),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            f"""
            INSERT INTO roadmaps(project_id, name, path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ({utc_now()}), ({utc_now()}))
            """,
            (project_id, name, resolved, status),
        )
        roadmap_id = int(cur.lastrowid)
    else:
        roadmap_id = int(row["id"])
        conn.execute(
            f"""
            UPDATE roadmaps
            SET name = ?,
                status = ?,
                updated_at = ({utc_now()})
            WHERE id = ?
            """,
            (name, status, roadmap_id),
        )
    conn.commit()
    return roadmap_id


def add_roadmap_revision(conn: sqlite3.Connection, roadmap_id: int, source: str, raw_text: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(revision_no), 0) AS mx FROM roadmap_revisions WHERE roadmap_id = ?",
        (roadmap_id,),
    ).fetchone()
    revision_no = int(row["mx"]) + 1 if row else 1
    cur = conn.execute(
        f"""
        INSERT INTO roadmap_revisions(roadmap_id, revision_no, source, raw_text, created_at)
        VALUES (?, ?, ?, ?, ({utc_now()}))
        """,
        (roadmap_id, revision_no, source, raw_text),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_task_batch(
    conn: sqlite3.Connection, project_id: int, name: str, roadmap_id: int | None = None, status: str = "draft"
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO task_batches(project_id, roadmap_id, name, status, created_at)
        VALUES (?, ?, ?, ?, ({utc_now()}))
        """,
        (project_id, roadmap_id, name, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_task(
    conn: sqlite3.Connection,
    batch_id: int,
    project_id: int,
    title: str,
    description: str = "",
    priority: int = 50,
    runtime_kind: RuntimeKind = RuntimeKind.TMUX,
    preferred_agent: str | None = None,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO tasks(
            batch_id, project_id, title, description, status, priority, runtime_kind, preferred_agent, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ({utc_now()}), ({utc_now()}))
        """,
        (batch_id, project_id, title, description, priority, runtime_kind.value, preferred_agent),
    )
    conn.commit()
    return int(cur.lastrowid)


def _normalize_task_line(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^\s*[-*]\s+\[[ xX]\]\s*", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+\.\s+", "", text)
    return text.strip()


def _parse_directives(raw: str) -> tuple[str, dict[str, str]]:
    directives: dict[str, str] = {}
    for match in re.finditer(r"@([a-zA-Z_]+)=([^\s]+)", raw):
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if key:
            directives[key] = value
    cleaned = re.sub(r"\s*@([a-zA-Z_]+)=([^\s]+)", "", raw).strip()
    return cleaned, directives


def parse_freeform_tasks(
    task_text: str,
    default_runtime: RuntimeKind = RuntimeKind.TMUX,
    default_agent: str | None = None,
    default_priority: int = 50,
) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    base_priority = max(0, min(100, int(default_priority)))
    for line in task_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        normalized = _normalize_task_line(stripped)
        if not normalized:
            continue
        normalized, directives = _parse_directives(normalized)
        if not normalized:
            continue

        runtime = default_runtime
        preferred_agent = default_agent
        priority = base_priority

        runtime_raw = directives.get("runtime", directives.get("rt"))
        if runtime_raw:
            candidate = runtime_raw.strip().lower()
            if candidate in {RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value}:
                runtime = RuntimeKind(candidate)

        agent_raw = directives.get("agent", directives.get("a"))
        if agent_raw is not None:
            preferred_agent = agent_raw.strip() or None

        priority_raw = directives.get("priority", directives.get("p"))
        if priority_raw:
            try:
                priority = max(0, min(100, int(priority_raw)))
            except ValueError:
                priority = base_priority

        tasks.append(
            {
                "title": normalized,
                "priority": priority,
                "runtime_kind": runtime,
                "preferred_agent": preferred_agent,
            }
        )
    return tasks


def create_batch_from_task_text(
    conn: sqlite3.Connection,
    project_id: int,
    batch_name: str,
    task_text: str,
    default_runtime: RuntimeKind = RuntimeKind.TMUX,
    default_agent: str | None = None,
    default_priority: int = 50,
) -> tuple[int, list[int]]:
    parsed = parse_freeform_tasks(
        task_text=task_text,
        default_runtime=default_runtime,
        default_agent=default_agent,
        default_priority=default_priority,
    )
    if not parsed:
        raise ValueError("no tasks found in free-form input")

    batch_id = create_task_batch(
        conn,
        project_id=project_id,
        name=batch_name.strip() or "batch",
        roadmap_id=None,
        status="queued",
    )
    task_ids: list[int] = []
    for task in parsed:
        task_id = create_task(
            conn,
            batch_id=batch_id,
            project_id=project_id,
            title=str(task["title"]),
            description="",
            priority=int(task["priority"]),
            runtime_kind=task["runtime_kind"],  # type: ignore[arg-type]
            preferred_agent=task["preferred_agent"],  # type: ignore[arg-type]
        )
        task_ids.append(task_id)
    return batch_id, task_ids


def replace_batch_open_tasks(
    conn: sqlite3.Connection,
    batch_id: int,
    task_text: str,
    default_runtime: RuntimeKind = RuntimeKind.TMUX,
    default_agent: str | None = None,
    default_priority: int = 50,
) -> list[int]:
    batch_row = conn.execute(
        """
        SELECT id, project_id, name
        FROM task_batches
        WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    if batch_row is None:
        raise ValueError(f"batch not found: {batch_id}")

    parsed = parse_freeform_tasks(
        task_text=task_text,
        default_runtime=default_runtime,
        default_agent=default_agent,
        default_priority=default_priority,
    )
    if not parsed:
        raise ValueError("no tasks found in free-form input")

    conn.execute(
        """
        UPDATE tasks
        SET status = 'preempted',
            updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        WHERE batch_id = ? AND status IN ('queued', 'running', 'paused', 'awaiting_input')
        """,
        (batch_id,),
    )

    project_id = int(batch_row["project_id"])
    task_ids: list[int] = []
    for task in parsed:
        task_id = create_task(
            conn,
            batch_id=batch_id,
            project_id=project_id,
            title=str(task["title"]),
            description="",
            priority=int(task["priority"]),
            runtime_kind=task["runtime_kind"],  # type: ignore[arg-type]
            preferred_agent=task["preferred_agent"],  # type: ignore[arg-type]
        )
        task_ids.append(task_id)
    conn.execute(
        """
        UPDATE task_batches
        SET status = 'queued'
        WHERE id = ?
        """,
        (batch_id,),
    )
    conn.commit()
    return task_ids


def queue_demo_task_for_project(
    conn: sqlite3.Connection,
    project_name: str,
    title: str,
    description: str = "",
    runtime_kind: RuntimeKind = RuntimeKind.TMUX,
    preferred_agent: str | None = None,
) -> int:
    project = get_project(conn, project_name)
    if project is None:
        raise ValueError(f"project not found: {project_name}")
    batch_id = create_task_batch(
        conn,
        project_id=int(project["id"]),
        roadmap_id=None,
        name=f"adhoc-{title[:24]}",
        status="queued",
    )
    return create_task(
        conn,
        batch_id=batch_id,
        project_id=int(project["id"]),
        title=title,
        description=description,
        runtime_kind=runtime_kind,
        preferred_agent=preferred_agent,
    )


def add_dispatcher_decision(
    conn: sqlite3.Connection,
    proposal: dict[str, Any],
    batch_id: int | None = None,
    task_id: int | None = None,
    rationale: str = "",
    confidence: float | None = None,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO dispatcher_decisions(batch_id, task_id, proposal_json, rationale, confidence, applied, overridden, created_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ({utc_now()}))
        """,
        (batch_id, task_id, json.dumps(proposal, sort_keys=True), rationale, confidence),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_dispatcher_decision(conn: sqlite3.Connection, decision_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, batch_id, task_id, proposal_json, rationale, confidence, applied, overridden, created_at
        FROM dispatcher_decisions
        WHERE id = ?
        """,
        (decision_id,),
    ).fetchone()


def apply_dispatcher_decision(conn: sqlite3.Connection, decision_id: int) -> None:
    decision = get_dispatcher_decision(conn, decision_id)
    if decision is None:
        raise ValueError(f"dispatcher decision not found: {decision_id}")
    proposal = json.loads(str(decision["proposal_json"]))
    task_id = int(decision["task_id"]) if decision["task_id"] is not None else None
    if task_id is None:
        raise ValueError("dispatcher decision has no task_id to apply")

    updates: list[str] = [f"updated_at = ({utc_now()})"]
    params: list[object] = []
    preferred_agent = proposal.get("preferred_agent")
    runtime_kind = proposal.get("runtime_kind")
    priority = proposal.get("priority")
    if preferred_agent is not None:
        updates.append("preferred_agent = ?")
        params.append(str(preferred_agent))
    if runtime_kind is not None:
        rk = str(runtime_kind).strip().lower()
        if rk not in {RuntimeKind.TMUX.value, RuntimeKind.LOCAL_PTY.value}:
            raise ValueError(f"unsupported runtime_kind in decision: {runtime_kind}")
        updates.append("runtime_kind = ?")
        params.append(rk)
    if priority is not None:
        updates.append("priority = ?")
        params.append(int(priority))

    params.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
    conn.execute(
        "UPDATE dispatcher_decisions SET applied = 1, overridden = 0 WHERE id = ?",
        (decision_id,),
    )
    if task_id is not None:
        conn.execute(
            """
            UPDATE dispatcher_decisions
            SET overridden = 1
            WHERE task_id = ? AND id <> ? AND applied = 0
            """,
            (task_id, decision_id),
        )
    conn.commit()


def apply_latest_dispatcher_decision(conn: sqlite3.Connection, task_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM dispatcher_decisions
        WHERE task_id = ? AND applied = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    decision_id = int(row["id"])
    apply_dispatcher_decision(conn, decision_id)
    return decision_id


def add_usage_record(
    conn: sqlite3.Connection,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    source: str = "adapter",
    session_id: int | None = None,
    task_id: int | None = None,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO usage_records(
            session_id, task_id, provider, model, input_tokens, output_tokens, cost_usd, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ({utc_now()}))
        """,
        (session_id, task_id, provider, model, int(input_tokens), int(output_tokens), float(cost_usd), source),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_usage_snapshot(
    conn: sqlite3.Connection,
    session_id: int,
    task_id: int | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    source: str = "runtime_parse",
) -> int | None:
    provider_norm = provider.strip() or "unknown"
    model_norm = model.strip() or "unknown"
    current_input = max(0, int(input_tokens))
    current_output = max(0, int(output_tokens))
    current_cost = max(0.0, float(cost_usd))

    previous = conn.execute(
        """
        SELECT input_tokens, output_tokens, cost_usd
        FROM usage_ingestion_state
        WHERE session_id = ? AND provider = ? AND model = ?
        """,
        (session_id, provider_norm, model_norm),
    ).fetchone()

    if previous is None:
        delta_input = current_input
        delta_output = current_output
        delta_cost = current_cost
        next_input = current_input
        next_output = current_output
        next_cost = current_cost
    else:
        prev_input = int(previous["input_tokens"])
        prev_output = int(previous["output_tokens"])
        prev_cost = float(previous["cost_usd"])
        delta_input = max(0, current_input - prev_input)
        delta_output = max(0, current_output - prev_output)
        delta_cost = max(0.0, current_cost - prev_cost)
        next_input = max(prev_input, current_input)
        next_output = max(prev_output, current_output)
        next_cost = max(prev_cost, current_cost)

    conn.execute(
        f"""
        INSERT INTO usage_ingestion_state(session_id, provider, model, input_tokens, output_tokens, cost_usd, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ({utc_now()}))
        ON CONFLICT(session_id, provider, model)
        DO UPDATE SET
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cost_usd = excluded.cost_usd,
            updated_at = excluded.updated_at
        """,
        (session_id, provider_norm, model_norm, next_input, next_output, next_cost),
    )

    if delta_input == 0 and delta_output == 0 and delta_cost <= 0:
        conn.commit()
        return None

    cur = conn.execute(
        f"""
        INSERT INTO usage_records(
            session_id, task_id, provider, model, input_tokens, output_tokens, cost_usd, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ({utc_now()}))
        """,
        (session_id, task_id, provider_norm, model_norm, delta_input, delta_output, delta_cost, source),
    )
    conn.commit()
    return int(cur.lastrowid)


def usage_summary(conn: sqlite3.Connection, project_id: int | None = None) -> list[sqlite3.Row]:
    if project_id is None:
        return conn.execute(
            """
            SELECT provider, model,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd) AS cost_usd,
                   COUNT(*) AS records
            FROM usage_records
            GROUP BY provider, model
            ORDER BY cost_usd DESC
            """
        ).fetchall()
    return conn.execute(
        """
        SELECT u.provider, u.model,
               SUM(u.input_tokens) AS input_tokens,
               SUM(u.output_tokens) AS output_tokens,
               SUM(u.cost_usd) AS cost_usd,
               COUNT(*) AS records
        FROM usage_records u
        JOIN tasks t ON t.id = u.task_id
        WHERE t.project_id = ?
        GROUP BY u.provider, u.model
        ORDER BY cost_usd DESC
        """,
        (project_id,),
    ).fetchall()
