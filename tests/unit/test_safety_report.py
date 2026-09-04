from dataclasses import replace

import pytest

from qualock.qualification.models import Verdict
from qualock.report.safety import SafetyStatus, build_safety_summary
from tests.unit.test_report import sample_result


@pytest.mark.parametrize(
    ("verdict", "status", "headline", "recommendation_fragment"),
    [
        (Verdict.PASS, SafetyStatus.SAFE, "SAFE TO UPDATE", "looks safe"),
        (Verdict.WARN, SafetyStatus.CAUTION, "REVIEW BEFORE UPDATING", "Review the workflows marked REVIEW"),
        (Verdict.BLOCK, SafetyStatus.DONT_UPDATE, "DON'T UPDATE YET", "Keep using Codex"),
        (Verdict.INCOMPLETE, SafetyStatus.INCOMPLETE, "CHECK COULD NOT FINISH", "Run the check again"),
    ],
)
def test_build_safety_summary_maps_suite_verdict_to_plain_english(
    verdict: Verdict,
    status: SafetyStatus,
    headline: str,
    recommendation_fragment: str,
) -> None:
    source = sample_result()
    execution = replace(source.executions[0], verdict=verdict)
    result = replace(source, verdict=verdict, executions=(execution,))

    summary = build_safety_summary(
        result,
        {"critical-bug": "Login and checkout"},
        agent_display_name="Codex",
    )

    assert summary.status is status
    assert summary.headline == headline
    assert recommendation_fragment in summary.recommendation
    assert summary.baseline_version == "0.150.0"
    assert summary.candidate_version == "0.151.0"
    assert summary.workflows[0].name == "Login and checkout"
    assert summary.workflows[0].verdict is verdict


def test_build_safety_summary_falls_back_to_canary_id() -> None:
    source = sample_result()
    summary = build_safety_summary(source, {}, agent_display_name="Codex")

    assert summary.workflows[0].name == "critical-bug"


def test_safety_summary_uses_injected_agent_display_name() -> None:
    summary = build_safety_summary(
        sample_result(),
        {},
        agent_display_name="Claude Code",
    )

    assert summary.agent_display_name == "Claude Code"
    assert "Keep using Claude Code 0.150.0" in summary.recommendation
    assert "Codex" not in summary.recommendation
