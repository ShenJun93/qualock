from dataclasses import asdict

from qualock.evidence.models import AgentEvidence
from qualock.qualification.models import AttemptResult, QualificationResult, Usage, Verdict


def test_usage_total_excludes_input_and_output_token_subsets() -> None:
    usage = Usage(
        input_tokens=100,
        cached_input_tokens=40,
        cache_write_input_tokens=15,
        output_tokens=30,
        reasoning_output_tokens=20,
        observed=True,
    )

    assert usage.total_tokens == 130
    assert asdict(usage) == {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": 15,
        "output_tokens": 30,
        "reasoning_output_tokens": 20,
        "observed": True,
    }
    assert "total_tokens" not in asdict(usage)


def test_accounting_models_default_to_unobserved_and_unlimited() -> None:
    attempt = AttemptResult(
        side="baseline",
        repetition=1,
        success=True,
        valid=True,
        duration_ms=1,
    )
    result = QualificationResult(
        qualification_id="q1",
        baseline_version="1.0.0",
        candidate_version="1.1.0",
        verdict=Verdict.PASS,
        executions=(),
        reasons=(),
        run_order=(),
    )

    assert Usage().observed is False
    assert attempt.usage.observed is False
    assert AgentEvidence().usage_observed is False
    assert result.max_attempts is None
    assert result.max_tokens is None
    assert result.attempts_used == 0
    assert result.observed_tokens is None
