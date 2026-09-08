import json
import re
from collections.abc import Iterable
from typing import Any

from .models import AgentEvidence, AgentEvidenceError, CommandEvent


class GeminiEvidenceError(AgentEvidenceError):
    pass


_KNOWN_EVENT_TYPES = {"init", "message", "tool_use", "tool_result", "error", "result"}
_FILE_CHANGE_TOOLS = {"write_file", "replace"}
_WEB_TOOLS = {"google_web_search", "web_fetch"}
_REQUIRED_STATS_INT_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached",
    "input",
    "duration_ms",
    "tool_calls",
)
_EXIT_CODE_RE = re.compile(r"QUALOCK_GEMINI_EXIT_CODE=([0-9]{1,3})")
_SANDBOX_FAILURE_MARKER = "QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE"


def _required_non_empty_string(event: dict[str, Any], key: str, *, context: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value:
        raise GeminiEvidenceError(f"Gemini {context} must be a non-empty string")
    return value


def _required_object(event: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = event.get(key)
    if not isinstance(value, dict):
        raise GeminiEvidenceError(f"Gemini {context} must be an object")
    return value


def _required_stats_int(stats: dict[str, Any], key: str) -> int:
    value = stats.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GeminiEvidenceError(f"Gemini result stats {key} must be a non-negative integer")
    return value


def _sum_reasoning_tokens(stats: dict[str, Any]) -> int:
    if "models" not in stats:
        return 0
    models = stats["models"]
    if not isinstance(models, dict):
        raise GeminiEvidenceError("Gemini result stats models must be an object")
    total = 0
    for model_stats in models.values():
        if not isinstance(model_stats, dict):
            continue
        tokens = model_stats.get("tokens")
        if not isinstance(tokens, dict):
            continue
        thoughts = tokens.get("thoughts")
        if isinstance(thoughts, int) and not isinstance(thoughts, bool) and thoughts >= 0:
            total += thoughts
    return total


def _is_safe_relative_path(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if "\0" in path:
        return False
    if path.startswith("/"):
        return False
    if path == "~" or path.startswith("~/"):
        return False
    return not any(segment == ".." for segment in path.split("/"))


def _is_web_tool(tool_name: str) -> bool:
    return tool_name in _WEB_TOOLS or tool_name.startswith("browser_")


def _is_mcp_tool(tool_name: str) -> bool:
    return tool_name.startswith("mcp_")


def _parse_exit_code_marker(
    output: str, evidence: AgentEvidence, tool_id: str
) -> int | None:
    candidates = [
        candidate_line
        for candidate_line in output.splitlines()
        if candidate_line.startswith("QUALOCK_GEMINI_EXIT_CODE=")
    ]
    if not candidates:
        evidence.errors.append(f"missing QUALOCK_GEMINI_EXIT_CODE marker for tool_id {tool_id}")
        return None
    if len(candidates) > 1:
        evidence.errors.append(f"duplicate QUALOCK_GEMINI_EXIT_CODE marker for tool_id {tool_id}")
        return None
    match = _EXIT_CODE_RE.fullmatch(candidates[0])
    if match is None:
        evidence.errors.append(f"malformed QUALOCK_GEMINI_EXIT_CODE marker for tool_id {tool_id}")
        return None
    value = int(match.group(1))
    if not (0 <= value <= 255):
        evidence.errors.append(
            f"out-of-range QUALOCK_GEMINI_EXIT_CODE marker for tool_id {tool_id}"
        )
        return None
    return value


def _record_tool_use(
    event: dict[str, Any],
    evidence: AgentEvidence,
    pending_tools: dict[str, tuple[str, dict[str, Any]]],
    command_indexes: dict[str, int],
) -> None:
    tool_name = _required_non_empty_string(event, "tool_name", context="tool_use tool_name")
    tool_id = _required_non_empty_string(event, "tool_id", context="tool_use tool_id")
    parameters = event.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    pending_tools[tool_id] = (tool_name, parameters)

    if tool_name == "run_shell_command":
        command = parameters.get("command")
        if not isinstance(command, str) or not command:
            raise GeminiEvidenceError(
                "Gemini run_shell_command parameters must include a non-empty command"
            )
        evidence.commands.append(CommandEvent(command=command))
        command_indexes[tool_id] = len(evidence.commands) - 1
    elif _is_web_tool(tool_name):
        evidence.web_searches += 1
    elif _is_mcp_tool(tool_name):
        evidence.mcp_calls += 1


def _record_tool_result(
    event: dict[str, Any],
    evidence: AgentEvidence,
    pending_tools: dict[str, tuple[str, dict[str, Any]]],
    command_indexes: dict[str, int],
) -> None:
    tool_id = _required_non_empty_string(event, "tool_id", context="tool_result tool_id")
    status = _required_non_empty_string(event, "status", context="tool_result status")
    pending = pending_tools.get(tool_id)

    if tool_id in command_indexes:
        output = event.get("output")
        output_text = output if isinstance(output, str) else ""
        command_index = command_indexes[tool_id]
        exit_code = _parse_exit_code_marker(output_text, evidence, tool_id)
        if exit_code is not None:
            command = evidence.commands[command_index]
            evidence.commands[command_index] = CommandEvent(
                command=command.command, exit_code=exit_code
            )
        if _SANDBOX_FAILURE_MARKER in output_text:
            evidence.errors.append(f"Gemini shell sandbox failure for tool_id {tool_id}")
        return

    if pending is None:
        return
    tool_name, parameters = pending
    if tool_name in _FILE_CHANGE_TOOLS and status == "success":
        path = parameters.get("file_path")
        if isinstance(path, str) and _is_safe_relative_path(path):
            evidence.file_changes.append(path)


def _record_result(evidence: AgentEvidence, event: dict[str, Any]) -> None:
    status = _required_non_empty_string(event, "status", context="result status")
    if status == "success":
        stats = _required_object(event, "stats", context="result stats")
        for key in _REQUIRED_STATS_INT_KEYS:
            _required_stats_int(stats, key)
        evidence.input_tokens = stats["input_tokens"]
        evidence.output_tokens = stats["output_tokens"]
        evidence.cached_input_tokens = stats["cached"]
        evidence.cache_write_input_tokens = 0
        evidence.reasoning_output_tokens = _sum_reasoning_tokens(stats)
        evidence.usage_observed = True
    elif status == "error":
        message = event.get("message")
        text = message if isinstance(message, str) and message else "unspecified error"
        evidence.errors.append(f"Gemini terminal error: {text}")
    else:
        raise GeminiEvidenceError(f"unsupported Gemini result status {status!r}")


def parse_gemini_stream_json(lines: Iterable[str]) -> AgentEvidence:
    evidence = AgentEvidence()
    init_seen = False
    saw_result = False
    pending_tools: dict[str, tuple[str, dict[str, Any]]] = {}
    command_indexes: dict[str, int] = {}

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GeminiEvidenceError(f"invalid JSONL at line {line_no}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise GeminiEvidenceError(f"invalid JSONL at line {line_no}: expected object")

        event_type = event.get("type")
        if event_type == "init":
            if init_seen:
                raise GeminiEvidenceError("duplicate Gemini init event")
            init_seen = True
            session_id = _required_non_empty_string(event, "session_id", context="init session_id")
            evidence.thread_id = session_id
        elif event_type == "message":
            continue
        elif event_type == "tool_use":
            _record_tool_use(event, evidence, pending_tools, command_indexes)
        elif event_type == "tool_result":
            _record_tool_result(event, evidence, pending_tools, command_indexes)
        elif event_type == "error":
            message = event.get("message")
            text = message if isinstance(message, str) and message else "unspecified error"
            evidence.errors.append(f"Gemini error: {text}")
        elif event_type == "result":
            if saw_result:
                raise GeminiEvidenceError("duplicate Gemini result event")
            saw_result = True
            _record_result(evidence, event)
        else:
            evidence.unknown_events.append(event)

    if not saw_result:
        raise GeminiEvidenceError("missing Gemini result event")
    return evidence
