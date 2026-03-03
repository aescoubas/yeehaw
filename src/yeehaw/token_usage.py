"""Shared token usage parsing helpers for agent logs."""

from __future__ import annotations

import re

TOKEN_SCAN_WINDOW_LINES = 400
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TOTAL_TOKEN_PATTERNS = (
    re.compile(r"\btokens?\s+used\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btotal\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btokens?\s+total\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\btoken\s+usage\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"totalTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"totalTokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"total_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
INPUT_TOKEN_PATTERNS = (
    re.compile(r"\binput\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bprompt\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"inputTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"promptTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"input_tokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"prompt_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
OUTPUT_TOKEN_PATTERNS = (
    re.compile(r"\boutput\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bcompletion\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r"\bcandidate(?:s)?\s+tokens?\b[^0-9]{0,20}([0-9][0-9,_]*)", re.IGNORECASE),
    re.compile(r'"outputTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"completionTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"candidatesTokenCount"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"output_tokens"\s*:\s*([0-9][0-9,_]*)'),
    re.compile(r'"completion_tokens"\s*:\s*([0-9][0-9,_]*)'),
)
TOKEN_LINE_RE = re.compile(r"^\s*([0-9][0-9,]*)\s*$")


def parse_tokens_used(text: str) -> int | None:
    """Parse token usage from log text."""
    clean = ANSI_ESCAPE_RE.sub("", text)
    lines = clean.splitlines()[-TOKEN_SCAN_WINDOW_LINES:]
    tail = "\n".join(lines)

    total = last_pattern_value(tail, TOTAL_TOKEN_PATTERNS)
    if total is not None:
        return total

    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if "tokens used" not in line.lower():
            continue
        for next_idx in range(idx + 1, min(idx + 4, len(lines))):
            match = TOKEN_LINE_RE.match(lines[next_idx])
            if match is None:
                continue
            parsed = parse_int_token(match.group(1))
            if parsed is not None:
                return parsed

    input_tokens = last_pattern_value(tail, INPUT_TOKEN_PATTERNS)
    output_tokens = last_pattern_value(tail, OUTPUT_TOKEN_PATTERNS)
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens

    return None


def parse_int_token(value: str) -> int | None:
    """Parse integer token values with optional separators."""
    normalized = value.replace(",", "").replace("_", "").strip()
    if not normalized.isdigit():
        return None
    return int(normalized)


def last_pattern_value(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    """Return most-recent numeric value matched by any regex in patterns."""
    best: tuple[int, int] | None = None
    for pattern in patterns:
        for match in pattern.finditer(text):
            parsed = parse_int_token(match.group(1))
            if parsed is None:
                continue
            if best is None or match.start() > best[0]:
                best = (match.start(), parsed)
    return None if best is None else best[1]
