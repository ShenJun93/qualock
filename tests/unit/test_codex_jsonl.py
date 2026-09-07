from pathlib import Path

import pytest

from qualock.evidence.codex_jsonl import CodexEvidenceError, parse_codex_jsonl
from qualock.evidence.models import AgentEvidence, AgentEvidenceError
from qualock.qualification.models import Usage


def test_parses_usage_commands_file_changes_and_integrity_signals() -> None:
    lines = Path("tests/fixtures/codex/sample.jsonl").read_text(encoding="utf-8").splitlines()
    evidence = parse_codex_jsonl(lines)
    assert evidence.thread_id == "thread-1"
    assert evidence.input_tokens == 100
    assert evidence.cached_input_tokens == 40
    assert evidence.output_tokens == 20
    assert evidence.reasoning_output_tokens == 5
    assert evidence.commands[0].command == "pytest -q"
    assert evidence.file_changes == ["src/app.py"]
    assert evidence.web_searches == 1
    assert evidence.mcp_calls == 1
    assert len(evidence.unknown_events) == 1


def test_codex_usage_accumulates_trustworthy_completed_turns() -> None:
    lines = [
        '{"type":"turn.completed","usage":{"input_tokens":2,"cached_input_tokens":1,"output_tokens":3,"reasoning_output_tokens":2}}',
        '{"type":"turn.completed","usage":{"input_tokens":5,"cached_input_tokens":4,"output_tokens":7,"reasoning_output_tokens":3}}',
    ]
    evidence = parse_codex_jsonl(lines)
    assert evidence.input_tokens == 7
    assert evidence.cached_input_tokens == 5
    assert evidence.cache_write_input_tokens == 0
    assert evidence.output_tokens == 10
    assert evidence.reasoning_output_tokens == 5
    assert evidence.usage_observed is True


@pytest.mark.parametrize(
    ("usage_fragment", "input_tokens", "output_tokens"),
    [
        ("", 0, 0),
        (',"usage":[]', 0, 0),
        (',"usage":{"output_tokens":3}', 0, 3),
        (',"usage":{"input_tokens":2}', 2, 0),
    ],
)
def test_codex_usage_malformed_required_totals_make_attempt_unobserved(
    usage_fragment: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    lines = [
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":20}}',
        f'{{"type":"turn.completed"{usage_fragment}}}',
    ]

    evidence = parse_codex_jsonl(lines)

    assert evidence.input_tokens == 10 + input_tokens
    assert evidence.output_tokens == 20 + output_tokens
    assert evidence.usage_observed is False


@pytest.mark.parametrize(
    ("field", "value", "expected_input_tokens", "expected_output_tokens"),
    [
        ("input_tokens", "true", 0, 3),
        ("input_tokens", '"2"', 0, 3),
        ("input_tokens", "-2", -2, 3),
        ("output_tokens", "false", 2, 0),
        ("output_tokens", '"3"', 2, 0),
        ("output_tokens", "-3", 2, -3),
    ],
)
def test_codex_usage_validates_each_required_total_independently(
    field: str,
    value: str,
    expected_input_tokens: int,
    expected_output_tokens: int,
) -> None:
    usage = {"input_tokens": "2", "output_tokens": "3"}
    usage[field] = value
    evidence = parse_codex_jsonl([
        '{"type":"turn.completed","usage":'
        f'{{"input_tokens":{usage["input_tokens"]},'
        f'"output_tokens":{usage["output_tokens"]}}}}}'
    ])

    assert evidence.input_tokens == expected_input_tokens
    assert evidence.output_tokens == expected_output_tokens
    assert evidence.usage_observed is False


def test_codex_usage_later_valid_turn_cannot_restore_trust() -> None:
    evidence = parse_codex_jsonl([
        '{"type":"turn.completed","usage":{"input_tokens":-2,"output_tokens":3}}',
        '{"type":"turn.completed","usage":{"input_tokens":5,"cached_input_tokens":4,"output_tokens":7,"reasoning_output_tokens":2}}',
    ])

    assert evidence.input_tokens == 3
    assert evidence.cached_input_tokens == 4
    assert evidence.output_tokens == 10
    assert evidence.reasoning_output_tokens == 2
    assert evidence.usage_observed is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cached_input_tokens", "true"),
        ("cached_input_tokens", '"4"'),
        ("cached_input_tokens", "-4"),
        ("reasoning_output_tokens", "true"),
        ("reasoning_output_tokens", '"4"'),
        ("reasoning_output_tokens", "-4"),
    ],
)
def test_codex_usage_malformed_optional_details_normalize_to_zero(
    field: str, value: str
) -> None:
    evidence = parse_codex_jsonl([
        f'{{"type":"turn.completed","usage":{{"input_tokens":2,"output_tokens":3,"{field}":{value}}}}}'
    ])

    assert evidence.cached_input_tokens == 0
    assert evidence.reasoning_output_tokens == 0
    assert evidence.usage_observed is True


def test_codex_usage_constructs_canonical_usage_without_double_counting() -> None:
    evidence = parse_codex_jsonl([
        '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20,"reasoning_output_tokens":5}}'
    ])

    usage = Usage(
        input_tokens=evidence.input_tokens,
        cached_input_tokens=evidence.cached_input_tokens,
        cache_write_input_tokens=evidence.cache_write_input_tokens,
        output_tokens=evidence.output_tokens,
        reasoning_output_tokens=evidence.reasoning_output_tokens,
        observed=evidence.usage_observed,
    )
    assert usage.total_tokens == evidence.input_tokens + evidence.output_tokens


def test_codex_usage_does_not_retain_sensitive_wire_data() -> None:
    raw_line = (
        '{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3},'
        '"stdout":"stdout-secret","stderr":"stderr-secret",'
        '"transcript":"token-bearing-secret","credentials":"credential-secret"}'
    )

    evidence = parse_codex_jsonl([raw_line])
    retained = repr(vars(evidence))

    assert raw_line not in retained
    assert "stdout-secret" not in retained
    assert "stderr-secret" not in retained
    assert "token-bearing-secret" not in retained
    assert "credential-secret" not in retained


def test_malformed_json_is_invalid_evidence() -> None:
    with pytest.raises(CodexEvidenceError, match="line 2"):
        parse_codex_jsonl(['{"type":"thread.started"}', "not-json"])


def test_item_error_is_recorded_as_agent_error() -> None:
    evidence = parse_codex_jsonl([
        '{"type":"item.completed","item":{"type":"error","message":"missing code-mode host"}}'
    ])
    assert evidence.errors == ["missing code-mode host"]


def test_codex_parser_returns_normalized_agent_evidence() -> None:
    evidence = parse_codex_jsonl(['{"type":"turn.completed","usage":{"input_tokens":2}}'])
    assert isinstance(evidence, AgentEvidence)
    assert evidence.input_tokens == 2
    assert evidence.usage_observed is False


def test_codex_parse_error_is_generic_agent_evidence_error() -> None:
    with pytest.raises(AgentEvidenceError):
        parse_codex_jsonl(["not-json"])
