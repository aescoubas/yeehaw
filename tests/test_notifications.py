"""Tests for notification sink models, webhook delivery, and dispatching."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from yeehaw.notify.dispatcher import NotificationDispatcher
from yeehaw.notify.models import (
    MAX_WEBHOOK_MAX_ATTEMPTS,
    NotificationConfig,
    NotificationEvent,
    SinkDeliveryResult,
    WebhookSinkConfig,
    load_notification_config,
    parse_notification_config,
)
from yeehaw.notify.webhook import WebhookRequest, send_webhook


def test_parse_notification_config_webhook_sink() -> None:
    config = parse_notification_config(
        {
            "sinks": [
                {
                    "type": "webhook",
                    "name": "ops",
                    "url": "https://notify.example.com/hooks",
                    "events": ["task_done", "task_failed"],
                    "headers": {"Authorization": "Bearer token"},
                    "max_attempts": 4,
                    "backoff_initial_sec": 0.1,
                    "backoff_multiplier": 2.0,
                    "backoff_max_sec": 0.5,
                }
            ]
        }
    )

    assert len(config.sinks) == 1
    sink = config.sinks[0]
    assert sink.name == "ops"
    assert sink.url == "https://notify.example.com/hooks"
    assert sink.events == ("task_done", "task_failed")
    assert sink.headers["Authorization"] == "Bearer token"
    assert sink.max_attempts == 4


def test_parse_notification_config_accepts_tuple_event_filters() -> None:
    config = parse_notification_config(
        {
            "sinks": [
                {
                    "type": "webhook",
                    "name": "ops",
                    "url": "https://notify.example.com/hooks",
                    "events": ("task_done", "task_failed"),
                }
            ]
        }
    )

    assert config.sinks[0].events == ("task_done", "task_failed")


def test_parse_notification_config_rejects_unknown_sink_type() -> None:
    with pytest.raises(ValueError, match="unsupported sink type"):
        parse_notification_config(
            {
                "sinks": [
                    {
                        "type": "email",
                        "name": "mail",
                    }
                ]
            }
        )


def test_load_notification_config_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "notifications.json"
    config_path.write_text(
        json.dumps(
            {
                "sinks": [
                    {
                        "type": "webhook",
                        "name": "ops",
                        "url": "https://notify.example.com/hooks",
                    }
                ]
            }
        )
    )

    loaded = load_notification_config(config_path)

    assert len(loaded.sinks) == 1
    assert loaded.sinks[0].name == "ops"


def test_send_webhook_retries_transient_failures_with_backoff() -> None:
    sink = WebhookSinkConfig(
        name="ops",
        url="https://notify.example.com/hooks",
        max_attempts=4,
        backoff_initial_sec=0.1,
        backoff_multiplier=2.0,
        backoff_max_sec=0.2,
    )
    event = NotificationEvent(event_name="task_done", payload={"task_id": 1})

    attempts = {"count": 0}
    slept: list[float] = []

    def flaky_transport(_request: WebhookRequest) -> int:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("timed out")
        return 204

    result = send_webhook(
        sink,
        event,
        transport=flaky_transport,
        sleep_func=lambda delay: slept.append(delay),
    )

    assert result.ok is True
    assert result.attempts == 3
    assert attempts["count"] == 3
    assert slept == [0.1, 0.2]


def test_send_webhook_retry_count_is_bounded() -> None:
    sink = WebhookSinkConfig(
        name="ops",
        url="https://notify.example.com/hooks",
        max_attempts=99,
        backoff_initial_sec=0.0,
        backoff_multiplier=2.0,
        backoff_max_sec=0.0,
    )
    event = NotificationEvent(event_name="task_done", payload={"task_id": 1})

    attempts = {"count": 0}

    def always_fail_transport(_request: WebhookRequest) -> int:
        attempts["count"] += 1
        raise TimeoutError("still down")

    result = send_webhook(sink, event, transport=always_fail_transport)

    assert result.ok is False
    assert result.attempts == MAX_WEBHOOK_MAX_ATTEMPTS
    assert attempts["count"] == MAX_WEBHOOK_MAX_ATTEMPTS


def test_send_webhook_non_retryable_status_fails_fast() -> None:
    sink = WebhookSinkConfig(
        name="ops",
        url="https://notify.example.com/hooks",
        max_attempts=4,
        backoff_initial_sec=0.2,
        backoff_multiplier=2.0,
        backoff_max_sec=1.0,
    )
    event = NotificationEvent(event_name="task_failed", payload={"task_id": 4})

    attempts = {"count": 0}
    slept: list[float] = []

    def bad_request_transport(_request: WebhookRequest) -> int:
        attempts["count"] += 1
        return 400

    result = send_webhook(
        sink,
        event,
        transport=bad_request_transport,
        sleep_func=lambda delay: slept.append(delay),
    )

    assert result.ok is False
    assert result.attempts == 1
    assert result.status_code == 400
    assert attempts["count"] == 1
    assert slept == []


def test_send_webhook_invalid_transport_status_is_failure() -> None:
    sink = WebhookSinkConfig(
        name="ops",
        url="https://notify.example.com/hooks",
        max_attempts=2,
    )
    event = NotificationEvent(event_name="task_done", payload={"task_id": 4})

    result = send_webhook(sink, event, transport=lambda _request: "204")  # type: ignore[return-value]

    assert result.ok is False
    assert result.attempts == 1
    assert result.error is not None
    assert "invalid HTTP status code" in result.error


def test_dispatcher_fail_open_with_background_dispatch() -> None:
    slow_sink = WebhookSinkConfig(name="slow", url="https://notify.example.com/slow")
    failing_sink = WebhookSinkConfig(name="fail", url="https://notify.example.com/fail")
    config = NotificationConfig(sinks=(slow_sink, failing_sink))
    gate = threading.Event()

    def fake_sender(sink: WebhookSinkConfig, event: NotificationEvent) -> SinkDeliveryResult:
        if sink.name == "slow":
            gate.wait(timeout=1.0)
            return SinkDeliveryResult.success(
                sink_name=sink.name,
                sink_type=sink.sink_type,
                event_name=event.event_name,
                attempts=1,
                status_code=204,
            )
        raise RuntimeError("simulated sink crash")

    dispatcher = NotificationDispatcher(config, max_workers=2, webhook_sender=fake_sender)
    try:
        futures = dispatcher.dispatch("task_done", {"task_id": 11})

        assert len(futures) == 2
        assert any(not future.done() for future in futures)

        gate.set()
        results = tuple(future.result(timeout=1.0) for future in futures)
    finally:
        dispatcher.close()

    by_name = {result.sink_name: result for result in results}
    assert by_name["slow"].ok is True
    assert by_name["fail"].ok is False
    assert "Unhandled notification sink failure" in (by_name["fail"].error or "")


def test_dispatcher_filters_unsubscribed_events() -> None:
    sink = WebhookSinkConfig(
        name="ops",
        url="https://notify.example.com/hooks",
        events=("task_done",),
    )

    dispatcher = NotificationDispatcher(NotificationConfig(sinks=(sink,)))
    try:
        dispatched = dispatcher.dispatch("task_failed", {"task_id": 2})
    finally:
        dispatcher.close()

    assert dispatched == ()


def test_dispatcher_dispatch_after_close_returns_failed_future() -> None:
    sink = WebhookSinkConfig(name="ops", url="https://notify.example.com/hooks")
    dispatcher = NotificationDispatcher(NotificationConfig(sinks=(sink,)))
    dispatcher.close()

    futures = dispatcher.dispatch("task_done", {"task_id": 9})
    assert len(futures) == 1

    result = futures[0].result(timeout=0.2)
    assert result.ok is False
    assert result.attempts == 0
    assert "dispatcher unavailable" in (result.error or "").lower()
