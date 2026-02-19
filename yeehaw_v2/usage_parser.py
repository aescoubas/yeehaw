from __future__ import annotations

import json
import re
from dataclasses import dataclass


_INT_RE = r"([0-9][0-9_,]*)"
_FLOAT_RE = r"([0-9]+(?:\.[0-9]+)?)"


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _to_int(raw: str | None) -> int:
    if not raw:
        return 0
    cleaned = raw.replace(",", "").replace("_", "").strip()
    if not cleaned or cleaned.lower() == "none":
        return 0
    return int(cleaned)


def _to_float(raw: str | None) -> float:
    if not raw:
        return 0.0
    cleaned = raw.strip()
    if not cleaned or cleaned.lower() == "none":
        return 0.0
    return float(cleaned)


def _clean(raw: str | None, default: str) -> str:
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def _try_parse_json_line(line: str) -> UsageSnapshot | None:
    if "{" not in line or "}" not in line:
        return None
    text = line.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    provider = _clean(payload.get("provider"), "unknown")
    model = _clean(payload.get("model"), "unknown")
    input_tokens = _to_int(None if payload.get("input_tokens") is None else str(payload.get("input_tokens")))
    output_tokens = _to_int(None if payload.get("output_tokens") is None else str(payload.get("output_tokens")))
    cost_raw = payload.get("cost_usd", payload.get("cost"))
    cost_usd = _to_float(None if cost_raw is None else str(cost_raw))
    if input_tokens == 0 and output_tokens == 0 and cost_usd <= 0:
        return None
    return UsageSnapshot(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def parse_usage_snapshots(text: str, default_provider: str = "unknown", default_model: str = "unknown") -> list[UsageSnapshot]:
    provider_ctx = default_provider
    model_ctx = default_model
    snapshots: list[UsageSnapshot] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parsed_json = _try_parse_json_line(line)
        if parsed_json is not None:
            snapshots.append(parsed_json)
            provider_ctx = parsed_json.provider
            model_ctx = parsed_json.model
            continue

        provider_match = re.search(r"(?i)\bprovider\s*[:=]\s*([a-z0-9._-]+)", line)
        model_match = re.search(r"(?i)\bmodel\s*[:=]\s*([a-zA-Z0-9._:/-]+)", line)
        if provider_match:
            provider_ctx = _clean(provider_match.group(1), provider_ctx)
        if model_match:
            model_ctx = _clean(model_match.group(1), model_ctx)

        input_match = re.search(rf"(?i)\b(?:input|prompt)\s+tokens?\s*[:=]\s*{_INT_RE}", line)
        output_match = re.search(rf"(?i)\b(?:output|completion)\s+tokens?\s*[:=]\s*{_INT_RE}", line)
        cost_match = re.search(rf"(?i)\bcost(?:[_\s]?usd)?\s*[:=]\s*\$?\s*{_FLOAT_RE}", line)

        if input_match is None:
            input_match = re.search(rf"(?i)\bin(?:put)?\s*=\s*{_INT_RE}", line)
        if output_match is None:
            output_match = re.search(rf"(?i)\bout(?:put)?\s*=\s*{_INT_RE}", line)
        if cost_match is None:
            cost_match = re.search(rf"(?i)\$\s*{_FLOAT_RE}", line)

        input_tokens = _to_int(input_match.group(1) if input_match else None)
        output_tokens = _to_int(output_match.group(1) if output_match else None)
        cost_usd = _to_float(cost_match.group(1) if cost_match else None)

        if input_tokens == 0 and output_tokens == 0 and cost_usd <= 0:
            continue

        snapshots.append(
            UsageSnapshot(
                provider=provider_ctx,
                model=model_ctx,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
        )

    return snapshots
