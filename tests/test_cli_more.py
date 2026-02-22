"""Additional tests covering CLI handlers and dispatch branches."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import subprocess
from typing import Any

import pytest

import yeehaw.cli.main as cli_main
import yeehaw.cli.attach as cli_attach
import yeehaw.cli.logs as cli_logs
import yeehaw.cli.plan as cli_plan
import yeehaw.cli.roadmap as cli_roadmap
import yeehaw.cli.run as cli_run
import yeehaw.cli.scheduler as cli_scheduler
import yeehaw.cli.status as cli_status
import yeehaw.cli.stop as cli_stop
import yeehaw.cli.workers as cli_workers
from yeehaw.store.store import Store


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / ".yeehaw" / "yeehaw.db"


def _seed_project_with_task(db_path: Path, task_status: str = "pending") -> dict[str, Any]:
    store = Store(db_path)
    try:
        existing = store.get_project("proj-a")
        if existing is None:
            project_id = store.create_project("proj-a", "/tmp/repo-a")
        else:
            project_id = existing["id"]
        roadmap_id = store.create_roadmap(project_id, "# Roadmap")
        phase_id = store.create_phase(roadmap_id, 1, "Phase 1", "pytest -q")
        task_id = store.create_task(roadmap_id, phase_id, "1.1", "Task 1", "desc")

        if task_status == "queued":
            store.queue_task(task_id)
        elif task_status == "in-progress":
            store.assign_task(
                task_id,
                agent="codex",
                branch="b",
                worktree="/tmp/worktree",
                signal_dir="/tmp/signal",
            )
        elif task_status == "done":
            store.complete_task(task_id, "done")
        elif task_status == "failed":
            store.fail_task(task_id, "boom")
        elif task_status == "blocked":
            store.complete_task(task_id, "blocked")

        return {
            "project_id": project_id,
            "roadmap_id": roadmap_id,
            "phase_id": phase_id,
            "task_id": task_id,
        }
    finally:
        store.close()


def test_cli_main_dispatches_remaining_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    calls: list[tuple[str, Path]] = []

    def _capture(name: str):
        def _handler(*args: Any) -> None:
            db = args[-1]
            calls.append((name, db))

        return _handler

    monkeypatch.setattr("yeehaw.cli.project.handle_init", _capture("init"))
    monkeypatch.setattr("yeehaw.cli.roadmap.handle_roadmap", _capture("roadmap"))
    monkeypatch.setattr("yeehaw.cli.plan.handle_plan", _capture("plan"))
    monkeypatch.setattr("yeehaw.cli.run.handle_run", _capture("run"))
    monkeypatch.setattr("yeehaw.cli.attach.handle_attach", _capture("attach"))
    monkeypatch.setattr("yeehaw.cli.stop.handle_stop", _capture("stop"))
    monkeypatch.setattr("yeehaw.cli.logs.handle_logs", _capture("logs"))
    monkeypatch.setattr("yeehaw.cli.scheduler.handle_scheduler", _capture("scheduler"))
    monkeypatch.setattr("yeehaw.cli.status.handle_alerts", _capture("alerts"))
    monkeypatch.setattr("yeehaw.cli.workers.handle_workers", _capture("workers"))

    cli_main.main(["init"])
    cli_main.main(["roadmap", "show", "--project", "p"])  # routing only
    cli_main.main(["roadmap", "clear", "--project", "p"])  # routing only
    cli_main.main(
        ["roadmap", "generate", "--project", "p", "--prompt", "build the project roadmap"]
    )
    cli_main.main(["plan"])
    cli_main.main(["run", "--agent", "codex"])
    cli_main.main(["attach", "1"])
    cli_main.main(["stop", "1"])
    cli_main.main(["logs", "1"])
    cli_main.main(["scheduler", "show"])
    cli_main.main(["alerts"])
    cli_main.main(["workers", "show"])

    names = [name for name, _ in calls]
    assert names == [
        "init",
        "roadmap",
        "roadmap",
        "roadmap",
        "plan",
        "run",
        "attach",
        "stop",
        "logs",
        "scheduler",
        "alerts",
        "workers",
    ]
    assert all(db == tmp_path / ".yeehaw" / "yeehaw.db" for _, db in calls)


def test_handle_roadmap_create_branches(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = Namespace(roadmap_command="create", file="missing.md", project="missing")
    cli_roadmap.handle_roadmap(args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    project_id = store.create_project("proj-a", "/tmp/repo-a")
    store.close()

    args = Namespace(roadmap_command="create", file="missing.md", project="proj-a")
    cli_roadmap.handle_roadmap(args, db_path)
    assert "File 'missing.md' not found" in capsys.readouterr().out

    bad_path = db_path.parent / "bad.md"
    bad_path.write_text("## Phase 1: Missing header")
    args = Namespace(roadmap_command="create", file=str(bad_path), project="proj-a")
    cli_roadmap.handle_roadmap(args, db_path)
    assert "Missing roadmap header" in capsys.readouterr().out

    invalid_path = db_path.parent / "invalid.md"
    invalid_path.write_text(
        "# Roadmap: proj-a\n## Phase 2: Wrong\n### Task 2.1: Bad\nbody\n"
    )
    args = Namespace(roadmap_command="create", file=str(invalid_path), project="proj-a")
    cli_roadmap.handle_roadmap(args, db_path)
    assert "Validation errors:" in capsys.readouterr().out

    ok_path = db_path.parent / "ok.md"
    ok_path.write_text(
        "# Roadmap: proj-a\n"
        "## Phase 1: Foundation\n"
        "**Verify:** `pytest -q`\n"
        "### Task 1.1: Build\n"
        "desc\n"
    )
    args = Namespace(roadmap_command="create", file=str(ok_path), project="proj-a")
    cli_roadmap.handle_roadmap(args, db_path)
    out = capsys.readouterr().out
    assert "Roadmap created" in out

    store = Store(db_path)
    try:
        roadmaps = store.list_tasks(project_id=project_id)
        assert len(roadmaps) == 1
    finally:
        store.close()


def test_handle_roadmap_show_and_approve(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    show_args = Namespace(roadmap_command="show", project="missing")
    cli_roadmap.handle_roadmap(show_args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    store.create_project("proj-a", "/tmp/repo-a")
    store.close()

    show_args = Namespace(roadmap_command="show", project="proj-a")
    cli_roadmap.handle_roadmap(show_args, db_path)
    assert "No active roadmap" in capsys.readouterr().out

    ids = _seed_project_with_task(db_path, task_status="queued")

    store = Store(db_path)
    try:
        task = store.get_task(ids["task_id"])
        assert task is not None
        store.assign_task(
            ids["task_id"],
            agent="codex",
            branch="b",
            worktree="/tmp/w",
            signal_dir="/tmp/s",
        )
        store.fail_task(ids["task_id"], "x")
    finally:
        store.close()

    show_args = Namespace(roadmap_command="show", project="proj-a")
    cli_roadmap.handle_roadmap(show_args, db_path)
    out = capsys.readouterr().out
    assert "Roadmap #" in out
    assert "Phase 1:" in out
    assert "Verify:" in out
    assert "Task 1.1:" in out

    approve_args = Namespace(roadmap_command="approve", project="missing")
    cli_roadmap.handle_roadmap(approve_args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    try:
        project = store.get_project("proj-a")
        assert project is not None
        roadmap = store.get_active_roadmap(project["id"])
        assert roadmap is not None
        store.update_roadmap_status(roadmap["id"], "executing")
    finally:
        store.close()

    approve_args = Namespace(roadmap_command="approve", project="proj-a")
    cli_roadmap.handle_roadmap(approve_args, db_path)
    assert "not 'draft'" in capsys.readouterr().out


def test_handle_roadmap_clear(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clear_args = Namespace(roadmap_command="clear", project="missing")
    cli_roadmap.handle_roadmap(clear_args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    store.create_project("proj-a", "/tmp/repo-a")
    store.close()

    clear_args = Namespace(roadmap_command="clear", project="proj-a")
    cli_roadmap.handle_roadmap(clear_args, db_path)
    assert "No active roadmap." in capsys.readouterr().out

    markdown_path = db_path.parent / "to-clear-1.md"
    markdown_path.write_text(
        "# Roadmap: proj-a\n"
        "## Phase 1: Foundation\n"
        "### Task 1.1: Build\n"
        "desc\n"
    )

    cli_roadmap.handle_roadmap(
        Namespace(roadmap_command="create", file=str(markdown_path), project="proj-a"),
        db_path,
    )

    markdown_path_2 = db_path.parent / "to-clear-2.md"
    markdown_path_2.write_text(
        "# Roadmap: proj-a\n"
        "## Phase 1: Foundation\n"
        "### Task 1.1: Build again\n"
        "desc\n"
    )
    cli_roadmap.handle_roadmap(
        Namespace(roadmap_command="create", file=str(markdown_path_2), project="proj-a"),
        db_path,
    )
    capsys.readouterr()

    cli_roadmap.handle_roadmap(clear_args, db_path)
    out = capsys.readouterr().out
    assert "Cleared 1 roadmap(s)" in out
    assert "tasks removed" in out

    store = Store(db_path)
    try:
        project = store.get_project("proj-a")
        assert project is not None
        assert store.get_active_roadmap(project["id"]) is None
        assert store.list_tasks(project_id=project["id"]) == []
    finally:
        store.close()


def test_handle_roadmap_generate_paths(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(
        roadmap_command="generate",
        project="missing",
        prompt="Build API v1",
        file=None,
        agent="codex",
        approve=False,
    )
    cli_roadmap.handle_roadmap(args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    store.create_project("proj-a", "/tmp/repo-a")
    store.close()

    called: list[tuple[Path, str, str, str]] = []

    class FakeResult:
        def __init__(self, success: bool, stderr: str = "") -> None:
            self.success = success
            self.message = "ok" if success else "boom"
            self.roadmap_id = 12 if success else None
            self.phases = 2 if success else 0
            self.tasks = 3 if success else 0
            self.stdout = ""
            self.stderr = stderr

    def fake_generate(
        db_path_arg: Path,
        project_name: str,
        prompt_text: str,
        agent: str,
    ) -> FakeResult:
        called.append((db_path_arg, project_name, prompt_text, agent))
        return FakeResult(success=True)

    monkeypatch.setattr(cli_roadmap, "generate_roadmap_from_prompt", fake_generate)

    cli_roadmap.handle_roadmap(
        Namespace(
            roadmap_command="generate",
            project="proj-a",
            prompt="Build API v1",
            file=None,
            agent="codex",
            approve=False,
        ),
        db_path,
    )
    out = capsys.readouterr().out
    assert "Roadmap generated (id=12): 2 phases, 3 tasks (agent=codex)" in out
    assert called == [(db_path, "proj-a", "Build API v1", "codex")]

    briefing = db_path.parent / "briefing.txt"
    briefing.write_text("Natural language roadmap request")

    def fake_generate_failure(
        db_path_arg: Path,
        project_name: str,
        prompt_text: str,
        agent: str,
    ) -> FakeResult:
        called.append((db_path_arg, project_name, prompt_text, agent))
        return FakeResult(success=False, stderr="trace line 1\ntrace line 2")

    monkeypatch.setattr(
        cli_roadmap,
        "generate_roadmap_from_prompt",
        fake_generate_failure,
    )

    cli_roadmap.handle_roadmap(
        Namespace(
            roadmap_command="generate",
            project="proj-a",
            prompt=None,
            file=str(briefing),
            agent="gemini",
            approve=False,
        ),
        db_path,
    )
    out = capsys.readouterr().out
    assert "Roadmap generation failed: boom" in out
    assert "Agent stderr (tail):" in out
    assert "trace line 2" in out

    cli_roadmap.handle_roadmap(
        Namespace(
            roadmap_command="generate",
            project="proj-a",
            prompt=None,
            file="missing.txt",
            agent="claude",
            approve=False,
        ),
        db_path,
    )
    out = capsys.readouterr().out
    assert "File 'missing.txt' not found" in out


def test_handle_status_and_alerts(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = Namespace(project="missing", as_json=False)
    cli_status.handle_status(args, db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    _seed_project_with_task(db_path, task_status="queued")

    args = Namespace(project=None, as_json=True)
    cli_status.handle_status(args, db_path)
    assert '"task_number": "1.1"' in capsys.readouterr().out

    store = Store(db_path)
    try:
        project = store.get_project("proj-a")
        assert project is not None
        store.create_alert("warn", "alert message", project_id=project["id"])
    finally:
        store.close()

    args = Namespace(project=None, as_json=False)
    cli_status.handle_status(args, db_path)
    out = capsys.readouterr().out
    assert "ID" in out
    assert "Branch" in out
    assert "Attempts" in out
    assert "Tokens" in out
    assert "n/a" in out
    assert "Total:" in out

    cli_status.handle_alerts(Namespace(ack=None), db_path)
    out = capsys.readouterr().out
    assert "[WARN]" in out

    cli_status.handle_alerts(Namespace(ack=1), db_path)
    out = capsys.readouterr().out
    assert "acknowledged" in out

    cli_status.handle_alerts(Namespace(ack=None), db_path)
    out = capsys.readouterr().out
    assert "No alerts" in out


def test_handle_status_sorts_tasks_by_id(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = Store(db_path)
    try:
        project_id = store.create_project("proj-a", "/tmp/repo-a")
        roadmap_id = store.create_roadmap(project_id, "# Roadmap 1")
        phase_id = store.create_phase(roadmap_id, 1, "P1", None)
        task_1 = store.create_task(roadmap_id, phase_id, "1.1", "First", "desc")
        task_2 = store.create_task(roadmap_id, phase_id, "1.2", "Second", "desc")

        assert task_1 < task_2
    finally:
        store.close()

    cli_status.handle_status(Namespace(project=None, as_json=False), db_path)
    out = capsys.readouterr().out

    first_idx = out.find(f"{task_1:<6}")
    second_idx = out.find(f"{task_2:<6}")
    assert first_idx != -1
    assert second_idx != -1
    assert first_idx < second_idx


def test_handle_status_truncates_long_title(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    long_title = "Create script skeleton and module contract that should be truncated"

    store = Store(db_path)
    try:
        project_id = store.create_project("proj-a", "/tmp/repo-a")
        roadmap_id = store.create_roadmap(project_id, "# Roadmap")
        phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
        task_id = store.create_task(roadmap_id, phase_id, "1.1", long_title, "desc")
        store.queue_task(task_id)
    finally:
        store.close()

    cli_status.handle_status(Namespace(project=None, as_json=False), db_path)
    out = capsys.readouterr().out

    expected = f"{long_title[:32]}..."
    assert expected in out
    assert long_title not in out

    row = next(line for line in out.splitlines() if line.startswith(f"{task_id:<6}"))
    assert "queued" in row
    assert "0/4" in row


def test_handle_status_branch_states_ahead_diverged_and_merged(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ahead_branch = "yeehaw/task-1.1-ahead"
    diverged_branch = "yeehaw/task-1.2-diverged"
    merged_branch = "yeehaw/task-1.3-merged"

    store = Store(db_path)
    try:
        project_id = store.create_project("proj-a", "/tmp/repo-a")
        roadmap_id = store.create_roadmap(project_id, "# Roadmap")
        phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
        task_ahead = store.create_task(roadmap_id, phase_id, "1.1", "Ahead branch", "desc")
        task_diverged = store.create_task(roadmap_id, phase_id, "1.2", "Diverged branch", "desc")
        task_merged = store.create_task(roadmap_id, phase_id, "1.3", "Merged branch", "desc")
        store.assign_task(task_ahead, "codex", ahead_branch, "/tmp/w1", "/tmp/s1")
        store.assign_task(task_diverged, "codex", diverged_branch, "/tmp/w2", "/tmp/s2")
        store.assign_task(task_merged, "codex", merged_branch, "/tmp/w3", "/tmp/s3")
    finally:
        store.close()

    def fake_git_run(
        cmd: list[str],
        cwd: Path | None = None,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert cmd and cmd[0] == "git"
        if cmd[1] == "rev-parse":
            branch_ref = cmd[4]
            if branch_ref in (
                "refs/heads/main",
                f"refs/heads/{ahead_branch}",
                f"refs/heads/{diverged_branch}",
                f"refs/heads/{merged_branch}",
            ):
                return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd[1] == "rev-list":
            rev_range = cmd[4]
            if rev_range == f"refs/heads/main...refs/heads/{ahead_branch}":
                return subprocess.CompletedProcess(cmd, 0, "0\t2\n", "")
            if rev_range == f"refs/heads/main...refs/heads/{diverged_branch}":
                return subprocess.CompletedProcess(cmd, 0, "1\t2\n", "")
            if rev_range == f"refs/heads/main...refs/heads/{merged_branch}":
                return subprocess.CompletedProcess(cmd, 0, "2\t0\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "")
        raise AssertionError(f"Unexpected git command: {cmd}")

    monkeypatch.setattr(cli_status.subprocess, "run", fake_git_run)

    cli_status.handle_status(Namespace(project=None, as_json=False), db_path)
    out = capsys.readouterr().out
    assert "Branch" in out

    ahead_row = next(line for line in out.splitlines() if line.startswith(f"{task_ahead:<6}"))
    diverged_row = next(line for line in out.splitlines() if line.startswith(f"{task_diverged:<6}"))
    merged_row = next(line for line in out.splitlines() if line.startswith(f"{task_merged:<6}"))
    assert "ahead" in ahead_row
    assert ahead_row.rstrip().endswith("n/a")
    assert "diverged" in diverged_row
    assert diverged_row.rstrip().endswith("n/a")
    assert "merged" in merged_row
    assert merged_row.rstrip().endswith("n/a")


def test_handle_status_shows_tokens_for_in_progress_task(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(db_path)
    try:
        project_id = store.create_project("proj-a", "/tmp/repo-a")
        roadmap_id = store.create_roadmap(project_id, "# Roadmap")
        phase_id = store.create_phase(roadmap_id, 1, "Phase 1", None)
        task_id = store.create_task(roadmap_id, phase_id, "1.1", "Running task", "desc")
        store.assign_task(task_id, "codex", "yeehaw/task-1.1-running-task", "/tmp/w", "/tmp/s")
    finally:
        store.close()

    logs_dir = db_path.parent / "logs" / f"task-{task_id}"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "attempt-01-codex.log"
    log_path.write_text(
        "step output\n"
        "\x1b[3m\x1b[35mtokens used\x1b[0m\n"
        "27,315\n"
        "more output\n",
    )

    cli_status.handle_status(Namespace(project=None, as_json=False), db_path)
    out = capsys.readouterr().out

    row = next(line for line in out.splitlines() if line.startswith(f"{task_id:<6}"))
    assert "Tokens" in out
    assert "27,315" in row
    assert "1/4" in row


def test_parse_tokens_used_supports_total_tokens_line() -> None:
    text = (
        "Assistant run complete\n"
        "Input tokens: 1200\n"
        "Output tokens: 300\n"
        "Total tokens: 1,500\n"
    )
    assert cli_status._parse_tokens_used(text) == 1500


def test_parse_tokens_used_supports_gemini_usage_metadata_json() -> None:
    text = (
        '{"usageMetadata":{"promptTokenCount":1200,'
        '"candidatesTokenCount":300,"totalTokenCount":1500}}'
    )
    assert cli_status._parse_tokens_used(text) == 1500


def test_parse_tokens_used_sums_input_and_output_when_total_missing() -> None:
    text = "input tokens: 1,200\ncompletion tokens: 300\n"
    assert cli_status._parse_tokens_used(text) == 1500


def test_handle_scheduler_show_config_and_no_changes(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_scheduler.handle_scheduler(Namespace(scheduler_command="show"), db_path)
    out = capsys.readouterr().out
    assert "Scheduler Configuration" in out

    cli_scheduler.handle_scheduler(
        Namespace(
            scheduler_command="config",
            max_global=None,
            max_project=None,
            tick=None,
            timeout=None,
        ),
        db_path,
    )
    assert "No changes specified" in capsys.readouterr().out

    cli_scheduler.handle_scheduler(
        Namespace(
            scheduler_command="config",
            max_global=9,
            max_project=4,
            tick=3,
            timeout=22,
        ),
        db_path,
    )
    out = capsys.readouterr().out
    assert "Scheduler config updated" in out
    assert "max_global_tasks = 9" in out


def test_handle_workers_show_default_custom_and_invalid(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_workers.handle_workers(Namespace(workers_command="show"), db_path)
    out = capsys.readouterr().out
    assert "Worker Configuration:" in out
    assert "workers.json (not found)" in out
    assert "[claude]" in out
    assert "[gemini]" in out
    assert "[codex]" in out

    workers_path = db_path.parent / "workers.json"
    workers_path.parent.mkdir(parents=True, exist_ok=True)
    workers_path.write_text(
        (
            "{"
            '"disable_default_mcp": true,'
            '"extra_args": ["--global"],'
            '"agents": {"codex": {"disable_default_mcp": false, "extra_args": ["--agent"]}}'
            "}"
        )
    )

    cli_workers.handle_workers(Namespace(workers_command="show"), db_path)
    out = capsys.readouterr().out
    assert "workers.json (found)" in out
    assert "[codex]" in out
    assert "disable_default_mcp: false" in out
    assert "default_no_mcp_args: (none)" in out
    assert "extra_args: --global --agent" in out

    workers_path.write_text("{not-json")
    cli_workers.handle_workers(Namespace(workers_command="show"), db_path)
    out = capsys.readouterr().out
    assert "Error: Invalid JSON in" in out


def test_handle_attach_paths(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_attach.handle_attach(Namespace(task_id=99), db_path)
    assert "Task 99 not found" in capsys.readouterr().out

    ids = _seed_project_with_task(db_path)

    monkeypatch.setattr(cli_attach, "has_session", lambda _name: False)
    cli_attach.handle_attach(Namespace(task_id=ids["task_id"]), db_path)
    assert "No active tmux session" in capsys.readouterr().out

    attached: list[str] = []
    monkeypatch.setattr(cli_attach, "has_session", lambda _name: True)
    monkeypatch.setattr(cli_attach, "attach_session", lambda name: attached.append(name))
    cli_attach.handle_attach(Namespace(task_id=ids["task_id"]), db_path)
    out = capsys.readouterr().out
    assert "Attaching to" in out
    assert attached == [f"yeehaw-task-{ids['task_id']}"]


def test_handle_plan_missing_and_success(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(briefing="missing.txt", agent="claude", project=None)
    cli_plan.handle_plan(args, db_path)
    assert "Briefing file 'missing.txt' not found" in capsys.readouterr().out

    cli_plan.handle_plan(Namespace(briefing=None, agent="codex", project="missing"), db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    store = Store(db_path)
    store.create_project("proj-a", "/tmp/repo-a")
    store.close()

    briefing = db_path.parent.parent / "briefing.txt"
    briefing.write_text("content")

    called: list[tuple[Path, Path | None, str, str | None]] = []

    def fake_start(
        db: Path,
        briefing_file: Path | None,
        agent: str,
        project_name: str | None,
    ) -> None:
        called.append((db, briefing_file, agent, project_name))

    monkeypatch.setattr(cli_plan, "start_planner_session", fake_start)
    cli_plan.handle_plan(
        Namespace(briefing=str(briefing), agent="gemini", project="proj-a"),
        db_path,
    )

    out = capsys.readouterr().out
    assert "Starting interactive planner session" in out
    assert called == [(db_path, briefing, "gemini", "proj-a")]

    monkeypatch.setattr(
        cli_plan,
        "start_planner_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("planner failed")),
    )
    cli_plan.handle_plan(Namespace(briefing=None, agent="codex", project=None), db_path)
    assert "Error: planner failed" in capsys.readouterr().out


def test_handle_run_paths(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOrchestrator:
        def __init__(
            self,
            store: Store,
            repo_root: Path,
            default_agent: str | None = None,
        ) -> None:
            self.store = store
            self.repo_root = repo_root
            self.default_agent = default_agent
            self.called_with: list[int | None] = []

        def run(self, project_id: int | None = None) -> None:
            self.called_with.append(project_id)

    monkeypatch.setattr(cli_run, "Orchestrator", FakeOrchestrator)

    cli_run.handle_run(Namespace(project="missing", agent=None), db_path)
    assert "Project 'missing' not found" in capsys.readouterr().out

    ids = _seed_project_with_task(db_path)
    cli_run.handle_run(Namespace(project="proj-a", agent="codex"), db_path)
    out = capsys.readouterr().out
    assert "Starting orchestrator" in out

    class InterruptingOrchestrator(FakeOrchestrator):
        def run(self, project_id: int | None = None) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_run, "Orchestrator", InterruptingOrchestrator)
    cli_run.handle_run(Namespace(project=None, agent=None), db_path)
    out = capsys.readouterr().out
    assert "Stopping" in out


def test_handle_logs_paths(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli_logs.handle_logs(Namespace(task_id=999, attempt=None, tail=10), db_path)
    assert "Task 999 not found" in capsys.readouterr().out

    ids = _seed_project_with_task(db_path)
    cli_logs.handle_logs(Namespace(task_id=ids["task_id"], attempt=None, tail=10), db_path)
    assert "No logs found for task" in capsys.readouterr().out

    logs_dir = db_path.parent.parent / ".yeehaw" / "logs" / f"task-{ids['task_id']}"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "attempt-01-claude.log"
    log_path.write_text("line1\\nline2\\nline3\\n")

    cli_logs.handle_logs(Namespace(task_id=ids["task_id"], attempt=None, tail=2), db_path)
    out = capsys.readouterr().out
    assert f"Log file: {log_path}" in out
    assert "line2" in out
    assert "line3" in out


def test_handle_logs_follow_mode(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_project_with_task(db_path)
    logs_dir = db_path.parent.parent / ".yeehaw" / "logs" / f"task-{ids['task_id']}"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "attempt-01-claude.log"
    log_path.write_text("line1\n")

    calls = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            log_path.write_text("line1\nline2\n")
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_logs.time, "sleep", fake_sleep)

    cli_logs.handle_logs(
        Namespace(task_id=ids["task_id"], attempt=None, tail=1, follow=True),
        db_path,
    )
    out = capsys.readouterr().out
    assert "Following live output" in out
    assert "line1" in out
    assert "line2" in out
    assert "Stopped following." in out


def test_handle_stop_paths(
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_stop.handle_stop(Namespace(all=False, task_id=None), db_path)
    assert "Specify a task ID or --all" in capsys.readouterr().out

    cli_stop.handle_stop(Namespace(all=False, task_id=88), db_path)
    assert "No matching tasks found" in capsys.readouterr().out

    ids = _seed_project_with_task(db_path, task_status="in-progress")

    killed: list[str] = []
    cleaned: list[tuple[Path, Path]] = []

    monkeypatch.setattr(cli_stop, "has_session", lambda _session: True)
    monkeypatch.setattr(cli_stop, "kill_session", lambda session: killed.append(session))
    monkeypatch.setattr(
        cli_stop,
        "cleanup_worktree",
        lambda repo_root, worktree: cleaned.append((repo_root, worktree)),
    )

    cli_stop.handle_stop(Namespace(all=False, task_id=ids["task_id"]), db_path)
    out = capsys.readouterr().out
    assert "Stopped task" in out
    assert killed == [f"yeehaw-task-{ids['task_id']}"]
    assert cleaned == [(Path("/tmp/repo-a"), Path("/tmp/worktree"))]

    # --all path
    ids2 = _seed_project_with_task(db_path, task_status="in-progress")
    killed.clear()
    cleaned.clear()
    cli_stop.handle_stop(Namespace(all=True, task_id=None), db_path)
    out = capsys.readouterr().out
    assert f"Stopped task {ids2['task_id']}" in out
