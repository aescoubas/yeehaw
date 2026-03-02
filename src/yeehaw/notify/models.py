"""Notification sink models and configuration parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SUPPORTED_NOTIFICATION_SINK_TYPES: tuple[str, ...] = ("webhook",)

DEFAULT_WEBHOOK_TIMEOUT_SEC = 3.0
MAX_WEBHOOK_TIMEOUT_SEC = 30.0

DEFAULT_WEBHOOK_MAX_ATTEMPTS = 3
MAX_WEBHOOK_MAX_ATTEMPTS = 5

DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC = 0.25
DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER = 2.0
DEFAULT_WEBHOOK_BACKOFF_MAX_SEC = 2.0
MAX_WEBHOOK_BACKOFF_SEC = 10.0

_ROOT_KEYS = frozenset({"sinks"})
_WEBHOOK_KEYS = frozenset(
    {
        "name",
        "type",
        "url",
        "enabled",
        "events",
        "method",
        "headers",
        "timeout_sec",
        "max_attempts",
        "backoff_initial_sec",
        "backoff_multiplier",
        "backoff_max_sec",
    }
)


@dataclass(frozen=True)
class NotificationEvent:
    """Lifecycle event payload dispatched to notification sinks."""

    event_name: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    emitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class WebhookSinkConfig:
    """Webhook sink settings."""

    name: str
    url: str
    enabled: bool = True
    events: tuple[str, ...] = ()
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: float = DEFAULT_WEBHOOK_TIMEOUT_SEC
    max_attempts: int = DEFAULT_WEBHOOK_MAX_ATTEMPTS
    backoff_initial_sec: float = DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC
    backoff_multiplier: float = DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER
    backoff_max_sec: float = DEFAULT_WEBHOOK_BACKOFF_MAX_SEC
    sink_type: str = field(default="webhook", init=False)

    def matches_event(self, event_name: str) -> bool:
        """Return True when this sink should receive the event."""
        return self.enabled and (not self.events or event_name in self.events)

    def bounded_attempts(self) -> int:
        """Return attempts clamped to hard safety limits."""
        return max(1, min(self.max_attempts, MAX_WEBHOOK_MAX_ATTEMPTS))

    def bounded_timeout_sec(self) -> float:
        """Return timeout clamped to hard safety limits."""
        return min(max(self.timeout_sec, 0.1), MAX_WEBHOOK_TIMEOUT_SEC)

    def bounded_backoff_initial_sec(self) -> float:
        """Return initial backoff clamped to hard safety limits."""
        return min(max(self.backoff_initial_sec, 0.0), MAX_WEBHOOK_BACKOFF_SEC)

    def bounded_backoff_multiplier(self) -> float:
        """Return backoff multiplier clamped to safe lower bound."""
        return max(self.backoff_multiplier, 1.0)

    def bounded_backoff_max_sec(self) -> float:
        """Return max backoff clamped to hard safety limits."""
        return min(max(self.backoff_max_sec, 0.0), MAX_WEBHOOK_BACKOFF_SEC)


@dataclass(frozen=True)
class NotificationConfig:
    """Configured notification sinks."""

    sinks: tuple[WebhookSinkConfig, ...] = ()

    def matching_sinks(self, event_name: str) -> tuple[WebhookSinkConfig, ...]:
        """Return enabled sinks subscribed to the event name."""
        return tuple(sink for sink in self.sinks if sink.matches_event(event_name))


@dataclass(frozen=True)
class SinkDeliveryResult:
    """Result for one sink delivery attempt sequence."""

    sink_name: str
    sink_type: str
    event_name: str
    ok: bool
    attempts: int
    status_code: int | None = None
    error: str | None = None

    @classmethod
    def success(
        cls,
        *,
        sink_name: str,
        sink_type: str,
        event_name: str,
        attempts: int,
        status_code: int | None = None,
    ) -> SinkDeliveryResult:
        """Build a success result."""
        return cls(
            sink_name=sink_name,
            sink_type=sink_type,
            event_name=event_name,
            ok=True,
            attempts=attempts,
            status_code=status_code,
            error=None,
        )

    @classmethod
    def failure(
        cls,
        *,
        sink_name: str,
        sink_type: str,
        event_name: str,
        attempts: int,
        status_code: int | None = None,
        error: str | None = None,
    ) -> SinkDeliveryResult:
        """Build a failure result."""
        return cls(
            sink_name=sink_name,
            sink_type=sink_type,
            event_name=event_name,
            ok=False,
            attempts=attempts,
            status_code=status_code,
            error=error,
        )


def load_notification_config(config_path: Path) -> NotificationConfig:
    """Load notification configuration from a JSON file."""
    if not config_path.exists():
        return NotificationConfig()

    try:
        raw_text = config_path.read_text()
    except OSError as exc:
        raise ValueError(f"Unable to read notification config {config_path}: {exc}") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in notification config {config_path}: {exc}") from exc

    try:
        return parse_notification_config(payload)
    except ValueError as exc:
        raise ValueError(f"Invalid notification config in {config_path}: {exc}") from exc


def parse_notification_config(payload: object) -> NotificationConfig:
    """Parse and validate notification sink configuration."""
    if not isinstance(payload, dict):
        raise ValueError("root must be an object")

    unknown_root_keys = sorted(set(payload) - _ROOT_KEYS)
    if unknown_root_keys:
        keys = ", ".join(unknown_root_keys)
        raise ValueError(f"unsupported top-level keys: {keys}")

    raw_sinks = payload.get("sinks", [])
    if raw_sinks is None:
        raw_sinks = []
    if not isinstance(raw_sinks, list):
        raise ValueError("'sinks' must be an array")

    sinks: list[WebhookSinkConfig] = []
    for index, raw_sink in enumerate(raw_sinks):
        location = f"sinks[{index}]"
        if not isinstance(raw_sink, dict):
            raise ValueError(f"{location} must be an object")

        sink_type = _require_non_empty_string(raw_sink, "type", location)
        if sink_type != "webhook":
            raise ValueError(f"{location} has unsupported sink type {sink_type!r}")

        sinks.append(_parse_webhook_sink(raw_sink, location))

    return NotificationConfig(sinks=tuple(sinks))


def _parse_webhook_sink(payload: dict[str, Any], location: str) -> WebhookSinkConfig:
    unknown_keys = sorted(set(payload) - _WEBHOOK_KEYS)
    if unknown_keys:
        keys = ", ".join(unknown_keys)
        raise ValueError(f"{location} has unsupported keys: {keys}")

    name = _require_non_empty_string(payload, "name", location)
    url = _require_non_empty_string(payload, "url", location)

    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{location}.enabled must be a boolean")

    method = _require_non_empty_string(payload, "method", location, default="POST").upper()
    events = _read_event_names(payload, location)
    headers = _read_headers(payload, location)
    timeout_sec = _read_float(
        payload,
        "timeout_sec",
        location,
        default=DEFAULT_WEBHOOK_TIMEOUT_SEC,
        min_value=0.1,
        max_value=MAX_WEBHOOK_TIMEOUT_SEC,
    )
    max_attempts = _read_int(
        payload,
        "max_attempts",
        location,
        default=DEFAULT_WEBHOOK_MAX_ATTEMPTS,
        min_value=1,
        max_value=MAX_WEBHOOK_MAX_ATTEMPTS,
    )
    backoff_initial_sec = _read_float(
        payload,
        "backoff_initial_sec",
        location,
        default=DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC,
        min_value=0.0,
        max_value=MAX_WEBHOOK_BACKOFF_SEC,
    )
    backoff_multiplier = _read_float(
        payload,
        "backoff_multiplier",
        location,
        default=DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER,
        min_value=1.0,
    )
    backoff_max_sec = _read_float(
        payload,
        "backoff_max_sec",
        location,
        default=DEFAULT_WEBHOOK_BACKOFF_MAX_SEC,
        min_value=0.0,
        max_value=MAX_WEBHOOK_BACKOFF_SEC,
    )

    if backoff_initial_sec > backoff_max_sec:
        raise ValueError(
            f"{location}.backoff_initial_sec must be <= {location}.backoff_max_sec"
        )

    return WebhookSinkConfig(
        name=name,
        url=url,
        enabled=enabled,
        events=events,
        method=method,
        headers=headers,
        timeout_sec=timeout_sec,
        max_attempts=max_attempts,
        backoff_initial_sec=backoff_initial_sec,
        backoff_multiplier=backoff_multiplier,
        backoff_max_sec=backoff_max_sec,
    )


def _require_non_empty_string(
    payload: dict[str, Any],
    key: str,
    location: str,
    *,
    default: str | None = None,
) -> str:
    raw = payload.get(key, default)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return raw.strip()


def _read_event_names(payload: dict[str, Any], location: str) -> tuple[str, ...]:
    raw_events = payload.get("events", [])
    if raw_events is None:
        raw_events = []

    items: list[str]
    if isinstance(raw_events, str):
        items = [raw_events]
    elif isinstance(raw_events, list | tuple):
        items = list(raw_events)
    else:
        raise ValueError(f"{location}.events must be a string or array of strings")

    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{location}.events must contain only non-empty strings")

    return tuple(dict.fromkeys(item.strip() for item in items if item.strip()))


def _read_headers(payload: dict[str, Any], location: str) -> dict[str, str]:
    raw_headers = payload.get("headers", {})
    if raw_headers is None:
        return {}
    if not isinstance(raw_headers, dict):
        raise ValueError(f"{location}.headers must be an object")

    headers: dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{location}.headers must map string keys to string values")
        headers[key] = value
    return headers


def _read_int(
    payload: dict[str, Any],
    key: str,
    location: str,
    *,
    default: int,
    min_value: int,
    max_value: int | None = None,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{location}.{key} must be an integer")
    if value < min_value:
        raise ValueError(f"{location}.{key} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{location}.{key} must be <= {max_value}")
    return value


def _read_float(
    payload: dict[str, Any],
    key: str,
    location: str,
    *,
    default: float,
    min_value: float,
    max_value: float | None = None,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{location}.{key} must be a number")

    parsed = float(value)
    if parsed < min_value:
        raise ValueError(f"{location}.{key} must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"{location}.{key} must be <= {max_value}")
    return parsed


__all__ = [
    "DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC",
    "DEFAULT_WEBHOOK_BACKOFF_MAX_SEC",
    "DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER",
    "DEFAULT_WEBHOOK_MAX_ATTEMPTS",
    "DEFAULT_WEBHOOK_TIMEOUT_SEC",
    "MAX_WEBHOOK_BACKOFF_SEC",
    "MAX_WEBHOOK_MAX_ATTEMPTS",
    "MAX_WEBHOOK_TIMEOUT_SEC",
    "NotificationConfig",
    "NotificationEvent",
    "SUPPORTED_NOTIFICATION_SINK_TYPES",
    "SinkDeliveryResult",
    "WebhookSinkConfig",
    "load_notification_config",
    "parse_notification_config",
]
