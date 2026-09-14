from dataclasses import dataclass

from qualock.qualification.models import AttemptResult


@dataclass(frozen=True)
class PreparedTarget:
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


@dataclass(frozen=True)
class AttemptControlProfiles:
    preparation_sha256: str | None
    isolation_sha256: str | None
    resource_sha256: str | None
    runtime_sha256: str | None


@dataclass(frozen=True)
class AttemptControlContext:
    profiles: AttemptControlProfiles
    isolation_instance_sha256: str | None


@dataclass(frozen=True)
class AttemptExecution:
    result: AttemptResult
    context: AttemptControlContext


@dataclass(frozen=True)
class AttemptRunTrace:
    canary_id: str
    side: str
    repetition: int
    trace_design_sha256: str | None
    started_offset_ms: int
    finished_offset_ms: int
    events_sha256: str
    context: AttemptControlContext
