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
