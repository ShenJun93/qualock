from dataclasses import dataclass, field
from typing import Any


class AgentEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CommandEvent:
    command: str
    exit_code: int | None = None


@dataclass
class AgentEvidence:
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
