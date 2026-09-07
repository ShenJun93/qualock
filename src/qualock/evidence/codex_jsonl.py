import json
from collections.abc import Iterable
from typing import TypeGuard

from .models import AgentEvidence, AgentEvidenceError, CommandEvent


class CodexEvidenceError(AgentEvidenceError):
    pass

def parse_codex_jsonl(lines: Iterable[str]) -> AgentEvidence:
    evidence = AgentEvidence()
    completed_turns = 0
    usage_trustworthy = True
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
            completed_turns += 1
            usage = event.get("usage")
            if isinstance(usage, dict):
                if not _trusted_required_total(
                    usage.get("input_tokens")
                ) or not _trusted_required_total(usage.get("output_tokens")):
                    usage_trustworthy = False
                evidence.input_tokens += _int_value(usage.get("input_tokens"))
                evidence.cached_input_tokens += _optional_detail_value(
                    usage.get("cached_input_tokens")
                )
                evidence.output_tokens += _int_value(usage.get("output_tokens"))
                evidence.reasoning_output_tokens += _optional_detail_value(
                    usage.get("reasoning_output_tokens")
                )
            else:
                usage_trustworthy = False
        elif event_type in {"error", "turn.failed"}:
            message = event.get("message") or event.get("error")
            evidence.errors.append(str(message))
        elif event_type in {"turn.started"}:
            pass
        else:
            evidence.unknown_events.append(event)
    evidence.usage_observed = completed_turns > 0 and usage_trustworthy
    return evidence


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _trusted_required_total(value: object) -> TypeGuard[int]:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _optional_detail_value(value: object) -> int:
    return value if _trusted_required_total(value) else 0
