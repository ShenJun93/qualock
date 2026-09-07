from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoricalAttempt:
    side: str | None
    repetition: int | None
    success: bool | None
    valid: bool | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    usage_observed: bool = False


@dataclass(frozen=True)
class HistoricalExecution:
    canary_id: str
    attempts: tuple[HistoricalAttempt, ...]


@dataclass(frozen=True)
class LoadedReport:
    qualification_id: str
    qualification_dir: Path
    executions: tuple[HistoricalExecution, ...]


@dataclass(frozen=True)
class ReportLoadFailure:
    qualification_dir: Path
    reason: str


@dataclass(frozen=True)
class HistorySummary:
    loaded: tuple[LoadedReport, ...]
    ignored: tuple[ReportLoadFailure, ...]


@dataclass(frozen=True)
class CanaryEffectiveness:
    canary_id: str
    eligible_samples: int
    detections: int
    detection_rate: float | None


@dataclass(frozen=True)
class CanaryEstimate:
    canary_id: str
    runtime_samples_ms: tuple[int, ...]
    token_samples: tuple[int, ...]
    runtime_median_ms: float | None
    token_median: float | None


@dataclass(frozen=True)
class SuiteEstimate:
    runtime_ms: float | None
    tokens: float | None
    missing_runtime_canaries: tuple[str, ...]
    missing_token_canaries: tuple[str, ...]


@dataclass(frozen=True)
class HistoryAnalysis:
    loaded_reports: int
    ignored_reports: tuple[ReportLoadFailure, ...]
    ranked: tuple[CanaryEffectiveness, ...]
    not_enough_history: tuple[CanaryEffectiveness, ...]
    per_canary_estimates: tuple[CanaryEstimate, ...]
    suite_estimate: SuiteEstimate
