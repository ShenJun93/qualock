import uuid
from pathlib import Path

from pydantic import BaseModel, ValidationError

import qualock
from qualock.github_pr.models import (
    PrCanarySummary,
    PrClassification,
    PrReasonCode,
    PrReportVerdict,
    PullRequestContext,
    PullRequestReport,
)
from qualock.qualification.models import CanaryExecution, QualificationResult, Verdict

DEFAULT_CONTEXT_MAX_BYTES = 131_072
DEFAULT_REPORT_MAX_BYTES = 262_144


class PrArtifactError(Exception):
    """Raised when a PR context or report artifact cannot be trusted as-is."""


def _atomic_write(path: Path, payload: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise PrArtifactError(f"cannot stat artifact {path}: {error}") from error
    if size > max_bytes:
        raise PrArtifactError(f"artifact {path} exceeds {max_bytes} byte limit")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise PrArtifactError(f"cannot read artifact {path}: {error}") from error


def write_context(path: Path, context: PullRequestContext) -> None:
    _atomic_write(path, context)


def read_context(
    path: Path, *, max_bytes: int = DEFAULT_CONTEXT_MAX_BYTES
) -> PullRequestContext:
    text = _read_bounded_text(path, max_bytes=max_bytes)
    try:
        return PullRequestContext.model_validate_json(text)
    except ValidationError as error:
        raise PrArtifactError(f"invalid PR context artifact {path}: {error}") from error


def write_report(path: Path, report: PullRequestReport) -> None:
    _atomic_write(path, report)


def read_report(
    path: Path, *, max_bytes: int = DEFAULT_REPORT_MAX_BYTES
) -> PullRequestReport:
    text = _read_bounded_text(path, max_bytes=max_bytes)
    try:
        return PullRequestReport.model_validate_json(text)
    except ValidationError as error:
        raise PrArtifactError(f"invalid PR report artifact {path}: {error}") from error


def _reason_code(execution: CanaryExecution) -> PrReasonCode | None:
    if execution.verdict is Verdict.INCOMPLETE:
        return PrReasonCode.INSUFFICIENT_VALID_ATTEMPTS
    if execution.verdict is Verdict.BLOCK:
        return PrReasonCode.CRITICAL_REGRESSION
    if execution.verdict is Verdict.WARN:
        if execution.baseline_successes < execution.baseline_valid:
            return PrReasonCode.UNSTABLE_BASELINE
        return PrReasonCode.QUALITY_REGRESSION
    return None


def report_from_qualification(
    context: PullRequestContext, result: QualificationResult
) -> PullRequestReport:
    canaries = tuple(
        PrCanarySummary(
            canary_id=execution.canary_id,
            baseline_successes=execution.baseline_successes,
            baseline_valid=execution.baseline_valid,
            candidate_successes=execution.candidate_successes,
            candidate_valid=execution.candidate_valid,
            verdict=execution.verdict,
        )
        for execution in result.executions
    )
    reason_codes: list[PrReasonCode] = []
    for execution in result.executions:
        code = _reason_code(execution)
        if code is not None and code not in reason_codes:
            reason_codes.append(code)
    return PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=context.classification,
        baseline_version=result.baseline_version,
        candidate_version=result.candidate_version,
        qualification_id=result.qualification_id,
        qualock_version=qualock.__version__,
        verdict=PrReportVerdict(result.verdict.value),
        canaries=canaries,
        reason_codes=tuple(reason_codes),
        credential_unavailable=False,
        qualification_completed=True,
    )


def not_applicable_report(context: PullRequestContext) -> PullRequestReport:
    return PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=PrClassification.NOT_APPLICABLE,
        qualock_version=qualock.__version__,
        verdict=PrReportVerdict.NOT_APPLICABLE,
        credential_unavailable=False,
        qualification_completed=False,
    )


def incomplete_report(
    context: PullRequestContext,
    *,
    reason_codes: tuple[PrReasonCode, ...] = (),
    credential_unavailable: bool = False,
) -> PullRequestReport:
    return PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=context.classification,
        qualock_version=qualock.__version__,
        verdict=PrReportVerdict.INCOMPLETE,
        reason_codes=reason_codes,
        credential_unavailable=credential_unavailable,
        qualification_completed=False,
    )
