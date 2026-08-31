import json
from dataclasses import dataclass, field
from typing import Any, Iterable


class CodexEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CommandEvent:
    command: str
    exit_code: int | None = None


@dataclass
class CodexEvidence:
    thread_id: str | None = None
    commands: list[CommandEvent] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    web_searches: int = 0
    mcp_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    unknown_events: list[dict[str, Any]] = field(default_factory=list)


def parse_codex_jsonl(lines: Iterable[str]) -> CodexEvidence:
    evidence = CodexEvidence()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexEvidenceError(f"invalid JSONL at line {line_no}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise CodexEvidenceError(f"invalid JSONL at line {line_no}: expected object")

        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id") or event.get("thread", {}).get("id")
            if isinstance(thread_id, str):
                evidence.thread_id = thread_id
        elif event_type in {"item.started", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict):
                evidence.unknown_events.append(event)
                continue
            item_type = item.get("type")
            if item_type in {"command_execution", "command"}:
                command = item.get("command")
                if isinstance(command, str):
                    exit_code = item.get("exit_code")
                    evidence.commands.append(
                        CommandEvent(
                            command=command,
                            exit_code=exit_code if isinstance(exit_code, int) else None,
                        )
                    )
            elif item_type in {"file_change", "file_changes"}:
                path = item.get("path")
                if isinstance(path, str):
                    evidence.file_changes.append(path)
                changes = item.get("changes")
                if isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict) and isinstance(change.get("path"), str):
                            evidence.file_changes.append(change["path"])
            elif item_type in {"web_search", "web_search_call"}:
                evidence.web_searches += 1
            elif item_type in {"mcp_call", "mcp_tool_call"}:
                evidence.mcp_calls += 1
            elif item_type == "error":
                message = item.get("message") or item.get("error")
                evidence.errors.append(str(message))
            elif item_type in {"agent_message", "reasoning", "plan_update"}:
                pass
            else:
                evidence.unknown_events.append(event)
        elif event_type == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                evidence.input_tokens += _int_value(usage.get("input_tokens"))
                evidence.cached_input_tokens += _int_value(usage.get("cached_input_tokens"))
                evidence.output_tokens += _int_value(usage.get("output_tokens"))
                evidence.reasoning_output_tokens += _int_value(
                    usage.get("reasoning_output_tokens")
                )
        elif event_type in {"error", "turn.failed"}:
            message = event.get("message") or event.get("error")
            evidence.errors.append(str(message))
        elif event_type in {"turn.started"}:
            pass
        else:
            evidence.unknown_events.append(event)
    return evidence


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
