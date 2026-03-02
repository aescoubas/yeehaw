"""Notification sink dispatch orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from yeehaw.notify.models import NotificationConfig, NotificationEvent, SinkDeliveryResult, WebhookSinkConfig
from yeehaw.notify.webhook import send_webhook

WebhookSender = Callable[[WebhookSinkConfig, NotificationEvent], SinkDeliveryResult]


class NotificationDispatcher:
    """Thread-pooled sink dispatcher with fail-open semantics."""

    def __init__(
        self,
        config: NotificationConfig | Sequence[WebhookSinkConfig] | None = None,
        *,
        max_workers: int = 4,
        webhook_sender: WebhookSender | None = None,
    ) -> None:
        if config is None:
            self._config = NotificationConfig()
        elif isinstance(config, NotificationConfig):
            self._config = config
        else:
            self._config = NotificationConfig(sinks=tuple(config))

        self._webhook_sender = webhook_sender or send_webhook
        worker_count = max(1, max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="yeehaw-notify",
        )

    def dispatch(
        self,
        event_name: str,
        payload: Mapping[str, Any],
    ) -> tuple[Future[SinkDeliveryResult], ...]:
        """Dispatch one event to all matching sinks without blocking caller."""
        event = NotificationEvent(event_name=event_name, payload=dict(payload))
        futures: list[Future[SinkDeliveryResult]] = []
        for sink in self._config.matching_sinks(event_name):
            futures.append(self._submit_sink(sink, event))
        return tuple(futures)

    def dispatch_sync(
        self,
        event_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_sec: float | None = None,
    ) -> tuple[SinkDeliveryResult, ...]:
        """Dispatch and wait for all sink results."""
        return tuple(
            future.result(timeout=timeout_sec)
            for future in self.dispatch(event_name, payload)
        )

    def close(self, *, wait: bool = True) -> None:
        """Shutdown thread pool resources."""
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> NotificationDispatcher:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _submit_sink(
        self,
        sink: WebhookSinkConfig,
        event: NotificationEvent,
    ) -> Future[SinkDeliveryResult]:
        try:
            return self._executor.submit(self._dispatch_sink, sink, event)
        except RuntimeError as exc:
            failed = SinkDeliveryResult.failure(
                sink_name=sink.name,
                sink_type=sink.sink_type,
                event_name=event.event_name,
                attempts=0,
                error=f"Notification dispatcher unavailable: {exc}",
            )
            future: Future[SinkDeliveryResult] = Future()
            future.set_result(failed)
            return future

    def _dispatch_sink(self, sink: WebhookSinkConfig, event: NotificationEvent) -> SinkDeliveryResult:
        try:
            return self._webhook_sender(sink, event)
        except Exception as exc:  # pragma: no cover - safety net
            return SinkDeliveryResult.failure(
                sink_name=sink.name,
                sink_type=sink.sink_type,
                event_name=event.event_name,
                attempts=0,
                error=f"Unhandled notification sink failure: {exc}",
            )


__all__ = ["NotificationDispatcher", "WebhookSender"]
