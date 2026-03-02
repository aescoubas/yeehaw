"""Webhook sink delivery implementation."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from yeehaw.notify.models import NotificationEvent, SinkDeliveryResult, WebhookSinkConfig

_RETRYABLE_HTTP_CODES = frozenset({408, 425, 429})


@dataclass(frozen=True)
class WebhookRequest:
    """Prepared webhook HTTP request."""

    url: str
    method: str
    headers: dict[str, str]
    timeout_sec: float
    body: bytes


WebhookTransport = Callable[[WebhookRequest], int]
SleepFunc = Callable[[float], None]


def send_webhook(
    sink: WebhookSinkConfig,
    event: NotificationEvent,
    *,
    transport: WebhookTransport | None = None,
    sleep_func: SleepFunc = time.sleep,
) -> SinkDeliveryResult:
    """Send one event to a webhook sink with bounded retries/backoff."""
    request = build_webhook_request(sink, event)
    deliver = transport or _transport_via_urllib

    attempt_limit = sink.bounded_attempts()
    backoff_max = sink.bounded_backoff_max_sec()
    backoff_delay = min(sink.bounded_backoff_initial_sec(), backoff_max)
    backoff_multiplier = sink.bounded_backoff_multiplier()

    for attempt in range(1, attempt_limit + 1):
        try:
            status_code = deliver(request)
        except Exception as exc:  # pragma: no cover - exercised via tests with custom transport
            if attempt < attempt_limit and _is_retryable_exception(exc):
                _sleep(backoff_delay, sleep_func)
                backoff_delay = _next_backoff(backoff_delay, backoff_multiplier, backoff_max)
                continue
            return SinkDeliveryResult.failure(
                sink_name=sink.name,
                sink_type=sink.sink_type,
                event_name=event.event_name,
                attempts=attempt,
                error=str(exc),
            )

        if 200 <= status_code < 300:
            return SinkDeliveryResult.success(
                sink_name=sink.name,
                sink_type=sink.sink_type,
                event_name=event.event_name,
                attempts=attempt,
                status_code=status_code,
            )

        should_retry = attempt < attempt_limit and _is_retryable_http_code(status_code)
        if should_retry:
            _sleep(backoff_delay, sleep_func)
            backoff_delay = _next_backoff(backoff_delay, backoff_multiplier, backoff_max)
            continue

        return SinkDeliveryResult.failure(
            sink_name=sink.name,
            sink_type=sink.sink_type,
            event_name=event.event_name,
            attempts=attempt,
            status_code=status_code,
            error=f"Webhook returned HTTP {status_code}",
        )

    return SinkDeliveryResult.failure(
        sink_name=sink.name,
        sink_type=sink.sink_type,
        event_name=event.event_name,
        attempts=attempt_limit,
        error="Webhook delivery exhausted retry attempts",
    )


def build_webhook_request(sink: WebhookSinkConfig, event: NotificationEvent) -> WebhookRequest:
    """Build JSON webhook request payload for a notification event."""
    body = json.dumps(
        {
            "event_name": event.event_name,
            "event_id": event.event_id,
            "emitted_at": event.emitted_at,
            "payload": event.payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "yeehaw-notify/1",
    }
    headers.update(sink.headers)

    return WebhookRequest(
        url=sink.url,
        method=sink.method,
        headers=headers,
        timeout_sec=sink.bounded_timeout_sec(),
        body=body,
    )


def _transport_via_urllib(request: WebhookRequest) -> int:
    urllib_request = urllib.request.Request(
        url=request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urllib.request.urlopen(urllib_request, timeout=request.timeout_sec) as response:
            return int(response.getcode())
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError | OSError | urllib.error.URLError)


def _is_retryable_http_code(status_code: int) -> bool:
    return status_code in _RETRYABLE_HTTP_CODES or 500 <= status_code <= 599


def _next_backoff(current_delay: float, multiplier: float, max_delay: float) -> float:
    if current_delay <= 0 or max_delay <= 0:
        return 0.0
    return min(max_delay, current_delay * multiplier)


def _sleep(delay_sec: float, sleep_func: SleepFunc) -> None:
    if delay_sec <= 0:
        return
    sleep_func(delay_sec)


__all__ = [
    "WebhookRequest",
    "WebhookTransport",
    "build_webhook_request",
    "send_webhook",
]
