import pytest
from pydantic import ValidationError

from qualock.github_pr.models import (
    PrCanarySummary,
    PrClassification,
    PrReasonCode,
    PrReportVerdict,
    PullRequestContext,
    PullRequestReport,
)
from qualock.qualification.models import Verdict


def valid_context(*, agent: str | None = None) -> PullRequestContext:
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
        agent=agent,
    )


def valid_report(*, agent: str | None = None) -> PullRequestReport:
    return PullRequestReport(
        repository_id=123,
        repository_full_name="owner/repo",
        pr_number=17,
        base_sha="a" * 40,
        head_sha="b" * 40,
        producer_run_id=999,
        classification=PrClassification.UPGRADE,
        baseline_version="0.151.0",
        candidate_version="0.152.0",
        qualification_id="check-1",
        qualock_version="0.1.1",
        verdict=PrReportVerdict.PASS,
        agent=agent,
    )


def test_context_is_strict_and_frozen() -> None:
    context = valid_context()
    assert context.schema_version == 2
    assert context.classification is PrClassification.UPGRADE
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**context.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**context.model_dump(), "head_sha": "short"})
    with pytest.raises(ValidationError):
        context.pr_number = 99  # type: ignore[misc]


def test_context_rejects_invalid_repository_name_and_negative_ids() -> None:
    base = valid_context().model_dump()
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "repository_full_name": "no-slash"})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "repository_id": -1})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "pr_number": 0})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "producer_run_id": -5})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "base_sha": "not-hex-" + "a" * 32})


def test_context_rejects_more_than_3000_changed_paths() -> None:
    base = valid_context().model_dump()
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate(
            {**base, "changed_paths": tuple(f"path-{i}" for i in range(3001))}
        )


def test_context_accepts_exactly_3000_changed_paths() -> None:
    base = valid_context().model_dump()
    context = PullRequestContext.model_validate(
        {**base, "changed_paths": tuple(f"path-{i}" for i in range(3000))}
    )
    assert len(context.changed_paths) == 3000


def test_context_rejects_changed_path_longer_than_4096_characters() -> None:
    base = valid_context().model_dump()
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "changed_paths": ("a" * 4097,)})


def test_context_accepts_changed_path_exactly_4096_characters() -> None:
    base = valid_context().model_dump()
    context = PullRequestContext.model_validate({**base, "changed_paths": ("a" * 4096,)})
    assert len(context.changed_paths[0]) == 4096


def test_canary_summary_rejects_negative_counts_and_is_frozen() -> None:
    summary = PrCanarySummary(
        canary_id="canary-1",
        baseline_successes=3,
        baseline_valid=3,
        candidate_successes=3,
        candidate_valid=3,
        verdict=Verdict.PASS,
    )
    assert summary.verdict is Verdict.PASS
    with pytest.raises(ValidationError):
        summary.baseline_successes = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PrCanarySummary.model_validate(
            {**summary.model_dump(), "baseline_successes": -1}
        )


def test_report_is_strict_and_frozen_and_carries_no_free_form_reason() -> None:
    report = valid_report()
    assert report.schema_version == 2
    assert report.verdict is PrReportVerdict.PASS
    assert report.canaries == ()
    assert report.reason_codes == ()
    with pytest.raises(ValidationError):
        PullRequestReport.model_validate({**report.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        report.verdict = PrReportVerdict.BLOCK  # type: ignore[misc]
    assert "reason" not in PullRequestReport.model_fields


def test_report_reason_codes_are_bounded_enum_values() -> None:
    report = PullRequestReport(
        **{
            **valid_report().model_dump(exclude={"verdict", "reason_codes"}),
            "verdict": PrReportVerdict.BLOCK,
        },
        reason_codes=(PrReasonCode.CRITICAL_REGRESSION,),
    )
    assert report.reason_codes == (PrReasonCode.CRITICAL_REGRESSION,)


def test_reason_code_includes_unsupported_agent() -> None:
    assert PrReasonCode.UNSUPPORTED_AGENT.value == "unsupported_agent"


@pytest.mark.parametrize("agent", ["codex", "claude", None])
def test_context_schema_v2_accepts_bounded_agent_values(agent: str | None) -> None:
    context = valid_context(agent=agent)
    assert context.schema_version == 2
    assert context.agent == agent
    reloaded = PullRequestContext.model_validate_json(context.model_dump_json())
    assert reloaded.agent == agent
    assert reloaded.schema_version == 2


@pytest.mark.parametrize("agent", ["codex", "claude", None])
def test_report_schema_v2_accepts_bounded_agent_values(agent: str | None) -> None:
    report = valid_report(agent=agent)
    assert report.schema_version == 2
    assert report.agent == agent
    reloaded = PullRequestReport.model_validate_json(report.model_dump_json())
    assert reloaded.agent == agent
    assert reloaded.schema_version == 2


def test_context_rejects_schema_version_1_json() -> None:
    payload = valid_context().model_dump_json()
    stale = payload.replace('"schema_version":2', '"schema_version":1')
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate_json(stale)


def test_report_rejects_schema_version_1_json() -> None:
    payload = valid_report().model_dump_json()
    stale = payload.replace('"schema_version":2', '"schema_version":1')
    with pytest.raises(ValidationError):
        PullRequestReport.model_validate_json(stale)


def test_context_rejects_invalid_agent_value() -> None:
    base = valid_context().model_dump()
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**base, "agent": "antigravity"})


def test_report_rejects_invalid_agent_value() -> None:
    base = valid_report().model_dump()
    with pytest.raises(ValidationError):
        PullRequestReport.model_validate({**base, "agent": "antigravity"})
