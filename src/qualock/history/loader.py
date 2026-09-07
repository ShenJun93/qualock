import json
from pathlib import Path

from qualock.history.models import (
    HistoricalAttempt,
    HistoricalExecution,
    HistorySummary,
    LoadedReport,
    ReportLoadFailure,
)

_FAILURE_UNREADABLE = "unreadable file"
_FAILURE_INVALID_JSON = "invalid JSON"
_FAILURE_NOT_OBJECT = "not a JSON object"
_FAILURE_ID = "missing or invalid qualification_id"
_FAILURE_EXECUTIONS = "missing or invalid executions list"
_FAILURE_EXECUTION = "malformed execution entry"
_FAILURE_DUPLICATE = "duplicate qualification_id"


def _normalize_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _normalize_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _normalize_attempt(raw: object) -> HistoricalAttempt:
    if not isinstance(raw, dict):
        return HistoricalAttempt(
            side=None,
            repetition=None,
            success=None,
            valid=None,
            duration_ms=None,
            input_tokens=None,
            output_tokens=None,
            usage_observed=False,
            cached_input_tokens=None,
            cache_write_input_tokens=None,
            reasoning_output_tokens=None,
        )

    usage = raw.get("usage")
    if isinstance(usage, dict):
        usage_observed = usage.get("observed") is True
        input_tokens = _normalize_int(usage.get("input_tokens"))
        output_tokens = _normalize_int(usage.get("output_tokens"))
        cached_input_tokens = _normalize_int(usage.get("cached_input_tokens"))
        cache_write_input_tokens = _normalize_int(usage.get("cache_write_input_tokens"))
        reasoning_output_tokens = _normalize_int(usage.get("reasoning_output_tokens"))
    else:
        usage_observed = False
        input_tokens = None
        output_tokens = None
        cached_input_tokens = None
        cache_write_input_tokens = None
        reasoning_output_tokens = None

    return HistoricalAttempt(
        side=_normalize_str(raw.get("side")),
        repetition=_normalize_int(raw.get("repetition")),
        success=_normalize_bool(raw.get("success")),
        valid=_normalize_bool(raw.get("valid")),
        duration_ms=_normalize_int(raw.get("duration_ms")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_observed=usage_observed,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def _parse_executions(raw_executions: list[object]) -> tuple[HistoricalExecution, ...] | None:
    executions: list[HistoricalExecution] = []
    for raw_execution in raw_executions:
        if not isinstance(raw_execution, dict):
            return None
        canary_id = raw_execution.get("canary_id")
        raw_attempts = raw_execution.get("attempts")
        if not isinstance(canary_id, str) or not canary_id or not isinstance(raw_attempts, list):
            return None
        attempts = tuple(_normalize_attempt(raw_attempt) for raw_attempt in raw_attempts)
        executions.append(HistoricalExecution(canary_id=canary_id, attempts=attempts))
    return tuple(executions)


def scan_results(results_dir: Path) -> HistorySummary:
    if not results_dir.exists():
        return HistorySummary(loaded=(), ignored=())

    loaded: list[LoadedReport] = []
    ignored: list[ReportLoadFailure] = []
    accepted_ids: set[str] = set()

    for qualification_dir in sorted(results_dir.iterdir(), key=lambda path: path.name):
        if not qualification_dir.is_dir():
            continue
        report_path = qualification_dir / "report.json"
        if not report_path.is_file():
            continue

        try:
            text = report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_UNREADABLE))
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_INVALID_JSON))
            continue

        if not isinstance(payload, dict):
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_NOT_OBJECT))
            continue

        if (
            set(payload.keys()) == {"kind", "result"}
            and isinstance(payload.get("kind"), str)
            and isinstance(payload.get("result"), dict)
        ):
            continue

        qualification_id = payload.get("qualification_id")
        if not isinstance(qualification_id, str) or not qualification_id:
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_ID))
            continue

        raw_executions = payload.get("executions")
        if not isinstance(raw_executions, list):
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_EXECUTIONS))
            continue

        executions = _parse_executions(raw_executions)
        if executions is None:
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_EXECUTION))
            continue

        if qualification_id in accepted_ids:
            ignored.append(ReportLoadFailure(qualification_dir, _FAILURE_DUPLICATE))
            continue

        accepted_ids.add(qualification_id)
        loaded.append(
            LoadedReport(
                qualification_id=qualification_id,
                qualification_dir=qualification_dir,
                executions=executions,
            )
        )

    return HistorySummary(loaded=tuple(loaded), ignored=tuple(ignored))
