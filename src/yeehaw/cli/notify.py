"""Notification sink configuration and synthetic dispatch commands."""

from __future__ import annotations

import argparse

import json
from pathlib import Path
from typing import Any

from yeehaw.notify import (
    NotificationDispatcher,
    SinkDeliveryResult,
    WebhookSinkConfig,
    load_notification_config,
    parse_notification_config,
)

NOTIFICATIONS_CONFIG_DIR = "config"
NOTIFICATIONS_SINK_CONFIG = "notifications.json"


def handle_notify(args: argparse.Namespace, db_path: Path) -> None:
    """Handle `yeehaw notify` subcommands."""
    config_path = _notification_config_path(db_path)

    if args.notify_command == "show":
        _show_notify_config(config_path)
    elif args.notify_command == "set":
        _set_notify_sink(config_path, args)
    elif args.notify_command == "test":
        _test_notify_dispatch(config_path, args)


def _notification_config_path(db_path: Path) -> Path:
    """Return notification sink config path under runtime config dir."""
    return db_path.parent / NOTIFICATIONS_CONFIG_DIR / NOTIFICATIONS_SINK_CONFIG


def _show_notify_config(config_path: Path) -> None:
    """Show parsed notification sink configuration."""
    try:
        config = load_notification_config(config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    status = "found" if config_path.exists() else "not found"
    print("Notification Configuration:")
    print(f"  Config file: {config_path} ({status})")

    if not config.sinks:
        print("Sinks: (none)")
        return

    print("Sinks:")
    for sink in config.sinks:
        events = ", ".join(sink.events) if sink.events else "(all)"
        print(
            f"  - {sink.name} (type={sink.sink_type}, enabled={str(sink.enabled).lower()})"
        )
        print(f"    url: {sink.url}")
        print(f"    method: {sink.method}")
        print(f"    events: {events}")
        print(f"    headers: {json.dumps(sink.headers, sort_keys=True)}")
        print(f"    timeout_sec: {sink.timeout_sec}")
        print(f"    max_attempts: {sink.max_attempts}")
        print(f"    backoff_initial_sec: {sink.backoff_initial_sec}")
        print(f"    backoff_multiplier: {sink.backoff_multiplier}")
        print(f"    backoff_max_sec: {sink.backoff_max_sec}")


def _set_notify_sink(config_path: Path, args: Any) -> None:
    """Create or update one webhook sink in runtime config."""
    try:
        config = load_notification_config(config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    try:
        sink_payload = _build_sink_payload(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    sinks = [_sink_to_payload(sink) for sink in config.sinks]
    replaced = False
    for index, existing in enumerate(sinks):
        if existing.get("name") == sink_payload["name"]:
            sinks[index] = sink_payload
            replaced = True
            break
    if not replaced:
        sinks.append(sink_payload)

    payload = {"sinks": sinks}
    try:
        parse_notification_config(payload)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    action = "Updated" if replaced else "Added"
    print(f"{action} notification sink '{sink_payload['name']}'.")
    print(f"Config file: {config_path}")


def _test_notify_dispatch(config_path: Path, args: Any) -> None:
    """Dispatch one synthetic notification event or print dry-run output."""
    try:
        config = load_notification_config(config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    try:
        extra_payload = _parse_json_object(args.payload)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    payload = _build_test_payload(args, extra_payload)
    matching_sinks = config.matching_sinks(args.event)

    print(f"Notification test event: {args.event}")
    print(f"Synthetic payload: {json.dumps(payload, sort_keys=True)}")
    print(f"Matching sinks: {len(matching_sinks)}")

    if args.dry_run:
        print("Dry run enabled; dispatch skipped.")
        return

    if not matching_sinks:
        print(f"No enabled sinks are configured for event '{args.event}'.")
        return

    try:
        with NotificationDispatcher(config) as dispatcher:
            results = dispatcher.dispatch_sync(
                args.event,
                payload,
                timeout_sec=args.timeout_sec,
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Error: failed to dispatch synthetic notification: {exc}")
        return

    _print_results(results)


def _build_sink_payload(args: Any) -> dict[str, Any]:
    """Build sink payload from CLI args."""
    method = str(args.method).strip().upper()
    if not method:
        raise ValueError("--method must be a non-empty string")

    events = _normalize_events(args.events or [])
    headers = _parse_headers(args.header or [])

    sink_payload: dict[str, Any] = {
        "name": str(args.name).strip(),
        "type": "webhook",
        "url": str(args.url).strip(),
        "enabled": bool(args.enabled) or not bool(args.disabled),
        "method": method,
        "headers": headers,
    }
    if not sink_payload["name"]:
        raise ValueError("--name must be a non-empty string")
    if not sink_payload["url"]:
        raise ValueError("--url must be a non-empty string")
    if events:
        sink_payload["events"] = events
    if args.timeout_sec is not None:
        sink_payload["timeout_sec"] = args.timeout_sec
    if args.max_attempts is not None:
        sink_payload["max_attempts"] = args.max_attempts
    if args.backoff_initial_sec is not None:
        sink_payload["backoff_initial_sec"] = args.backoff_initial_sec
    if args.backoff_multiplier is not None:
        sink_payload["backoff_multiplier"] = args.backoff_multiplier
    if args.backoff_max_sec is not None:
        sink_payload["backoff_max_sec"] = args.backoff_max_sec
    return sink_payload


def _sink_to_payload(sink: WebhookSinkConfig) -> dict[str, Any]:
    """Serialize sink dataclass back to JSON payload."""
    payload: dict[str, Any] = {
        "name": sink.name,
        "type": sink.sink_type,
        "url": sink.url,
        "enabled": sink.enabled,
        "method": sink.method,
        "headers": sink.headers,
        "timeout_sec": sink.timeout_sec,
        "max_attempts": sink.max_attempts,
        "backoff_initial_sec": sink.backoff_initial_sec,
        "backoff_multiplier": sink.backoff_multiplier,
        "backoff_max_sec": sink.backoff_max_sec,
    }
    if sink.events:
        payload["events"] = list(sink.events)
    return payload


def _normalize_events(raw_events: list[str]) -> list[str]:
    """Normalize repeated event args while preserving order."""
    normalized: list[str] = []
    for raw in raw_events:
        event = str(raw).strip()
        if not event:
            raise ValueError("--event values must be non-empty strings")
        if event not in normalized:
            normalized.append(event)
    return normalized


def _parse_headers(raw_headers: list[str]) -> dict[str, str]:
    """Parse KEY=VALUE header args."""
    parsed: dict[str, str] = {}
    for raw_header in raw_headers:
        key, separator, value = raw_header.partition("=")
        if not separator or not key.strip():
            raise ValueError(
                f"invalid header {raw_header!r}; expected KEY=VALUE"
            )
        parsed[key.strip()] = value
    return parsed


def _parse_json_object(raw_payload: str | None) -> dict[str, Any]:
    """Parse optional JSON object from CLI argument."""
    if raw_payload is None:
        return {}

    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--payload must be valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("--payload must decode to a JSON object")
    return parsed


def _build_test_payload(args: Any, extra_payload: dict[str, Any]) -> dict[str, Any]:
    """Build synthetic event payload for `notify test` command."""
    payload: dict[str, Any] = {
        "project_id": args.project_id,
        "project_name": args.project_name,
        "roadmap_id": args.roadmap_id,
        "phase_id": args.phase_id,
        "task_id": args.task_id,
        "task_number": args.task_number,
        "task_status": args.task_status,
        "reason": args.reason,
    }
    payload.update(extra_payload)
    return payload


def _print_results(results: tuple[SinkDeliveryResult, ...]) -> None:
    """Print sink delivery outcomes for one test event."""
    succeeded = 0
    for result in results:
        status_label = "ok" if result.ok else "failed"
        if result.ok:
            succeeded += 1

        status_code = "-" if result.status_code is None else str(result.status_code)
        error = result.error if result.error else "-"
        print(
            "  "
            f"{result.sink_name} [{result.sink_type}] {status_label} "
            f"(attempts={result.attempts}, status={status_code}, error={error})"
        )

    failed = len(results) - succeeded
    print(f"Delivery summary: {succeeded} succeeded, {failed} failed.")


__all__ = ["handle_notify"]
