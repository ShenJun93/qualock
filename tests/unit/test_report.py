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
        AttemptResult(
            side="baseline",
            repetition=i,
            success=True,
            valid=True,
            duration_ms=1000,
            usage=Usage(input_tokens=10, output_tokens=2, observed=True),
        )
        for i in range(1, 4)
    )
    candidate_attempts = tuple(
        AttemptResult(
            side="candidate",
            repetition=i,
            success=False,
            valid=True,
            duration_ms=900,
            usage=Usage(input_tokens=11, output_tokens=2, observed=True),
        )
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
    text = render_markdown(sample_result(), agent_display_name="Codex")
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
    text = render_terminal(sample_result(), agent_display_name="Codex")
    assert "Quality" in text
    assert "BLOCK" in text


def test_easy_terminal_report_leads_with_safety_and_evidence_path() -> None:
    from qualock.report.render import render_safety_terminal
    from qualock.report.safety import build_safety_summary

    summary = build_safety_summary(
        sample_result(),
        {"critical-bug": "Login and checkout"},
        agent_display_name="Codex",
    )

    text = render_safety_terminal(summary, ".qualock/results/q1/")

    assert "QuaLock Safety Check" in text
    assert "DON'T UPDATE YET" in text
    assert "Login and checkout" in text
    assert "Keep using Codex 0.150.0" in text
    assert "Technical evidence: .qualock/results/q1/" in text
    assert "3/3" in text and "0/3" in text


def test_easy_terminal_report_type_hints_resolve() -> None:
    from typing import get_type_hints

    from qualock.report.render import render_safety_terminal
    from qualock.report.safety import SafetySummary

    hints = get_type_hints(render_safety_terminal)

    assert hints["summary"] is SafetySummary


def test_technical_reports_use_injected_agent_display_name() -> None:
    markdown = render_markdown(sample_result(), agent_display_name="Claude Code")
    terminal = render_terminal(sample_result(), agent_display_name="Claude Code")

    assert "**Claude Code:** `0.150.0` → `0.151.0`" in markdown
    assert "Qualock qualification: Claude Code 0.150.0 -> 0.151.0" in terminal
    assert "Codex" not in markdown
    assert "Codex" not in terminal


def budgeted_result() -> QualificationResult:
    source = sample_result()
    return source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=source.verdict,
        executions=source.executions,
        reasons=source.reasons,
        run_order=source.run_order,
        max_attempts=8,
        max_tokens=50_000,
        attempts_used=6,
        observed_tokens=57_231,
    )


def test_render_usage_line_reports_observed_tokens_without_threshold() -> None:
    from qualock.report.render import render_usage_line

    source = sample_result()
    result = source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=source.verdict,
        executions=source.executions,
        reasons=source.reasons,
        run_order=source.run_order,
        attempts_used=6,
        observed_tokens=57_231,
    )

    assert render_usage_line(result) == "Observed model tokens: 57,231"


def test_render_usage_line_reports_observed_tokens_with_threshold() -> None:
    from qualock.report.render import render_usage_line

    assert render_usage_line(budgeted_result()) == (
        "Observed model tokens: 57,231 (threshold 50,000; "
        "checked between complete canaries)"
    )


def test_render_usage_line_reports_unavailable_without_threshold() -> None:
    from qualock.report.render import render_usage_line

    assert render_usage_line(sample_result()) == "Observed model tokens: unavailable"


def test_render_usage_line_reports_unavailable_with_threshold() -> None:
    from qualock.report.render import render_usage_line

    source = sample_result()
    result = source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=source.verdict,
        executions=source.executions,
        reasons=source.reasons,
        run_order=source.run_order,
        max_tokens=50_000,
    )

    assert render_usage_line(result) == (
        "Observed model tokens: unavailable (threshold 50,000; "
        "checked between complete canaries)"
    )


def test_render_safety_terminal_exact_output_is_unchanged_without_usage_line() -> None:
    from qualock.report.render import render_safety_terminal
    from qualock.report.safety import build_safety_summary

    summary = build_safety_summary(
        sample_result(),
        {"critical-bug": "Login and checkout"},
        agent_display_name="Codex",
    )

    expected = (
        "QuaLock Safety Check\n\n"
        "DON'T UPDATE YET\n\n"
        "At least one critical protected workflow regressed.\n\n"
        "Codex 0.150.0 -> 0.151.0\n\n"
        "Protected workflows\n"
        "- REGRESSED: Login and checkout  3/3 -> 0/3\n\n"
        "Recommendation:\n"
        "Keep using Codex 0.150.0 for now. "
        "Do not update to Codex 0.151.0 until the regression is understood.\n\n"
        "Technical evidence: .qualock/results/q1/\n"
    )

    assert render_safety_terminal(summary, ".qualock/results/q1/") == expected
    assert (
        render_safety_terminal(summary, ".qualock/results/q1/", usage_line=None)
        == expected
    )


def test_render_safety_terminal_inserts_usage_line_once_before_evidence() -> None:
    from qualock.report.render import render_safety_terminal
    from qualock.report.safety import build_safety_summary

    summary = build_safety_summary(
        sample_result(),
        {"critical-bug": "Login and checkout"},
        agent_display_name="Codex",
    )
    usage_line = "Observed model tokens: 57,231"

    text = render_safety_terminal(
        summary, ".qualock/results/q1/", usage_line=usage_line
    )

    assert f"{usage_line}\n\nTechnical evidence:" in text
    assert text.count(usage_line) == 1


def test_technical_terminal_and_markdown_report_accounting() -> None:
    result = budgeted_result()

    markdown = render_markdown(result, agent_display_name="Codex")
    terminal = render_terminal(result, agent_display_name="Codex")

    assert "Attempts used: 6" in markdown
    assert "Max attempts: 8" in markdown
    assert "Observed model tokens: 57,231 (threshold 50,000; " in markdown
    assert "Attempts used: 6" in terminal
    assert "Max attempts: 8" in terminal
    assert "Observed model tokens: 57,231 (threshold 50,000; " in terminal
    assert "Max attempts: 8 (threshold" not in markdown
    assert "Max attempts: 8 (threshold" not in terminal


def test_default_low_tech_usage_line_has_no_cache_or_reasoning_breakdown() -> None:
    from qualock.report.render import render_usage_line

    text = render_usage_line(budgeted_result())

    assert "cache" not in text.lower()
    assert "reasoning" not in text.lower()
