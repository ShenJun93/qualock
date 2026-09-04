import json
from collections.abc import Iterable
from typing import Any

from .models import AgentEvidence, AgentEvidenceError, CommandEvent


class ClaudeEvidenceError(AgentEvidenceError):
    pass


def _tool_uses(event: dict[str, Any]) -> Iterable[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return ()
    content = message.get("content")
    if not isinstance(content, list):
        return ()
    return (
        item
        for item in content
        if isinstance(item, dict) and item.get("type") == "tool_use"
    )


def _record_tool_use(evidence: AgentEvidence, item: dict[str, Any]) -> None:
    name = item.get("name")
    tool_input = item.get("input")
    if not isinstance(name, str):
        return
    payload = tool_input if isinstance(tool_input, dict) else {}

    if name == "Bash":
        command = payload.get("command")
        if isinstance(command, str):
            evidence.commands.append(CommandEvent(command=command))
    elif name in {"Edit", "Write"}:
        path = payload.get("file_path") or payload.get("path")
        if isinstance(path, str):
            evidence.file_changes.append(path)

    if name in {"WebSearch", "WebFetch"}:
        evidence.web_searches += 1
    if name.startswith("mcp__"):
        evidence.mcp_calls += 1


def _usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _record_result(evidence: AgentEvidence, event: dict[str, Any]) -> None:
    usage = event.get("usage")
    if isinstance(usage, dict):
        evidence.input_tokens = _usage_value(usage, "input_tokens")
        evidence.cached_input_tokens = _usage_value(usage, "cache_read_input_tokens")
        evidence.output_tokens = _usage_value(usage, "output_tokens")

    subtype = event.get("subtype")
    is_error = event.get("is_error") is True
    if isinstance(subtype, str) and subtype != "success":
        evidence.errors.append(f"Claude result: {subtype}")
    elif is_error:
        evidence.errors.append("Claude result reported an error")

    permission_denials = event.get("permission_denials")
    if isinstance(permission_denials, list) and permission_denials:
        evidence.errors.append("Claude permission denial detected")


def parse_claude_stream_json(lines: Iterable[str]) -> AgentEvidence:
    evidence = AgentEvidence()
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
            for item in _tool_uses(event):
                _record_tool_use(evidence, item)
        elif event_type == "result":
            _record_result(evidence, event)
        else:
            evidence.unknown_events.append(event)

    return evidence
