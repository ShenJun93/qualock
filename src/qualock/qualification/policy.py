from collections.abc import Sequence

from .models import CanaryAggregate, CanaryComparison, QualificationVerdict, Verdict


def qualify_canary(
    canary_id: str,
    baseline: CanaryAggregate,
    candidate: CanaryAggregate,
    *,
    critical: bool,
) -> CanaryComparison:
    if baseline.valid_runs < baseline.expected_runs or candidate.valid_runs < candidate.expected_runs:
        return CanaryComparison(
            canary_id=canary_id,
            baseline=baseline,
            candidate=candidate,
            critical=critical,
            verdict=Verdict.INCOMPLETE,
            reason="INCOMPLETE: insufficient valid attempts",
            baseline_stable=False,
        )

    baseline_stable = baseline.successes == baseline.expected_runs
    if not baseline_stable:
        return CanaryComparison(
            canary_id=canary_id,
            baseline=baseline,
            candidate=candidate,
            critical=critical,
            verdict=Verdict.WARN,
            reason=(
                f"UNSTABLE baseline: {baseline.successes}/{baseline.valid_runs}; "
                "candidate cannot be auto-blocked"
            ),
            baseline_stable=False,
        )

    if candidate.successes == candidate.expected_runs:
        verdict = Verdict.PASS
        reason = "candidate matches stable baseline"
    elif critical and candidate.successes == 0:
        verdict = Verdict.BLOCK
        reason = "critical canary regressed from full pass to zero passes"
    else:
        verdict = Verdict.WARN
        reason = "candidate quality regressed; confirmation required"

    return CanaryComparison(
        canary_id=canary_id,
        baseline=baseline,
        candidate=candidate,
        critical=critical,
        verdict=verdict,
        reason=reason,
        baseline_stable=True,
    )


def qualify_suite(comparisons: Sequence[CanaryComparison]) -> QualificationVerdict:
    ordered = tuple(comparisons)
    if any(item.verdict is Verdict.INCOMPLETE for item in ordered):
        verdict = Verdict.INCOMPLETE
    elif any(item.verdict is Verdict.BLOCK for item in ordered):
        verdict = Verdict.BLOCK
    elif any(item.verdict is Verdict.WARN for item in ordered):
        verdict = Verdict.WARN
    else:
        verdict = Verdict.PASS
    reasons = tuple(item.reason for item in ordered if item.verdict is not Verdict.PASS)
    return QualificationVerdict(verdict=verdict, comparisons=ordered, reasons=reasons)
