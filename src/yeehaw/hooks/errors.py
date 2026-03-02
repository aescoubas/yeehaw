"""Typed runtime errors for hook execution and protocol validation."""

from __future__ import annotations

from pathlib import Path


class HookRuntimeError(RuntimeError):
    """Base runtime error for hook invocation failures."""

    def __init__(
        self,
        message: str,
        *,
        hook_name: str,
        entrypoint: Path,
        event_name: str,
        event_id: str,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.hook_name = hook_name
        self.entrypoint = entrypoint
        self.event_name = event_name
        self.event_id = event_id
        self.stdout = stdout
        self.stderr = stderr


class HookRequestSerializationError(HookRuntimeError):
    """Raised when a HookRequest payload cannot be JSON-serialized."""


class HookSpawnError(HookRuntimeError):
    """Raised when the hook process cannot be started."""


class HookTimeoutError(HookRuntimeError):
    """Raised when a hook invocation exceeds its timeout."""

    def __init__(
        self,
        message: str,
        *,
        hook_name: str,
        entrypoint: Path,
        event_name: str,
        event_id: str,
        timeout_ms: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            message,
            hook_name=hook_name,
            entrypoint=entrypoint,
            event_name=event_name,
            event_id=event_id,
            stdout=stdout,
            stderr=stderr,
        )
        self.timeout_ms = timeout_ms


class HookExecutionError(HookRuntimeError):
    """Raised when a hook exits with a non-zero return code."""

    def __init__(
        self,
        message: str,
        *,
        hook_name: str,
        entrypoint: Path,
        event_name: str,
        event_id: str,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            message,
            hook_name=hook_name,
            entrypoint=entrypoint,
            event_name=event_name,
            event_id=event_id,
            stdout=stdout,
            stderr=stderr,
        )
        self.returncode = returncode


class HookPayloadTooLargeError(HookRuntimeError):
    """Raised when hook request/response payload exceeds configured limits."""

    def __init__(
        self,
        message: str,
        *,
        hook_name: str,
        entrypoint: Path,
        event_name: str,
        event_id: str,
        stream: str,
        size_bytes: int,
        max_bytes: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(
            message,
            hook_name=hook_name,
            entrypoint=entrypoint,
            event_name=event_name,
            event_id=event_id,
            stdout=stdout,
            stderr=stderr,
        )
        self.stream = stream
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class HookOutputDecodeError(HookRuntimeError):
    """Raised when hook output is not valid UTF-8 text."""


class HookResponseParseError(HookRuntimeError):
    """Raised when hook stdout is not valid JSON."""


class HookResponseValidationError(HookRuntimeError):
    """Raised when hook JSON response does not match schema requirements."""


__all__ = [
    "HookExecutionError",
    "HookOutputDecodeError",
    "HookPayloadTooLargeError",
    "HookRequestSerializationError",
    "HookResponseParseError",
    "HookResponseValidationError",
    "HookRuntimeError",
    "HookSpawnError",
    "HookTimeoutError",
]
