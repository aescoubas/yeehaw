"""Hook execution with timeout isolation and structured response parsing."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any

from yeehaw.hooks.errors import (
    HookExecutionError,
    HookOutputDecodeError,
    HookPayloadTooLargeError,
    HookRequestSerializationError,
    HookResponseParseError,
    HookResponseValidationError,
    HookRuntimeError,
    HookSpawnError,
    HookTimeoutError,
)
from yeehaw.hooks.models import (
    HOOK_RESPONSE_STATUSES,
    HookAction,
    HookDefinition,
    HookRequest,
    HookResponse,
)

DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES = 65_536


@dataclass(frozen=True)
class HookRunResult:
    """Result of a single hook invocation."""

    hook: HookDefinition
    request: HookRequest
    response: HookResponse | None
    error: HookRuntimeError | None
    returncode: int | None
    duration_ms: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.error is None and self.response is not None


def run_hooks(
    hooks: Iterable[HookDefinition],
    request: HookRequest,
    *,
    strict: bool = False,
    payload_limit_bytes: int = DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES,
    env: Mapping[str, str] | None = None,
) -> list[HookRunResult]:
    """Run hooks sequentially for one event request."""
    return [
        run_hook(
            hook,
            request,
            strict=strict,
            payload_limit_bytes=payload_limit_bytes,
            env=env,
        )
        for hook in hooks
    ]


def run_hook(
    hook: HookDefinition,
    request: HookRequest,
    *,
    strict: bool = False,
    payload_limit_bytes: int = DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES,
    env: Mapping[str, str] | None = None,
) -> HookRunResult:
    """Execute a hook subprocess and parse a structured JSON response.

    In default fail-open mode (`strict=False`), invocation failures are returned
    in `HookRunResult.error` and no exception is raised.
    """
    if payload_limit_bytes < 1:
        raise ValueError("payload_limit_bytes must be >= 1")

    started = perf_counter()
    empty_stdout = ""
    empty_stderr = ""

    try:
        request_bytes = _serialize_request(
            hook,
            request,
            payload_limit_bytes=payload_limit_bytes,
        )
    except HookRuntimeError as exc:
        return _handle_error(
            hook,
            request,
            exc,
            strict=strict,
            started=started,
            returncode=None,
            stdout=empty_stdout,
            stderr=empty_stderr,
        )

    child_env = dict(os.environ)
    if env:
        child_env.update(env)

    try:
        completed = subprocess.run(
            [str(hook.entrypoint)],
            input=request_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=hook.timeout_ms / 1000,
            check=False,
            cwd=hook.entrypoint.parent,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_text = _decode_diagnostic_output(exc.stdout)
        stderr_text = _decode_diagnostic_output(exc.stderr)
        timeout_error = HookTimeoutError(
            (
                f"Hook '{hook.name}' timed out after {hook.timeout_ms}ms "
                f"for event '{request.event_name}'"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            timeout_ms=hook.timeout_ms,
            stdout=stdout_text,
            stderr=stderr_text,
        )
        return _handle_error(
            hook,
            request,
            timeout_error,
            strict=strict,
            started=started,
            returncode=124,
            stdout=stdout_text,
            stderr=stderr_text,
        )
    except OSError as exc:
        spawn_error = HookSpawnError(
            (
                f"Failed to start hook '{hook.name}' for event "
                f"'{request.event_name}': {exc}"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
        )
        return _handle_error(
            hook,
            request,
            spawn_error,
            strict=strict,
            started=started,
            returncode=None,
            stdout=empty_stdout,
            stderr=empty_stderr,
        )

    stdout_bytes = completed.stdout or b""
    stderr_bytes = completed.stderr or b""
    size_error = _validate_output_sizes(
        hook,
        request,
        stdout=stdout_bytes,
        stderr=stderr_bytes,
        payload_limit_bytes=payload_limit_bytes,
    )
    if size_error is not None:
        stdout_text = _decode_diagnostic_output(stdout_bytes)
        stderr_text = _decode_diagnostic_output(stderr_bytes)
        return _handle_error(
            hook,
            request,
            size_error,
            strict=strict,
            started=started,
            returncode=completed.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    try:
        stdout_text = _decode_output(
            stdout_bytes,
            stream="stdout",
            hook=hook,
            request=request,
        )
        stderr_text = _decode_output(
            stderr_bytes,
            stream="stderr",
            hook=hook,
            request=request,
        )
    except HookRuntimeError as exc:
        return _handle_error(
            hook,
            request,
            exc,
            strict=strict,
            started=started,
            returncode=completed.returncode,
            stdout=_decode_diagnostic_output(stdout_bytes),
            stderr=_decode_diagnostic_output(stderr_bytes),
        )

    if completed.returncode != 0:
        execution_error = HookExecutionError(
            (
                f"Hook '{hook.name}' exited with code {completed.returncode} "
                f"for event '{request.event_name}'"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            returncode=completed.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )
        return _handle_error(
            hook,
            request,
            execution_error,
            strict=strict,
            started=started,
            returncode=completed.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    try:
        response = parse_hook_response(
            hook=hook,
            request=request,
            raw_stdout=stdout_text,
        )
    except HookRuntimeError as exc:
        return _handle_error(
            hook,
            request,
            exc,
            strict=strict,
            started=started,
            returncode=completed.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    return HookRunResult(
        hook=hook,
        request=request,
        response=response,
        error=None,
        returncode=completed.returncode,
        duration_ms=_elapsed_ms(started),
        stdout=stdout_text,
        stderr=stderr_text,
    )


def parse_hook_response(
    *,
    hook: HookDefinition,
    request: HookRequest,
    raw_stdout: str,
) -> HookResponse:
    """Parse and validate a hook JSON response payload from stdout."""
    content = raw_stdout.strip()
    if not content:
        raise HookResponseParseError(
            (
                f"Hook '{hook.name}' did not emit JSON response for event "
                f"'{request.event_name}'"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stdout=raw_stdout,
        )

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HookResponseParseError(
            (
                f"Hook '{hook.name}' emitted invalid JSON response for event "
                f"'{request.event_name}': {exc}"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stdout=raw_stdout,
        ) from exc

    if not isinstance(payload, dict):
        raise HookResponseValidationError(
            (
                f"Hook '{hook.name}' response must be a JSON object for event "
                f"'{request.event_name}'"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stdout=raw_stdout,
        )

    return parse_hook_response_payload(
        hook=hook,
        request=request,
        payload=payload,
        raw_stdout=raw_stdout,
    )


def parse_hook_response_payload(
    *,
    hook: HookDefinition,
    request: HookRequest,
    payload: dict[str, Any],
    raw_stdout: str,
) -> HookResponse:
    """Validate a decoded hook response payload and build HookResponse."""
    schema_version = _require_int_field(
        payload,
        key="schema_version",
        hook=hook,
        request=request,
        raw_stdout=raw_stdout,
    )
    if schema_version != request.schema_version:
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=(
                "response schema_version "
                f"{schema_version} does not match request schema_version "
                f"{request.schema_version}"
            ),
        )

    event_id = _require_non_empty_string_field(
        payload,
        key="event_id",
        hook=hook,
        request=request,
        raw_stdout=raw_stdout,
    )
    if event_id != request.event_id:
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response event_id {event_id!r} does not match request event_id {request.event_id!r}",
        )

    extension = _require_non_empty_string_field(
        payload,
        key="extension",
        hook=hook,
        request=request,
        raw_stdout=raw_stdout,
    )
    status = _require_non_empty_string_field(
        payload,
        key="status",
        hook=hook,
        request=request,
        raw_stdout=raw_stdout,
    )
    if status not in HOOK_RESPONSE_STATUSES:
        expected = ", ".join(HOOK_RESPONSE_STATUSES)
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response status must be one of [{expected}], got {status!r}",
        )

    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message="response field 'summary' must be a string when provided",
        )

    actions_raw = payload.get("actions", [])
    if not isinstance(actions_raw, list):
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message="response field 'actions' must be a list",
        )
    actions = tuple(
        _parse_action(
            item=action,
            index=index,
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
        )
        for index, action in enumerate(actions_raw)
    )

    metrics_raw = payload.get("metrics", {})
    if not isinstance(metrics_raw, dict):
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message="response field 'metrics' must be an object",
        )

    return HookResponse(
        schema_version=schema_version,
        event_id=event_id,
        extension=extension,
        status=status,
        summary=summary,
        actions=actions,
        metrics=dict(metrics_raw),
    )


def _parse_action(
    *,
    item: Any,
    index: int,
    hook: HookDefinition,
    request: HookRequest,
    raw_stdout: str,
) -> HookAction:
    if not isinstance(item, dict):
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response action at index {index} must be an object",
        )

    action_type = item.get("type")
    if not isinstance(action_type, str) or not action_type.strip():
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response action at index {index} must include non-empty string 'type'",
        )

    if "payload" in item:
        action_payload = item.get("payload")
        if not isinstance(action_payload, dict):
            raise _schema_error(
                hook=hook,
                request=request,
                raw_stdout=raw_stdout,
                message=f"response action payload at index {index} must be an object",
            )
        payload = dict(action_payload)
        for key, value in item.items():
            if key not in {"type", "payload"} and key not in payload:
                payload[key] = value
    else:
        payload = {key: value for key, value in item.items() if key != "type"}

    return HookAction(type=action_type.strip(), payload=payload)


def _serialize_request(
    hook: HookDefinition,
    request: HookRequest,
    *,
    payload_limit_bytes: int,
) -> bytes:
    try:
        raw_request = json.dumps(asdict(request), separators=(",", ":"), sort_keys=True)
    except TypeError as exc:
        raise HookRequestSerializationError(
            (
                f"Hook request for '{hook.name}' event "
                f"'{request.event_name}' is not JSON serializable: {exc}"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
        ) from exc

    request_bytes = raw_request.encode("utf-8")
    if len(request_bytes) > payload_limit_bytes:
        raise HookPayloadTooLargeError(
            (
                f"Hook request payload for '{hook.name}' exceeds "
                f"{payload_limit_bytes} bytes"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stream="stdin",
            size_bytes=len(request_bytes),
            max_bytes=payload_limit_bytes,
        )

    return request_bytes


def _validate_output_sizes(
    hook: HookDefinition,
    request: HookRequest,
    *,
    stdout: bytes,
    stderr: bytes,
    payload_limit_bytes: int,
) -> HookPayloadTooLargeError | None:
    if len(stdout) > payload_limit_bytes:
        return HookPayloadTooLargeError(
            (
                f"Hook stdout payload for '{hook.name}' exceeds "
                f"{payload_limit_bytes} bytes"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stream="stdout",
            size_bytes=len(stdout),
            max_bytes=payload_limit_bytes,
        )

    if len(stderr) > payload_limit_bytes:
        return HookPayloadTooLargeError(
            (
                f"Hook stderr payload for '{hook.name}' exceeds "
                f"{payload_limit_bytes} bytes"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
            stream="stderr",
            size_bytes=len(stderr),
            max_bytes=payload_limit_bytes,
        )

    return None


def _decode_output(
    payload: bytes,
    *,
    stream: str,
    hook: HookDefinition,
    request: HookRequest,
) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HookOutputDecodeError(
            (
                f"Hook '{hook.name}' emitted non-UTF-8 {stream} for event "
                f"'{request.event_name}'"
            ),
            hook_name=hook.name,
            entrypoint=hook.entrypoint,
            event_name=request.event_name,
            event_id=request.event_id,
        ) from exc


def _decode_diagnostic_output(payload: bytes | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return payload.decode("utf-8", errors="replace")


def _require_non_empty_string_field(
    payload: dict[str, Any],
    *,
    key: str,
    hook: HookDefinition,
    request: HookRequest,
    raw_stdout: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response field {key!r} must be a non-empty string",
        )
    return value.strip()


def _require_int_field(
    payload: dict[str, Any],
    *,
    key: str,
    hook: HookDefinition,
    request: HookRequest,
    raw_stdout: str,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _schema_error(
            hook=hook,
            request=request,
            raw_stdout=raw_stdout,
            message=f"response field {key!r} must be an integer",
        )
    return value


def _schema_error(
    *,
    hook: HookDefinition,
    request: HookRequest,
    raw_stdout: str,
    message: str,
) -> HookResponseValidationError:
    return HookResponseValidationError(
        (
            f"Hook '{hook.name}' emitted invalid response schema for event "
            f"'{request.event_name}': {message}"
        ),
        hook_name=hook.name,
        entrypoint=hook.entrypoint,
        event_name=request.event_name,
        event_id=request.event_id,
        stdout=raw_stdout,
    )


def _handle_error(
    hook: HookDefinition,
    request: HookRequest,
    error: HookRuntimeError,
    *,
    strict: bool,
    started: float,
    returncode: int | None,
    stdout: str,
    stderr: str,
) -> HookRunResult:
    if strict:
        raise error

    return HookRunResult(
        hook=hook,
        request=request,
        response=None,
        error=error,
        returncode=returncode,
        duration_ms=_elapsed_ms(started),
        stdout=stdout,
        stderr=stderr,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


__all__ = [
    "DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES",
    "HookRunResult",
    "parse_hook_response",
    "parse_hook_response_payload",
    "run_hook",
    "run_hooks",
]
