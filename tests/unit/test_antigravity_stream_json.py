import json

import pytest

from qualock.evidence.antigravity_stream_json import (
    AntigravityEvidenceError,
    parse_antigravity_stream_json,
)
from qualock.evidence.models import AgentEvidenceError, CommandEvent


def line(payload: object) -> str:
    return json.dumps(payload)


def init(*, conversation_id: object = "c1", permission_mode: object = "proceed-in-sandbox") -> str:
    return line(
        {
            "event": "init",
            "conversation_id": conversation_id,
            "init": {"permission_mode": permission_mode},
        }
    )


def result(
    *,
    conversation_id: object = "c1",
    status: object = "SUCCESS",
    denied_actions: object = None,
    usage: object = None,
) -> str:
    payload: dict[str, object] = {
        "conversation_id": conversation_id,
        "status": status,
        "usage": usage
        if usage is not None
        else {
            "input_tokens": 10,
            "output_tokens": 2,
            "thinking_tokens": 1,
            "cache_read_tokens": 3,
            "total_tokens": 12,
        },
    }
    if denied_actions is not None:
        payload["denied_actions"] = denied_actions
    return line({"event": "result", "result": payload})


def tool_update(
    step_index: object,
    state: object,
    tool_name: object,
    parameters: object,
    **tool_info_fields: object,
) -> str:
    tool_info: dict[str, object] = {
        "name": tool_name,
        "parameters": parameters,
        **tool_info_fields,
    }
    return line(
        {
            "event": "step_update",
            "step_update": {
                "step_index": step_index,
                "state": state,
                "step_type": "tool",
                "tool_name": tool_name,
                "tool_info": tool_info,
            },
        }
    )


def failed_shell_done_fixture() -> list[str]:
    return [
        init(),
        tool_update(1, "ACTIVE", "run_command", {"CommandLine": "exit 2"}),
        tool_update(
            1,
            "DONE",
            "run_command",
            {"CommandLine": "exit 2"},
            output="Command exited with code 2",
        ),
        line(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "assistant",
                    "content": "The command exited with status 2.",
                },
            }
        ),
        result(),
    ]


def denied_search_web_fixture() -> list[str]:
    parameters = {"query": "example"}
    return [
        init(),
        tool_update(3, "ACTIVE", "search_web", parameters),
        tool_update(3, "ERROR", "search_web", parameters, error="blocked by hook"),
        result(denied_actions=[]),
    ]


def test_parses_success_usage_and_tool_invocations() -> None:
    evidence = parse_antigravity_stream_json(
        [
            '{"event":"init","conversation_id":"c1","init":{"permission_mode":"proceed-in-sandbox"}}',
            '{"event":"step_update","step_update":{"step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"python3 probe.py"}}}}',
            '{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"python3 probe.py"},"output":"ok\\r\\n"}}}',
            '{"event":"result","result":{"conversation_id":"c1","status":"SUCCESS","usage":{"input_tokens":10,"output_tokens":2,"thinking_tokens":1,"cache_read_tokens":3,"total_tokens":12}}}',
        ]
    )

    assert evidence.thread_id == "c1"
    assert evidence.commands == [CommandEvent(command="python3 probe.py", exit_code=None)]
    assert evidence.input_tokens == 10
    assert evidence.cached_input_tokens == 3
    assert evidence.output_tokens == 2
    assert evidence.reasoning_output_tokens == 1


def test_does_not_infer_shell_exit_code_from_done_state_or_assistant_prose() -> None:
    # Real 1.1.27 can emit tool state DONE for a shell process that exited 2.
    evidence = parse_antigravity_stream_json(failed_shell_done_fixture())

    assert evidence.commands[0].exit_code is None


def test_counts_forbidden_tool_attempt_once() -> None:
    evidence = parse_antigravity_stream_json(denied_search_web_fixture())

    assert evidence.web_searches == 1


def test_missing_or_duplicate_terminal_result_fails_closed() -> None:
    with pytest.raises(AntigravityEvidenceError, match="missing Antigravity result event"):
        parse_antigravity_stream_json([])

    terminal = result()
    with pytest.raises(AntigravityEvidenceError, match="duplicate Antigravity result event"):
        parse_antigravity_stream_json([init(), terminal, terminal])


def test_malformed_or_non_object_json_fails_closed() -> None:
    with pytest.raises(AntigravityEvidenceError, match="invalid JSONL at line 2"):
        parse_antigravity_stream_json([init(), "{"])

    with pytest.raises(AntigravityEvidenceError, match="expected object"):
        parse_antigravity_stream_json(["[]"])


def test_requires_exactly_one_init_event() -> None:
    with pytest.raises(AntigravityEvidenceError, match="missing Antigravity init event"):
        parse_antigravity_stream_json([result()])

    with pytest.raises(AntigravityEvidenceError, match="duplicate Antigravity init event"):
        parse_antigravity_stream_json([init(), init(), result()])


@pytest.mark.parametrize("permission_mode", ["ask", "auto", None, 1])
def test_requires_proceed_in_sandbox_permission_mode(permission_mode: object) -> None:
    with pytest.raises(AntigravityEvidenceError, match="proceed-in-sandbox"):
        parse_antigravity_stream_json(
            [init(permission_mode=permission_mode), result()]
        )


def test_terminal_error_fails_closed() -> None:
    with pytest.raises(AntigravityEvidenceError, match="terminal status ERROR"):
        parse_antigravity_stream_json([init(), result(status="ERROR")])


def test_non_empty_denied_actions_fails_closed() -> None:
    with pytest.raises(AntigravityEvidenceError, match="denied actions"):
        parse_antigravity_stream_json(
            [init(), result(denied_actions=[{"tool_name": "search_web"}])]
        )


def test_denied_actions_must_be_a_list_when_present() -> None:
    with pytest.raises(AntigravityEvidenceError, match="denied_actions"):
        parse_antigravity_stream_json([init(), result(denied_actions="none")])


@pytest.mark.parametrize(
    ("tool_name", "expected_web_searches", "expected_mcp_calls"),
    [
        ("search_web", 1, 0),
        ("read_url_content", 1, 0),
        ("browser_subagent", 1, 0),
        ("invoke_subagent", 1, 0),
        ("mcp_server_tool", 0, 1),
    ],
)
def test_counts_network_capable_tool_families(
    tool_name: str,
    expected_web_searches: int,
    expected_mcp_calls: int,
) -> None:
    evidence = parse_antigravity_stream_json(
        [
            init(),
            tool_update(1, "ACTIVE", tool_name, {}),
            tool_update(1, "DONE", tool_name, {}, output="done"),
            result(),
        ]
    )

    assert evidence.web_searches == expected_web_searches
    assert evidence.mcp_calls == expected_mcp_calls


def test_records_successful_file_change_paths_once() -> None:
    invocations = [
        (1, "write_to_file", {"TargetFile": "src/new.py"}),
        (2, "replace_file_content", {"TargetFile": "src/existing.py"}),
        (3, "multi_replace_file_content", {"TargetFile": "README.md"}),
    ]
    updates = [init()]
    for step_index, tool_name, parameters in invocations:
        updates.extend(
            [
                tool_update(step_index, "ACTIVE", tool_name, parameters),
                tool_update(step_index, "DONE", tool_name, parameters, output="ok"),
            ]
        )
    updates.append(result())

    evidence = parse_antigravity_stream_json(updates)

    assert evidence.file_changes == ["src/new.py", "src/existing.py", "README.md"]


def test_first_active_event_for_step_index_wins() -> None:
    evidence = parse_antigravity_stream_json(
        [
            init(),
            tool_update(4, "ACTIVE", "run_command", {"CommandLine": "first"}),
            tool_update(4, "ACTIVE", "run_command", {"CommandLine": "duplicate"}),
            tool_update(4, "DONE", "run_command", {"CommandLine": "changed"}),
            result(),
        ]
    )

    assert evidence.commands == [CommandEvent(command="first", exit_code=None)]


def test_init_and_result_require_valid_matching_conversation_ids() -> None:
    with pytest.raises(AntigravityEvidenceError, match="conversation_id"):
        parse_antigravity_stream_json([init(conversation_id=None), result()])

    with pytest.raises(AntigravityEvidenceError, match="conversation_id"):
        parse_antigravity_stream_json([init(), result(conversation_id="other")])


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("input_tokens", True),
        ("output_tokens", "2"),
        ("thinking_tokens", None),
        ("cache_read_tokens", 1.5),
        ("total_tokens", -1),
    ],
)
def test_result_usage_requires_non_negative_integer_fields(
    field: str, bad_value: object
) -> None:
    usage: dict[str, object] = {
        "input_tokens": 10,
        "output_tokens": 2,
        "thinking_tokens": 1,
        "cache_read_tokens": 3,
        "total_tokens": 12,
    }
    usage[field] = bad_value

    with pytest.raises(AntigravityEvidenceError, match=field):
        parse_antigravity_stream_json([init(), result(usage=usage)])


def test_malformed_critical_event_shapes_fail_closed() -> None:
    with pytest.raises(AntigravityEvidenceError, match="init payload"):
        parse_antigravity_stream_json(
            [line({"event": "init", "conversation_id": "c1", "init": []}), result()]
        )

    with pytest.raises(AntigravityEvidenceError, match="step_update payload"):
        parse_antigravity_stream_json(
            [init(), line({"event": "step_update", "step_update": []}), result()]
        )

    with pytest.raises(AntigravityEvidenceError, match="result payload"):
        parse_antigravity_stream_json(
            [init(), line({"event": "result", "result": []})]
        )


def test_error_type_is_generic_agent_evidence_error() -> None:
    assert issubclass(AntigravityEvidenceError, AgentEvidenceError)
