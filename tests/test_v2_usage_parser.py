from __future__ import annotations

from yeehaw_v2.usage_parser import parse_usage_snapshots


def test_parse_usage_snapshots_from_plain_text_and_context() -> None:
    text = "\n".join(
        [
            "provider=openai model=gpt-5",
            "input tokens: 1,000 output tokens: 250 cost_usd=1.75",
            "input=120 out=80 $0.25",
        ]
    )
    snapshots = parse_usage_snapshots(text)
    assert len(snapshots) == 2
    assert snapshots[0].provider == "openai"
    assert snapshots[0].model == "gpt-5"
    assert snapshots[0].input_tokens == 1000
    assert snapshots[0].output_tokens == 250
    assert snapshots[0].cost_usd == 1.75
    assert snapshots[1].provider == "openai"
    assert snapshots[1].model == "gpt-5"
    assert snapshots[1].input_tokens == 120
    assert snapshots[1].output_tokens == 80
    assert snapshots[1].cost_usd == 0.25


def test_parse_usage_snapshots_from_json_line() -> None:
    text = '{"provider":"anthropic","model":"claude-sonnet-4","input_tokens":500,"output_tokens":75,"cost_usd":0.91}'
    snapshots = parse_usage_snapshots(text)
    assert len(snapshots) == 1
    assert snapshots[0].provider == "anthropic"
    assert snapshots[0].model == "claude-sonnet-4"
    assert snapshots[0].input_tokens == 500
    assert snapshots[0].output_tokens == 75
    assert snapshots[0].cost_usd == 0.91
