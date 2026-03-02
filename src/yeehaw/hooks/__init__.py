"""Hook discovery and protocol models."""

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
from yeehaw.hooks.loader import (
    DEFAULT_HOOK_TIMEOUT_MS,
    MAX_HOOK_TIMEOUT_MS,
    discover_hooks,
    load_hooks,
)
from yeehaw.hooks.models import (
    HOOK_RESPONSE_STATUSES,
    HookAction,
    HookDefinition,
    HookRequest,
    HookResponse,
)
from yeehaw.hooks.runner import (
    DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES,
    HookRunResult,
    parse_hook_response,
    parse_hook_response_payload,
    run_hook,
    run_hooks,
)

__all__ = [
    "DEFAULT_HOOK_TIMEOUT_MS",
    "DEFAULT_HOOK_PAYLOAD_LIMIT_BYTES",
    "HookExecutionError",
    "HookOutputDecodeError",
    "HookPayloadTooLargeError",
    "HookRequestSerializationError",
    "HookResponseParseError",
    "HookResponseValidationError",
    "HookRunResult",
    "HOOK_RESPONSE_STATUSES",
    "HookRuntimeError",
    "HookSpawnError",
    "HookTimeoutError",
    "MAX_HOOK_TIMEOUT_MS",
    "HookAction",
    "HookDefinition",
    "HookRequest",
    "HookResponse",
    "discover_hooks",
    "load_hooks",
    "parse_hook_response",
    "parse_hook_response_payload",
    "run_hook",
    "run_hooks",
]
