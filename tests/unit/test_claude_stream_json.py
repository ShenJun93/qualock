import json

import pytest

from qualock.evidence.claude_stream_json import ClaudeEvidenceError, parse_claude_stream_json
from qualock.evidence.models import AgentEvidenceError


def line(payload: object) -> str:
    return json.dumps(payload)


def result_usage() -> dict[str, int]:
    return {
        "input_tokens": 10,
        "cache_read_input_tokens": 4,
        "output_tokens": 3,
    }


def test_parses_session_tools_and_final_usage() -> None:
    evidence = parse_claude_stream_json(
        [
            line({"type": "system", "subtype": "init", "session_id": "s1"}),
            line(
                {
                    "type": "assistant",
                    "message": {
                        "usage": {"input_tokens": 999, "output_tokens": 999},
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "pytest -q"},
                            },
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {"file_path": "src/app.py"},
                            },
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"path": "README.md"},
                            },
                            {"type": "tool_use", "name": "WebSearch", "input": {}},
                            {"type": "tool_use", "name": "WebFetch", "input": {}},
                            {"type": "tool_use", "name": "mcp__server__tool", "input": {}},
                        ],
                    },
                }
            ),
            line(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 4,
                        "output_tokens": 3,
                    },
                    "permission_denials": [],
                }
            ),
        ]
    )

    assert evidence.thread_id == "s1"
    assert [(item.command, item.exit_code) for item in evidence.commands] == [("pytest -q", None)]
    assert evidence.file_changes == ["src/app.py", "README.md"]
    assert evidence.web_searches == 2
    assert evidence.mcp_calls == 1
    assert evidence.input_tokens == 10
    assert evidence.cached_input_tokens == 4
    assert evidence.output_tokens == 3
    assert evidence.reasoning_output_tokens == 0
    assert evidence.errors == []


def test_non_success_result_records_error() -> None:
    evidence = parse_claude_stream_json(
        [line({"type": "result", "subtype": "error_during_execution", "is_error": True, "usage": result_usage()})]
    )

    assert any("error_during_execution" in error for error in evidence.errors)


def test_permission_denial_records_error() -> None:
    evidence = parse_claude_stream_json(
        [
            line(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "permission_denials": [{"tool_name": "Bash", "reason": "blocked"}],
                    "usage": result_usage(),
                }
            )
        ]
    )

    assert any("permission" in error.lower() for error in evidence.errors)


def test_unknown_top_level_event_is_retained() -> None:
    payload = {"type": "future.event", "value": 1}
    evidence = parse_claude_stream_json(
        [line(payload), line({"type": "result", "subtype": "success", "usage": result_usage()})]
    )

    assert evidence.unknown_events == [payload]


def test_empty_lines_are_ignored() -> None:
    evidence = parse_claude_stream_json(["", "   ", line({"type": "result", "subtype": "success", "usage": result_usage()})])

    assert evidence.errors == []


def test_malformed_json_fails_closed() -> None:
    with pytest.raises(ClaudeEvidenceError, match="invalid JSONL at line 1"):
        parse_claude_stream_json(["{"])


def test_non_object_json_fails_closed() -> None:
    with pytest.raises(ClaudeEvidenceError, match="expected object"):
        parse_claude_stream_json(["[]"])


def test_claude_error_is_generic_agent_evidence_error() -> None:
    assert issubclass(ClaudeEvidenceError, AgentEvidenceError)


def test_missing_final_result_fails_closed() -> None:
    with pytest.raises(ClaudeEvidenceError, match="missing Claude result event"):
        parse_claude_stream_json(
            [line({"type": "system", "subtype": "init", "session_id": "s1"})]
        )


def test_result_requires_subtype_and_usage() -> None:
    with pytest.raises(ClaudeEvidenceError, match="result subtype"):
        parse_claude_stream_json([line({"type": "result", "usage": result_usage()})])

    with pytest.raises(ClaudeEvidenceError, match="result usage"):
        parse_claude_stream_json([line({"type": "result", "subtype": "success"})])


def test_result_usage_requires_integer_fields() -> None:
    bad = result_usage()
    bad["output_tokens"] = True
    with pytest.raises(ClaudeEvidenceError, match="output_tokens"):
        parse_claude_stream_json(
            [line({"type": "result", "subtype": "success", "usage": bad})]
        )


def test_user_tool_result_updates_bash_outcome_and_records_error() -> None:
    evidence = parse_claude_stream_json(
        [
            line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Bash",
                                "input": {"command": "false"},
                            }
                        ]
                    },
                }
            ),
            line(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "is_error": True,
                                "exit_code": 1,
                                "content": "command failed",
                            }
                        ]
                    },
                }
            ),
            line({"type": "result", "subtype": "success", "usage": result_usage()}),
        ]
    )

    assert [(item.command, item.exit_code) for item in evidence.commands] == [("false", 1)]
    assert any("tool result" in error.lower() for error in evidence.errors)
    assert evidence.unknown_events == []
