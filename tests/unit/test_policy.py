import pytest

from qualock.qualification.models import CanaryAggregate, Verdict
from qualock.qualification.policy import qualify_canary, qualify_suite


def agg(successes: int, *, valid: int = 3, expected: int = 3) -> CanaryAggregate:
    return CanaryAggregate(valid_runs=valid, successes=successes, expected_runs=expected)


@pytest.mark.parametrize(
    ("candidate_successes", "expected"),
    [(3, Verdict.PASS), (2, Verdict.WARN), (1, Verdict.WARN), (0, Verdict.BLOCK)],
)
def test_critical_three_of_three_baseline_policy(candidate_successes: int, expected: Verdict) -> None:
    result = qualify_canary("critical", agg(3), agg(candidate_successes), critical=True)
    assert result.verdict is expected


def test_unstable_contemporaneous_baseline_cannot_auto_block() -> None:
    result = qualify_canary("critical", agg(2), agg(0), critical=True)
    assert result.verdict is Verdict.WARN
    assert result.baseline_stable is False
    assert "UNSTABLE" in result.reason


def test_insufficient_valid_attempts_is_incomplete() -> None:
    result = qualify_canary("critical", agg(2, valid=2), agg(3), critical=True)
    assert result.verdict is Verdict.INCOMPLETE


def test_noncritical_regression_warns_instead_of_blocks() -> None:
    result = qualify_canary("optional", agg(3), agg(0), critical=False)
    assert result.verdict is Verdict.WARN


def test_suite_prioritizes_incomplete_over_block() -> None:
    blocked = qualify_canary("a", agg(3), agg(0), critical=True)
    incomplete = qualify_canary("b", agg(2, valid=2), agg(3), critical=True)
    assert qualify_suite([blocked, incomplete]).verdict is Verdict.INCOMPLETE


def test_suite_blocks_when_any_critical_canary_blocks() -> None:
    passed = qualify_canary("a", agg(3), agg(3), critical=True)
    blocked = qualify_canary("b", agg(3), agg(0), critical=True)
    assert qualify_suite([passed, blocked]).verdict is Verdict.BLOCK
