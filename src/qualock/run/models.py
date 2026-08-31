from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedImage:
    reference: str
    digest: str


@dataclass(frozen=True)
class AgentStateEvidence:
    changed_paths: tuple[str, ...]
    patch: str


@dataclass(frozen=True)
class FrozenAgentState:
    reference: str
    digest: str
    container_name: str
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_ms: int


@dataclass(frozen=True)
class GradeResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
