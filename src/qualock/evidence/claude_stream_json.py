import json
import re
from collections.abc import Iterable
from typing import Any

from .models import AgentEvidence, AgentEvidenceError, CommandEvent


class ClaudeEvidenceError(AgentEvidenceError):
    pass


_EXIT_CODE_RE = re.compile(r"(?:^|\n)Exit code (\d+)(?:\n|$)")


def _message_content(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _record_tool_use(evidence: AgentEvidence, item: dict[str, Any]) -> int | None:
    name = item.get("name")
    tool_input = item.get("input")
    if not isinstance(name, str):
        return None
    payload = tool_input if isinstance(tool_input, dict) else {}
    command_index: int | None = None

    if name == "Bash":
        command = payload.get("command")
        if isinstance(command, str):
            evidence.commands.append(CommandEvent(command=command))
            command_index = len(evidence.commands) - 1
    elif name in {"Edit", "Write"}:
        path = payload.get("file_path") or payload.get("path")
        if isinstance(path, str):
            evidence.file_changes.append(path)

    if name in {"WebSearch", "WebFetch"}:
        evidence.web_searches += 1
    if name.startswith("mcp__"):
        evidence.mcp_calls += 1
    return command_index


def _required_usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ClaudeEvidenceError(f"Claude result usage {key} must be an integer")
    return value


def _record_result(evidence: AgentEvidence, event: dict[str, Any]) -> None:
    subtype = event.get("subtype")
    if not isinstance(subtype, str):
        raise ClaudeEvidenceError("Claude result subtype must be a string")

    usage = event.get("usage")
    if not isinstance(usage, dict):
        raise ClaudeEvidenceError("Claude result usage must be an object")
    evidence.input_tokens = _required_usage_value(usage, "input_tokens")
    evidence.cached_input_tokens = _required_usage_value(usage, "cache_read_input_tokens")
    evidence.output_tokens = _required_usage_value(usage, "output_tokens")

    if subtype != "success":
        evidence.errors.append(f"Claude result: {subtype}")
    elif event.get("is_error") is True:
        evidence.errors.append("Claude result reported an error")

    permission_denials = event.get("permission_denials")
    if permission_denials is not None and not isinstance(permission_denials, list):
        raise ClaudeEvidenceError("Claude result permission_denials must be a list")
    if permission_denials:
        evidence.errors.append("Claude permission denial detected")


def _record_tool_result(
    evidence: AgentEvidence,
    item: dict[str, Any],
    command_indexes: dict[str, int],
) -> None:
    tool_use_id = item.get("tool_use_id")
    if isinstance(tool_use_id, str):
        command_index = command_indexes.get(tool_use_id)
        if command_index is not None:
            content = item.get("content")
            match = _EXIT_CODE_RE.search(content) if isinstance(content, str) else None
            if match is not None:
                command = evidence.commands[command_index]
                evidence.commands[command_index] = CommandEvent(
                    command=command.command,
                    exit_code=int(match.group(1)),
                )


def parse_claude_stream_json(lines: Iterable[str]) -> AgentEvidence:
    evidence = AgentEvidence()
    saw_result = False
    command_indexes: dict[str, int] = {}
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClaudeEvidenceError(f"invalid JSONL at line {line_no}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise ClaudeEvidenceError(f"invalid JSONL at line {line_no}: expected object")

        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            session_id = event.get("session_id")
            if isinstance(session_id, str):
                evidence.thread_id = session_id
        elif event_type == "assistant":
            for item in _message_content(event):
                if item.get("type") != "tool_use":
                    continue
                command_index = _record_tool_use(evidence, item)
                tool_use_id = item.get("id")
                if command_index is not None and isinstance(tool_use_id, str):
                    command_indexes[tool_use_id] = command_index
        elif event_type == "user":
            tool_results = [
                item
                for item in _message_content(event)
                if item.get("type") == "tool_result"
            ]
            if tool_results:
                for item in tool_results:
                    _record_tool_result(evidence, item, command_indexes)
            else:
                evidence.unknown_events.append(event)
        elif event_type == "result":
            if saw_result:
                raise ClaudeEvidenceError("duplicate Claude result event")
            saw_result = True
            _record_result(evidence, event)
        else:
            evidence.unknown_events.append(event)

    if not saw_result:
        raise ClaudeEvidenceError("missing Claude result event")
    return evidence
