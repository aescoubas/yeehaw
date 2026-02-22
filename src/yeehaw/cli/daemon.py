"""Systemd user-service management for persistent Yeehaw orchestrator."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def handle_daemon(args: Any, db_path: Path) -> None:
    """Handle `yeehaw daemon` subcommands."""
    try:
        command = str(args.daemon_command)
        service_name = _normalize_service_name(
            str(getattr(args, "service_name", "yeehaw-orchestrator"))
        )
        runtime_root = db_path.parent

        if command == "install":
            _handle_install(
                service_name=service_name,
                runtime_root=runtime_root,
                agent=getattr(args, "agent", None),
                force=bool(getattr(args, "force", False)),
                enable=not bool(getattr(args, "no_enable", False)),
                start=not bool(getattr(args, "no_start", False)),
            )
            return

        if command == "uninstall":
            _handle_uninstall(service_name)
            return

        if command == "start":
            _require_systemd_tools(journal=False)
            _run_systemctl_user(["start", service_name], check=True)
            print(f"Started {service_name}.")
            return

        if command == "stop":
            _require_systemd_tools(journal=False)
            _run_systemctl_user(["stop", service_name], check=True)
            print(f"Stopped {service_name}.")
            return

        if command == "restart":
            _require_systemd_tools(journal=False)
            _run_systemctl_user(["restart", service_name], check=True)
            print(f"Restarted {service_name}.")
            return

        if command == "status":
            _require_systemd_tools(journal=False)
            result = _run_systemctl_user(
                ["status", "--no-pager", "--full", service_name],
                check=False,
            )
            output = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if output:
                print(output)
            elif err:
                print(err)
            else:
                state = "active" if result.returncode == 0 else "inactive"
                print(f"{service_name}: {state}")
            return

        if command == "logs":
            _require_systemd_tools(journal=True)
            lines = max(1, int(getattr(args, "lines", 200)))
            follow = bool(getattr(args, "follow", False))
            journal_cmd = ["journalctl", "--user", "-u", service_name, "-n", str(lines)]
            if follow:
                journal_cmd.append("-f")
            else:
                journal_cmd.append("--no-pager")
            result = subprocess.run(
                journal_cmd,
                capture_output=not follow,
                text=not follow,
                check=False,
            )
            if follow:
                if result.returncode != 0:
                    raise RuntimeError(f"journalctl failed with exit code {result.returncode}")
                return
            output = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if output:
                print(output)
            elif err:
                print(err)
            else:
                print(f"No logs for {service_name}.")
            return

        raise RuntimeError(f"Unknown daemon command: {command}")
    except RuntimeError as exc:
        print(f"Error: {exc}")


def _handle_install(
    *,
    service_name: str,
    runtime_root: Path,
    agent: str | None,
    force: bool,
    enable: bool,
    start: bool,
) -> None:
    _require_systemd_tools(journal=False)

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / service_name

    if unit_path.exists() and not force:
        print(
            f"Service file already exists at {unit_path}. "
            "Use --force to overwrite."
        )
        return

    unit_text = _build_unit_text(
        service_name=service_name,
        runtime_root=runtime_root,
        agent=agent,
    )
    unit_path.write_text(unit_text)

    _run_systemctl_user(["daemon-reload"], check=True)
    if enable:
        _run_systemctl_user(["enable", service_name], check=True)
    if start:
        _run_systemctl_user(["start", service_name], check=True)

    print(f"Installed {service_name} at {unit_path}")
    print(f"Runtime root: {runtime_root}")
    if enable:
        print("Service enabled for user startup.")
    if start:
        print("Service started.")
    else:
        print("Service not started (--no-start).")


def _handle_uninstall(service_name: str) -> None:
    _require_systemd_tools(journal=False)

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_path = unit_dir / service_name

    _run_systemctl_user(["stop", service_name], check=False)
    _run_systemctl_user(["disable", service_name], check=False)

    if unit_path.exists():
        unit_path.unlink()
        removed = True
    else:
        removed = False

    _run_systemctl_user(["daemon-reload"], check=True)

    if removed:
        print(f"Removed {service_name} ({unit_path}).")
    else:
        print(f"Service file not found for {service_name}.")


def _build_unit_text(service_name: str, runtime_root: Path, agent: str | None) -> str:
    cmd = [sys.executable, "-m", "yeehaw", "run"]
    if agent:
        cmd.extend(["--agent", agent])
    exec_start = _format_systemd_exec(cmd)
    env_runtime = _escape_systemd(str(runtime_root))
    env_path = _escape_systemd(_build_service_path(agent))
    working_dir = _escape_systemd(str(Path.home()))

    description_name = service_name.removesuffix(".service")
    return (
        "[Unit]\n"
        f"Description=Yeehaw orchestrator ({description_name})\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"Environment=YEEHAW_HOME={env_runtime}\n"
        f"Environment=PATH={env_path}\n"
        f"WorkingDirectory={working_dir}\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _normalize_service_name(raw_name: str) -> str:
    name = raw_name.strip()
    if not name:
        raise RuntimeError("Service name cannot be empty")
    if not name.endswith(".service"):
        name += ".service"
    return name


def _build_service_path(agent: str | None) -> str:
    entries = _path_entries(os.environ.get("PATH", ""))
    for bin_dir in _discover_agent_bin_dirs(agent):
        if bin_dir not in entries:
            entries.insert(0, bin_dir)
    if not entries:
        entries = _path_entries(_default_service_path())
    return ":".join(entries)


def _path_entries(raw_path: str) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for raw_part in raw_path.split(":"):
        part = raw_part.strip()
        if not part:
            continue
        expanded = os.path.expandvars(part)
        expanded = str(Path(expanded).expanduser())
        candidate = Path(expanded)
        if not candidate.is_absolute():
            continue
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        entries.append(normalized)
    return entries


def _discover_agent_bin_dirs(agent: str | None) -> list[str]:
    candidates: list[str]
    if agent:
        candidates = [agent]
    else:
        candidates = ["claude", "gemini", "codex"]

    dirs: list[str] = []
    for name in candidates:
        resolved = shutil.which(name)
        if not resolved:
            continue
        bin_dir = str(Path(resolved).resolve().parent)
        if bin_dir not in dirs:
            dirs.append(bin_dir)
    return dirs


def _default_service_path() -> str:
    return "/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _require_systemd_tools(*, journal: bool) -> None:
    if shutil.which("systemctl") is None:
        raise RuntimeError("systemctl not found on PATH")
    if journal and shutil.which("journalctl") is None:
        raise RuntimeError("journalctl not found on PATH")


def _run_systemctl_user(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    cmd = ["systemctl", "--user", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or "unknown error"
        raise RuntimeError(f"systemctl {' '.join(args)} failed: {detail}")
    return result


def _format_systemd_exec(args: list[str]) -> str:
    return " ".join(_quote_systemd_arg(arg) for arg in args)


def _quote_systemd_arg(arg: str) -> str:
    escaped = _escape_systemd(arg)
    if any(char.isspace() for char in escaped):
        return f'"{escaped}"'
    return escaped


def _escape_systemd(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
