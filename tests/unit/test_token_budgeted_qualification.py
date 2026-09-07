from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.executor import QualificationExecutor
from qualock.run.models import PreparedTarget
from qualock.run.schedule import Side, paired_schedule


class TokenAwareBackend:
    """Recording backend that lets tests control per-attempt token usage."""

    def __init__(
        self,
        *,
        default_usage: Usage,
        overrides: dict[tuple[str, str, int], AttemptResult] | None = None,
        failing_candidates: set[str] | None = None,
    ) -> None:
        self.default_usage = default_usage
        self.overrides = overrides or {}
        self.failing_candidates = failing_candidates or set()
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedTarget:
        self.prepared.append(canary.id)
        return PreparedTarget(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedTarget,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        key = (canary.id, side.value, repetition)
        self.calls.append(key)
        if key in self.overrides:
            return self.overrides[key]
        success = side is Side.BASELINE or canary.id not in self.failing_candidates
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=True,
            duration_ms=10,
            usage=self.default_usage,
        )


def make_canary(tmp_path: Path, canary_id: str, *, critical: bool) -> CanarySpec:
    patch = tmp_path / f"{canary_id}.patch"
    patch.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": canary_id,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(patch), "command": ["pytest -q"]},
            "constraints": {"protected_paths": []},
            "critical": critical,
        }
    )


def binaries() -> tuple[AgentBinary, AgentBinary]:
    return (
        AgentBinary("codex", "0.150.0", Path("/baseline"), "sha-baseline"),
        AgentBinary("codex", "0.151.0", Path("/candidate"), "sha-candidate"),
    )


def test_max_tokens_below_first_canary_cost_finishes_whole_first_canary_then_stops(
    tmp_path: Path,
) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-token-below",
        max_tokens=5,
    )

    expected_first_calls = [
        ("first", slot.side.value, slot.repetition)
        for slot in paired_schedule("first", 1, "q-token-below")
    ]
    assert backend.calls == expected_first_calls
    assert backend.prepared == ["first"]

    first, second = result.executions
    assert first.attempts != ()
    assert first.verdict is Verdict.PASS
    assert second.attempts == ()
    assert second.verdict is Verdict.INCOMPLETE
    assert result.verdict is Verdict.INCOMPLETE
    assert result.attempts_used == 2
    assert result.observed_tokens == 10
    assert result.max_tokens == 5


def test_max_tokens_exact_reach_stops_before_next_canary(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-token-exact",
        max_tokens=10,
    )

    assert result.observed_tokens == 10
    second = result.executions[1]
    assert second.verdict is Verdict.INCOMPLETE
    assert second.reason == (
        "INCOMPLETE: skipped by token budget (max_tokens=10, observed_tokens=10)"
    )


def test_max_tokens_overshoot_stops_before_next_canary(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-token-overshoot",
        max_tokens=7,
    )

    assert result.observed_tokens == 10
    second = result.executions[1]
    assert second.verdict is Verdict.INCOMPLETE
    assert second.reason == (
        "INCOMPLETE: skipped by token budget (max_tokens=7, observed_tokens=10)"
    )


def test_unknown_usage_after_first_canary_stops_every_later_canary(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
        make_canary(tmp_path, "third", critical=False),
    ]
    qualification_id = "q-unknown-usage"
    unknown_slot = paired_schedule("first", 1, qualification_id)[0]
    overrides = {
        ("first", unknown_slot.side.value, unknown_slot.repetition): AttemptResult(
            side=unknown_slot.side.value,
            repetition=unknown_slot.repetition,
            success=True,
            valid=True,
            duration_ms=10,
            usage=Usage(),
        )
    }
    backend = TokenAwareBackend(
        default_usage=Usage(input_tokens=5, output_tokens=0, observed=True),
        overrides=overrides,
    )
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id=qualification_id,
        max_tokens=1000,
    )

    first, second, third = result.executions
    assert first.attempts != ()
    assert first.verdict is Verdict.PASS
    for skipped in (second, third):
        assert skipped.attempts == ()
        assert skipped.verdict is Verdict.INCOMPLETE
        assert skipped.reason == (
            "INCOMPLETE: skipped because token usage was unavailable "
            "for one or more attempts (max_tokens=1000)"
        )
    assert result.verdict is Verdict.INCOMPLETE
    assert result.observed_tokens is None
    assert result.attempts_used == 2


def test_invalid_observed_attempt_usage_counts(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
    ]
    qualification_id = "q-invalid-observed"
    invalid_slot = paired_schedule("first", 1, qualification_id)[0]
    overrides = {
        ("first", invalid_slot.side.value, invalid_slot.repetition): AttemptResult(
            side=invalid_slot.side.value,
            repetition=invalid_slot.repetition,
            success=False,
            valid=False,
            duration_ms=10,
            usage=Usage(input_tokens=8, output_tokens=2, observed=True),
        )
    }
    backend = TokenAwareBackend(
        default_usage=Usage(input_tokens=0, output_tokens=0, observed=True),
        overrides=overrides,
    )
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id=qualification_id,
        max_tokens=10,
    )

    assert result.attempts_used == 2
    assert result.observed_tokens == 10
    second = result.executions[1]
    assert second.verdict is Verdict.INCOMPLETE
    assert second.reason == (
        "INCOMPLETE: skipped by token budget (max_tokens=10, observed_tokens=10)"
    )


def test_zero_attempts_started_when_attempt_budget_too_small(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-zero-attempts",
        max_attempts=5,
    )

    assert backend.calls == []
    assert result.attempts_used == 0
    assert result.observed_tokens == 0


def test_no_budget_characterization_populates_usage_reporting(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=3, output_tokens=2, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-no-budget",
    )

    expected_calls = [
        (canary.id, slot.side.value, slot.repetition)
        for canary in suite
        for slot in paired_schedule(canary.id, 3, "q-no-budget")
    ]
    assert backend.calls == expected_calls
    assert [item.canary_id for item in result.executions] == [item.id for item in suite]
    assert result.run_order == tuple(expected_calls)
    assert result.verdict is Verdict.PASS
    assert result.max_attempts is None
    assert result.max_tokens is None
    assert result.attempts_used == len(expected_calls)
    assert result.observed_tokens == len(expected_calls) * 5


def test_token_budget_alone_activates_critical_first_ordering_but_preserves_result_order(
    tmp_path: Path,
) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=1, output_tokens=1, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-token-ordering",
        max_tokens=1_000_000,
    )

    assert backend.prepared == ["critical", "normal-a", "normal-b"]
    assert [item.canary_id for item in result.executions] == [
        "normal-a",
        "critical",
        "normal-b",
    ]
    assert result.verdict is Verdict.PASS
    assert all(item.attempts != () for item in result.executions)


def test_max_tokens_alone_never_arms_attempt_budget_skipping(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-token-only-no-attempt-arm",
        max_tokens=5,
    )

    second = result.executions[1]
    assert "attempt budget" not in second.reason
    assert second.reason == (
        "INCOMPLETE: skipped by token budget (max_tokens=5, observed_tokens=10)"
    )


def test_combination_precedence_attempt_budget_reason_wins(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "first", critical=False),
        make_canary(tmp_path, "second", critical=False),
        make_canary(tmp_path, "third", critical=False),
    ]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=5, output_tokens=0, observed=True))
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=1).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-combination-precedence",
        max_attempts=4,
        max_tokens=15,
    )

    assert result.attempts_used == 4
    assert result.observed_tokens == 20
    third = result.executions[2]
    assert third.verdict is Verdict.INCOMPLETE
    assert third.reason == (
        "INCOMPLETE: skipped by attempt budget (max_attempts=4, complete_canary_attempts=2)"
    )
    assert "token budget" not in third.reason
    assert "token usage" not in third.reason


@pytest.mark.parametrize("bad_max_tokens", [0, -1])
def test_run_rejects_nonpositive_max_tokens_for_direct_callers(
    tmp_path: Path, bad_max_tokens: int
) -> None:
    suite = [make_canary(tmp_path, "critical", critical=True)]
    backend = TokenAwareBackend(default_usage=Usage(input_tokens=1, output_tokens=1, observed=True))
    baseline, candidate = binaries()

    with pytest.raises(ValueError, match="max_tokens must be greater than zero"):
        QualificationExecutor(backend=backend, repetitions=1).run(
            baseline,
            candidate,
            suite,
            qualification_id="q-invalid-token-budget",
            max_tokens=bad_max_tokens,
        )

    assert backend.prepared == []
    assert backend.calls == []
