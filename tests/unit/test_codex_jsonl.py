from pathlib import Path

import pytest

from qualock.evidence.codex_jsonl import CodexEvidenceError, parse_codex_jsonl


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


def test_accumulates_usage_across_completed_turns() -> None:
    lines = [
        '{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3}}',
        '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":7}}',
    ]
    evidence = parse_codex_jsonl(lines)
    assert evidence.input_tokens == 7
    assert evidence.output_tokens == 10


def test_malformed_json_is_invalid_evidence() -> None:
    with pytest.raises(CodexEvidenceError, match="line 2"):
        parse_codex_jsonl(['{"type":"thread.started"}', "not-json"])


def test_item_error_is_recorded_as_agent_error() -> None:
    evidence = parse_codex_jsonl([
        '{"type":"item.completed","item":{"type":"error","message":"missing code-mode host"}}'
    ])
    assert evidence.errors == ["missing code-mode host"]
