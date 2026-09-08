import json

import pytest

from qualock.evidence.gemini_stream_json import (
    GeminiEvidenceError,
    parse_gemini_stream_json,
)
from qualock.evidence.models import AgentEvidenceError, CommandEvent

# NOTE: All fixtures in this file are synthetic, hand-authored stream-json
# events for unit testing the parser. They are not captured from a real,
# authenticated Gemini CLI run.


def line(payload: object) -> str:
    return json.dumps(payload)


def init(*, session_id: object = "s1", model: object = "gemini-3.5-flash") -> str:
    payload: dict[str, object] = {"type": "init"}
    if session_id is not None:
        payload["session_id"] = session_id
    if model is not None:
        payload["model"] = model
    return line(payload)


def shell_use(tool_id: object = "c1", command: object = "pytest -q") -> str:
    return line(
        {
            "type": "tool_use",
            "tool_name": "run_shell_command",
            "tool_id": tool_id,
            "parameters": {"command": command},
        }
    )


def shell_result(tool_id: object = "c1", status: object = "success", output: object = "") -> str:
    return line(
        {"type": "tool_result", "tool_id": tool_id, "status": status, "output": output}
    )


def result(
    *,
    status: object = "success",
    stats: object = None,
    message: object = None,
) -> str:
    payload: dict[str, object] = {"type": "result", "status": status}
    if status == "success":
        payload["stats"] = (
            stats
            if stats is not None
            else {
                "input_tokens": 12,
                "output_tokens": 5,
                "cached": 3,
                "input": 9,
                "duration_ms": 10,
                "tool_calls": 1,
                "models": {},
            }
        )
    elif stats is not None:
        payload["stats"] = stats
    if message is not None:
        payload["message"] = message
    return line(payload)


def test_parses_shell_marker_and_terminal_usage() -> None:
    evidence = parse_gemini_stream_json(
        [
            line({"type": "init", "session_id": "s1", "model": "gemini-3.5-flash"}),
            line(
                {
                    "type": "tool_use",
                    "tool_name": "run_shell_command",
                    "tool_id": "c1",
                    "parameters": {"command": "pytest -q"},
                }
            ),
            line(
                {
                    "type": "tool_result",
                    "tool_id": "c1",
                    "status": "success",
                    "output": "ok\nQUALOCK_GEMINI_EXIT_CODE=0\n",
                }
            ),
            line(
                {
                    "type": "result",
                    "status": "success",
                    "stats": {
                        "input_tokens": 12,
                        "output_tokens": 5,
                        "cached": 3,
                        "input": 9,
                        "duration_ms": 10,
                        "tool_calls": 1,
                        "models": {},
                    },
                }
            ),
        ]
    )
    assert evidence.thread_id == "s1"
    assert [(x.command, x.exit_code) for x in evidence.commands] == [("pytest -q", 0)]
    assert (evidence.input_tokens, evidence.cached_input_tokens, evidence.output_tokens) == (
        12,
        3,
        5,
    )
    assert evidence.usage_observed is True


@pytest.mark.parametrize("exit_code", [0, 1, 7, 125, 255])
def test_valid_exit_code_markers_are_recorded(exit_code: int) -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            shell_use(),
            shell_result(output=f"ok\nQUALOCK_GEMINI_EXIT_CODE={exit_code}\n"),
            result(),
        ]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=exit_code)]
    assert evidence.errors == []


def test_missing_exit_code_marker_leaves_exit_unknown_and_records_error() -> None:
    evidence = parse_gemini_stream_json(
        [init(), shell_use(), shell_result(output="ok\n"), result()]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]
    assert any("missing" in error for error in evidence.errors)


def test_duplicate_exit_code_marker_leaves_exit_unknown_and_records_error() -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            shell_use(),
            shell_result(
                output="QUALOCK_GEMINI_EXIT_CODE=0\nQUALOCK_GEMINI_EXIT_CODE=1\n"
            ),
            result(),
        ]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]
    assert any("duplicate" in error for error in evidence.errors)


def test_malformed_exit_code_marker_leaves_exit_unknown_and_records_error() -> None:
    evidence = parse_gemini_stream_json(
        [init(), shell_use(), shell_result(output="QUALOCK_GEMINI_EXIT_CODE=abc\n"), result()]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]
    assert any("malformed" in error for error in evidence.errors)


def test_out_of_range_exit_code_marker_leaves_exit_unknown_and_records_error() -> None:
    evidence = parse_gemini_stream_json(
        [init(), shell_use(), shell_result(output="QUALOCK_GEMINI_EXIT_CODE=256\n"), result()]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]
    assert any("out-of-range" in error for error in evidence.errors)


def test_tool_result_status_success_alone_does_not_imply_exit_zero() -> None:
    evidence = parse_gemini_stream_json(
        [init(), shell_use(), shell_result(status="success", output="all good, no marker"), result()]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]


def test_malformed_json_raises_with_line_number() -> None:
    with pytest.raises(GeminiEvidenceError, match="invalid JSONL at line 2"):
        parse_gemini_stream_json([init(), "{"])


def test_non_object_json_line_raises() -> None:
    with pytest.raises(GeminiEvidenceError, match="expected object"):
        parse_gemini_stream_json(["[]"])


def test_missing_terminal_result_raises() -> None:
    with pytest.raises(GeminiEvidenceError, match="missing Gemini result event"):
        parse_gemini_stream_json([init()])


def test_duplicate_terminal_result_raises() -> None:
    terminal = result()
    with pytest.raises(GeminiEvidenceError, match="duplicate Gemini result event"):
        parse_gemini_stream_json([init(), terminal, terminal])


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("input_tokens", True),
        ("output_tokens", "5"),
        ("cached", -1),
        ("input", None),
        ("duration_ms", 1.5),
        ("tool_calls", False),
    ],
)
def test_successful_result_requires_non_negative_integer_stats(
    field: str, bad_value: object
) -> None:
    stats: dict[str, object] = {
        "input_tokens": 12,
        "output_tokens": 5,
        "cached": 3,
        "input": 9,
        "duration_ms": 10,
        "tool_calls": 1,
    }
    stats[field] = bad_value

    with pytest.raises(GeminiEvidenceError, match=field):
        parse_gemini_stream_json([init(), result(stats=stats)])


def test_result_models_must_be_object_when_present() -> None:
    stats = {
        "input_tokens": 12,
        "output_tokens": 5,
        "cached": 3,
        "input": 9,
        "duration_ms": 10,
        "tool_calls": 1,
        "models": "not-an-object",
    }
    with pytest.raises(GeminiEvidenceError, match="models"):
        parse_gemini_stream_json([init(), result(stats=stats)])


def test_reasoning_output_tokens_sums_valid_model_thoughts_once() -> None:
    stats = {
        "input_tokens": 12,
        "output_tokens": 5,
        "cached": 3,
        "input": 9,
        "duration_ms": 10,
        "tool_calls": 2,
        "models": {
            "gemini-3.5-flash": {"tokens": {"thoughts": 4}},
            "gemini-3.5-pro": {"tokens": {"thoughts": 6}},
        },
    }
    evidence = parse_gemini_stream_json([init(), result(stats=stats)])

    assert evidence.reasoning_output_tokens == 10


def test_reasoning_output_tokens_skips_structurally_invalid_model_entries() -> None:
    stats = {
        "input_tokens": 12,
        "output_tokens": 5,
        "cached": 3,
        "input": 9,
        "duration_ms": 10,
        "tool_calls": 2,
        "models": {
            "gemini-3.5-flash": {"tokens": {"thoughts": 4}},
            "bad-model-1": "not-an-object",
            "bad-model-2": {"tokens": "not-an-object"},
            "bad-model-3": {"tokens": {"thoughts": -1}},
            "bad-model-4": {"tokens": {"thoughts": True}},
            "bad-model-5": {"tokens": {"thoughts": "4"}},
        },
    }
    evidence = parse_gemini_stream_json([init(), result(stats=stats)])

    assert evidence.reasoning_output_tokens == 4


def test_reasoning_output_tokens_defaults_to_zero_when_models_absent() -> None:
    evidence = parse_gemini_stream_json([init(), result()])

    assert evidence.reasoning_output_tokens == 0


def _write_use(tool_id: str, tool_name: str, file_path: object) -> str:
    return line(
        {
            "type": "tool_use",
            "tool_name": tool_name,
            "tool_id": tool_id,
            "parameters": {"file_path": file_path},
        }
    )


def _write_result(tool_id: str, status: object = "success") -> str:
    return line({"type": "tool_result", "tool_id": tool_id, "status": status, "output": "ok"})


@pytest.mark.parametrize("tool_name", ["write_file", "replace"])
def test_write_file_and_replace_record_safe_relative_paths(tool_name: str) -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            _write_use("f1", tool_name, "src/new.py"),
            _write_result("f1"),
            result(),
        ]
    )

    assert evidence.file_changes == ["src/new.py"]


@pytest.mark.parametrize(
    "unsafe_path",
    ["", "/etc/passwd", "~", "~/secrets", "../escape.py", "a/../b", "a\0b"],
)
def test_unsafe_or_missing_paths_are_not_recorded(unsafe_path: str) -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            _write_use("f1", "write_file", unsafe_path),
            _write_result("f1"),
            result(),
        ]
    )

    assert evidence.file_changes == []


def test_failed_file_change_is_not_recorded() -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            _write_use("f1", "write_file", "src/new.py"),
            _write_result("f1", status="error"),
            result(),
        ]
    )

    assert evidence.file_changes == []


@pytest.mark.parametrize(
    "tool_name",
    ["google_web_search", "web_fetch", "browser_open", "browser_navigate"],
)
def test_web_tools_increment_web_searches(tool_name: str) -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            line(
                {
                    "type": "tool_use",
                    "tool_name": tool_name,
                    "tool_id": "w1",
                    "parameters": {},
                }
            ),
            result(),
        ]
    )

    assert evidence.web_searches == 1


@pytest.mark.parametrize("tool_name", ["mcp_server_tool", "mcp_browser_tool"])
def test_mcp_tools_increment_mcp_calls(tool_name: str) -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            line(
                {
                    "type": "tool_use",
                    "tool_name": tool_name,
                    "tool_id": "m1",
                    "parameters": {},
                }
            ),
            result(),
        ]
    )

    assert evidence.mcp_calls == 1


def test_sandbox_failure_marker_records_error_independent_of_exit_marker() -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            shell_use(),
            shell_result(
                output="QUALOCK_GEMINI_EXIT_CODE=0\nQUALOCK_GEMINI_SHELL_SANDBOX_FAILURE\n"
            ),
            result(),
        ]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=0)]
    assert any("sandbox failure" in error for error in evidence.errors)


def test_sandbox_failure_marker_recorded_even_when_exit_marker_missing() -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            shell_use(),
            shell_result(output="QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE\n"),
            result(),
        ]
    )

    assert evidence.commands == [CommandEvent(command="pytest -q", exit_code=None)]
    assert any("missing" in error for error in evidence.errors)
    assert any("sandbox failure" in error for error in evidence.errors)


def test_terminal_error_status_records_typed_error_without_inventing_usage() -> None:
    evidence = parse_gemini_stream_json(
        [init(), result(status="error", message="provider unavailable")]
    )

    assert evidence.usage_observed is False
    assert evidence.input_tokens == 0
    assert evidence.output_tokens == 0
    assert any("provider unavailable" in error for error in evidence.errors)


def test_unsupported_terminal_status_raises() -> None:
    with pytest.raises(GeminiEvidenceError, match="unsupported Gemini result status"):
        parse_gemini_stream_json([init(), result(status="cancelled", stats={})])


def test_message_event_is_known_and_ignored() -> None:
    evidence = parse_gemini_stream_json(
        [
            init(),
            line({"type": "message", "role": "assistant", "content": "hello"}),
            result(),
        ]
    )

    assert evidence.unknown_events == []


def test_unknown_additive_event_types_are_preserved() -> None:
    unknown_event = {"type": "future_event", "detail": "additive"}
    evidence = parse_gemini_stream_json([init(), line(unknown_event), result()])

    assert evidence.unknown_events == [unknown_event]


def test_duplicate_init_event_raises() -> None:
    with pytest.raises(GeminiEvidenceError, match="duplicate Gemini init event"):
        parse_gemini_stream_json([init(), init(), result()])


def test_init_requires_non_empty_session_id() -> None:
    with pytest.raises(GeminiEvidenceError, match="session_id"):
        parse_gemini_stream_json([init(session_id=None), result()])


def test_shell_command_requires_non_empty_command_parameter() -> None:
    with pytest.raises(GeminiEvidenceError, match="command"):
        parse_gemini_stream_json([init(), shell_use(command=""), result()])


def test_error_type_is_generic_agent_evidence_error() -> None:
    assert issubclass(GeminiEvidenceError, AgentEvidenceError)
