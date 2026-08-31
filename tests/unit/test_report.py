from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Usage,
    Verdict,
)
from qualock.report.render import render_json, render_markdown, render_terminal


def sample_result() -> QualificationResult:
    baseline_attempts = tuple(
        AttemptResult(side="baseline", repetition=i, success=True, valid=True, duration_ms=1000, usage=Usage(input_tokens=10, output_tokens=2))
        for i in range(1, 4)
    )
    candidate_attempts = tuple(
        AttemptResult(side="candidate", repetition=i, success=False, valid=True, duration_ms=900, usage=Usage(input_tokens=11, output_tokens=2))
        for i in range(1, 4)
    )
    execution = CanaryExecution(
        canary_id="critical-bug",
        critical=True,
        prepared_image_digest="sha256:prepared",
        attempts=baseline_attempts + candidate_attempts,
        baseline_successes=3,
        candidate_successes=0,
        baseline_valid=3,
        candidate_valid=3,
        verdict=Verdict.BLOCK,
        reason="critical canary regressed from full pass to zero passes",
    )
    return QualificationResult(
        qualification_id="q1",
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        verdict=Verdict.BLOCK,
        executions=(execution,),
        reasons=(execution.reason,),
        run_order=(("critical-bug", "baseline", 1), ("critical-bug", "candidate", 1)),
    )


def test_markdown_report_shows_raw_counts_and_no_magic_score() -> None:
    text = render_markdown(sample_result())
    assert "0.150.0" in text and "0.151.0" in text
    assert "3/3" in text and "0/3" in text
    assert "BLOCK" in text
    assert "Score" not in text


def test_json_report_preserves_verdict_and_versions() -> None:
    payload = render_json(sample_result())
    assert payload["verdict"] == "block"
    assert payload["baseline_version"] == "0.150.0"
    assert payload["candidate_version"] == "0.151.0"
    assert payload["executions"][0]["candidate_successes"] == 0


def test_terminal_report_contains_independent_quality_verdict() -> None:
    text = render_terminal(sample_result())
    assert "Quality" in text
    assert "BLOCK" in text
