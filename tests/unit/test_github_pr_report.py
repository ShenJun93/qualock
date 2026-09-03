from pathlib import Path

import pytest

from qualock.github_pr.models import (
    PrClassification,
    PrReasonCode,
    PrReportVerdict,
    PullRequestContext,
)
from qualock.github_pr.report import (
    PrArtifactError,
    incomplete_report,
    not_applicable_report,
    read_context,
    read_report,
    report_from_qualification,
    write_context,
    write_report,
)
from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Verdict,
)


def valid_context() -> PullRequestContext:
    return PullRequestContext(
        repository_id=123,
        repository_full_name="owner/repo",
        pr_number=17,
        pr_author_login="alice",
        base_sha="a" * 40,
        head_sha="b" * 40,
        producer_run_id=999,
        changed_paths=(".qualock/baseline.lock",),
        classification=PrClassification.UPGRADE,
    )


def leaky_result() -> QualificationResult:
    sentinel_events = (
        '{"authorization": "Bearer GITHUB_TOKEN", '
        '"task body": "do the thing", '
        '"transcript": "SECRET_TRANSCRIPT"}'
    )
    attempt = AttemptResult(
        side="candidate",
        repetition=1,
        success=False,
        valid=True,
        duration_ms=1000,
        events_jsonl=sentinel_events,
    )
    execution = CanaryExecution(
        canary_id="canary-1",
        critical=True,
        prepared_image_digest="sha256:" + "0" * 64,
        attempts=(attempt,),
        baseline_successes=3,
        candidate_successes=0,
        baseline_valid=3,
        candidate_valid=3,
        verdict=Verdict.BLOCK,
        reason="candidate regressed: " + sentinel_events,
    )
    return QualificationResult(
        qualification_id="check-1",
        baseline_version="0.151.0",
        candidate_version="0.152.0",
        verdict=Verdict.BLOCK,
        executions=(execution,),
        reasons=("critical regression: " + sentinel_events,),
        run_order=(("canary-1", "candidate", 1),),
    )


def test_report_from_qualification_never_leaks_raw_execution_data() -> None:
    report = report_from_qualification(valid_context(), leaky_result())
    encoded = report.model_dump_json()
    for forbidden in (
        "events_jsonl",
        "SECRET_TRANSCRIPT",
        "authorization",
        "GITHUB_TOKEN",
        "task body",
    ):
        assert forbidden not in encoded
    assert report.verdict is PrReportVerdict.BLOCK
    assert report.canaries[0].canary_id == "canary-1"
    assert report.reason_codes == (PrReasonCode.CRITICAL_REGRESSION,)


def test_report_from_qualification_dedupes_reason_codes_in_first_seen_order() -> None:
    context = valid_context()
    result = leaky_result()
    second = CanaryExecution(
        canary_id="canary-2",
        critical=False,
        prepared_image_digest="sha256:" + "1" * 64,
        attempts=(),
        baseline_successes=3,
        candidate_successes=0,
        baseline_valid=3,
        candidate_valid=3,
        verdict=Verdict.BLOCK,
        reason="also blocked",
    )
    result = QualificationResult(
        qualification_id=result.qualification_id,
        baseline_version=result.baseline_version,
        candidate_version=result.candidate_version,
        verdict=result.verdict,
        executions=(*result.executions, second),
        reasons=result.reasons,
        run_order=result.run_order,
    )
    report = report_from_qualification(context, result)
    assert report.reason_codes == (PrReasonCode.CRITICAL_REGRESSION,)


def test_not_applicable_report_has_no_canaries_or_versions() -> None:
    report = not_applicable_report(valid_context())
    assert report.classification is PrClassification.NOT_APPLICABLE
    assert report.verdict is PrReportVerdict.NOT_APPLICABLE
    assert report.canaries == ()
    assert report.baseline_version is None
    assert report.qualification_completed is False


def test_incomplete_report_carries_reason_codes_and_credential_flag() -> None:
    report = incomplete_report(
        valid_context(),
        reason_codes=(PrReasonCode.CREDENTIAL_UNAVAILABLE,),
        credential_unavailable=True,
    )
    assert report.verdict is PrReportVerdict.INCOMPLETE
    assert report.reason_codes == (PrReasonCode.CREDENTIAL_UNAVAILABLE,)
    assert report.credential_unavailable is True


def test_write_and_read_context_round_trip_atomically(tmp_path: Path) -> None:
    path = tmp_path / "pr-context.json"
    context = valid_context()
    write_context(path, context)
    assert path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
    loaded = read_context(path)
    assert loaded == context


def test_write_and_read_report_round_trip_atomically(tmp_path: Path) -> None:
    path = tmp_path / "pr-report.json"
    report = report_from_qualification(valid_context(), leaky_result())
    write_report(path, report)
    assert path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
    loaded = read_report(path)
    assert loaded == report


def test_read_context_rejects_oversized_artifact(tmp_path: Path) -> None:
    path = tmp_path / "pr-context.json"
    path.write_text("x" * 200_000, encoding="utf-8")
    with pytest.raises(PrArtifactError):
        read_context(path, max_bytes=131_072)


def test_read_report_rejects_oversized_artifact(tmp_path: Path) -> None:
    path = tmp_path / "pr-report.json"
    path.write_text("x" * 300_000, encoding="utf-8")
    with pytest.raises(PrArtifactError):
        read_report(path, max_bytes=262_144)


def test_read_context_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PrArtifactError):
        read_context(tmp_path / "missing.json")


def test_read_report_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "pr-report.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(PrArtifactError):
        read_report(path)
