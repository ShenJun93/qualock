import json
from collections.abc import Iterable
from typing import Any

from .models import AgentEvidence, AgentEvidenceError, CommandEvent


class AntigravityEvidenceError(AgentEvidenceError):
    pass


_COMMAND_TOOLS = {"run_command"}
_FILE_CHANGE_TOOLS = {
    "multi_replace_file_content",
    "replace_file_content",
    "write_to_file",
}
_KNOWN_READ_TOOLS = {
    "find_by_name",
    "grep_search",
    "list_dir",
    "view_file",
}
_WEB_TOOLS = {
    "browser_subagent",
    "fetch_url",
    "invoke_subagent",
    "read_url_content",
    "search_web",
    "web_fetch",
    "web_search",
}
_FILE_PATH_KEYS = ("TargetFile", "FilePath", "file_path", "path")
_TOOL_STATES = {"ACTIVE", "DONE", "ERROR"}


def _required_object(container: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise AntigravityEvidenceError(f"Antigravity {context} must be an object")
    return value


def _required_non_empty_string(
    container: dict[str, Any], key: str, *, context: str
) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise AntigravityEvidenceError(f"Antigravity {context} must be a non-empty string")
    return value


def _required_usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AntigravityEvidenceError(
            f"Antigravity result usage {key} must be a non-negative integer"
        )
    return value


def _is_mcp_tool(tool_name: str) -> bool:
    normalized = tool_name.lower()
    return normalized == "mcp" or normalized.startswith("mcp_") or "__mcp__" in normalized


def _is_web_or_subagent_tool(tool_name: str) -> bool:
    normalized = tool_name.lower()
    family_names = set(normalized.split("_"))
    return normalized in _WEB_TOOLS or bool(
        family_names & {"browser", "subagent", "url", "web"}
    )


def _file_change_path(parameters: dict[str, Any], tool_name: str) -> str:
    for key in _FILE_PATH_KEYS:
        path = parameters.get(key)
        if isinstance(path, str) and path:
            return path
    raise AntigravityEvidenceError(
        f"Antigravity {tool_name} parameters must include a non-empty file path"
    )


def _record_tool_invocation(
    evidence: AgentEvidence,
    tool_name: str,
    parameters: dict[str, Any],
) -> str | None:
    normalized = tool_name.lower()
    if normalized in _COMMAND_TOOLS:
        command = parameters.get("CommandLine")
        if not isinstance(command, str) or not command:
            raise AntigravityEvidenceError(
                "Antigravity run_command parameters must include a non-empty CommandLine"
            )
        evidence.commands.append(CommandEvent(command=command))
    elif normalized in _FILE_CHANGE_TOOLS:
        return _file_change_path(parameters, tool_name)
    elif _is_mcp_tool(normalized):
        evidence.mcp_calls += 1
    elif _is_web_or_subagent_tool(normalized):
        evidence.web_searches += 1
    elif normalized not in _KNOWN_READ_TOOLS:
        raise AntigravityEvidenceError(f"unsupported Antigravity tool {tool_name!r}")
    return None


def _tool_error_detail(error: Any) -> str:
    if isinstance(error, str) and error:
        return error
    # agy 1.1.27 reports tool failures as {"type": ..., "message": ...} objects.
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "unspecified error"


def _record_tool_error(evidence: AgentEvidence, tool_name: str, tool_info: dict[str, Any]) -> None:
    evidence.errors.append(
        f"Antigravity tool {tool_name}: {_tool_error_detail(tool_info.get('error'))}"
    )


def _record_step_update(
    evidence: AgentEvidence,
    event: dict[str, Any],
    active_steps: dict[int, tuple[str, str | None]],
    seen_step_indexes: set[int],
) -> None:
    update = _required_object(event, "step_update", context="step_update payload")
    step_type = update.get("step_type")
    if step_type != "tool":
        return

    step_index = update.get("step_index")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
        raise AntigravityEvidenceError(
            "Antigravity tool step_update step_index must be a non-negative integer"
        )
    state = _required_non_empty_string(update, "state", context="tool state")
    if state not in _TOOL_STATES:
        raise AntigravityEvidenceError(f"unsupported Antigravity tool state {state!r}")

    tool_name = _required_non_empty_string(update, "tool_name", context="tool_name")
    tool_info = _required_object(update, "tool_info", context="tool_info")
    info_name = _required_non_empty_string(tool_info, "name", context="tool_info name")
    if info_name != tool_name:
        raise AntigravityEvidenceError(
            "Antigravity step_update tool_name does not match tool_info name"
        )
    parameters = _required_object(tool_info, "parameters", context="tool parameters")

    if state == "ACTIVE":
        if step_index not in seen_step_indexes:
            pending_file_path = _record_tool_invocation(evidence, tool_name, parameters)
            active_steps[step_index] = (tool_name, pending_file_path)
            seen_step_indexes.add(step_index)
        return

    active_step = active_steps.pop(step_index, None)
    if active_step is None:
        if state == "ERROR":
            _record_tool_error(evidence, tool_name, tool_info)
        return
    active_tool_name, pending_file_path = active_step
    if active_tool_name != tool_name:
        raise AntigravityEvidenceError(
            f"Antigravity tool step {step_index} changed tool name"
        )
    if state == "ERROR":
        _record_tool_error(evidence, tool_name, tool_info)
    elif pending_file_path is not None:
        evidence.file_changes.append(pending_file_path)


def _record_result(
    evidence: AgentEvidence,
    event: dict[str, Any],
) -> None:
    result = _required_object(event, "result", context="result payload")
    status = _required_non_empty_string(result, "status", context="result status")
    if status != "SUCCESS":
        raise AntigravityEvidenceError(f"Antigravity terminal status {status}")

    denied_actions = result.get("denied_actions", [])
    if not isinstance(denied_actions, list):
        raise AntigravityEvidenceError("Antigravity result denied_actions must be a list")
    if denied_actions:
        raise AntigravityEvidenceError("Antigravity result contains denied actions")

    usage = _required_object(result, "usage", context="result usage")
    evidence.input_tokens = _required_usage_value(usage, "input_tokens")
    evidence.output_tokens = _required_usage_value(usage, "output_tokens")
    evidence.reasoning_output_tokens = _required_usage_value(usage, "thinking_tokens")
    evidence.cached_input_tokens = _required_usage_value(usage, "cache_read_tokens")
    if "total_tokens" in usage:
        _required_usage_value(usage, "total_tokens")
    evidence.cache_write_input_tokens = 0
    evidence.usage_observed = True


def parse_antigravity_stream_json(lines: Iterable[str]) -> AgentEvidence:
    evidence = AgentEvidence()
    init_conversation_id: str | None = None
    saw_result = False
    active_steps: dict[int, tuple[str, str | None]] = {}
    seen_step_indexes: set[int] = set()

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AntigravityEvidenceError(
                f"invalid JSONL at line {line_no}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise AntigravityEvidenceError(
                f"invalid JSONL at line {line_no}: expected object"
            )

        event_type = event.get("event")
        if event_type == "init":
            if init_conversation_id is not None:
                raise AntigravityEvidenceError("duplicate Antigravity init event")
            init_payload = _required_object(event, "init", context="init payload")
            permission_mode = init_payload.get("permission_mode")
            if permission_mode != "proceed-in-sandbox":
                raise AntigravityEvidenceError(
                    "Antigravity init permission_mode must be proceed-in-sandbox"
                )
            init_conversation_id = _required_non_empty_string(
                event, "conversation_id", context="init conversation_id"
            )
            evidence.thread_id = init_conversation_id
        elif event_type == "step_update":
            _record_step_update(evidence, event, active_steps, seen_step_indexes)
        elif event_type == "result":
            if saw_result:
                raise AntigravityEvidenceError("duplicate Antigravity result event")
            if init_conversation_id is None:
                raise AntigravityEvidenceError("missing Antigravity init event before result")
            saw_result = True
            _record_result(evidence, event)
        else:
            evidence.unknown_events.append(event)

    if not saw_result:
        raise AntigravityEvidenceError("missing Antigravity result event")
    if init_conversation_id is None:
        raise AntigravityEvidenceError("missing Antigravity init event")
    return evidence
