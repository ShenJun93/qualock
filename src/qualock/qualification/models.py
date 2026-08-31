from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0


@dataclass(frozen=True)
class AttemptResult:
    side: str
    repetition: int
    success: bool
    valid: bool
    duration_ms: int
    usage: Usage = field(default_factory=Usage)
    invalid_reason: str | None = None
    events_jsonl: str = ""
    protected_path_violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanaryAggregate:
    valid_runs: int
    successes: int
    expected_runs: int = 3

    @property
    def pass_rate(self) -> float:
        return self.successes / self.valid_runs if self.valid_runs else 0.0


@dataclass(frozen=True)
class CanaryComparison:
    canary_id: str
    baseline: CanaryAggregate
    candidate: CanaryAggregate
    critical: bool
    verdict: Verdict
    reason: str
    baseline_stable: bool


@dataclass(frozen=True)
class QualificationVerdict:
    verdict: Verdict
    comparisons: tuple[CanaryComparison, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CanaryExecution:
    canary_id: str
    critical: bool
    prepared_image_digest: str
    attempts: tuple[AttemptResult, ...]
    baseline_successes: int
    candidate_successes: int
    baseline_valid: int
    candidate_valid: int
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class QualificationResult:
    qualification_id: str
    baseline_version: str
    candidate_version: str
    verdict: Verdict
    executions: tuple[CanaryExecution, ...]
    reasons: tuple[str, ...]
    run_order: tuple[tuple[str, str, int], ...]
