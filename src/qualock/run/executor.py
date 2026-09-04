from collections.abc import Sequence
from typing import Protocol

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import (
    AttemptResult,
    CanaryAggregate,
    CanaryComparison,
    CanaryExecution,
    QualificationResult,
    Verdict,
)
from qualock.qualification.policy import qualify_canary, qualify_suite

from .models import PreparedImage
from .schedule import Side, paired_schedule


class QualificationBackend(Protocol):
    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedImage: ...

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedImage,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult: ...


class QualificationExecutor:
    def __init__(self, *, backend: QualificationBackend, repetitions: int = 3) -> None:
        if repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        self.backend = backend
        self.repetitions = repetitions

    def run(
        self,
        baseline_binary: AgentBinary,
        candidate_binary: AgentBinary,
        suite: Sequence[CanarySpec],
        *,
        qualification_id: str,
        max_attempts: int | None = None,
    ) -> QualificationResult:
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")

        indexed_suite = tuple(enumerate(suite))
        attempts_per_canary = self.repetitions * 2
        full_suite_attempts = len(indexed_suite) * attempts_per_canary
        constrained = max_attempts is not None and max_attempts < full_suite_attempts

        if constrained:
            execution_order = tuple(pair for pair in indexed_suite if pair[1].critical) + tuple(
                pair for pair in indexed_suite if not pair[1].critical
            )
            remaining_attempts = max_attempts
        else:
            execution_order = indexed_suite
            remaining_attempts = None

        executions_by_index: dict[int, CanaryExecution] = {}
        comparisons_by_index: dict[int, CanaryComparison] = {}
        run_order: list[tuple[str, str, int]] = []

        for index, canary in execution_order:
            if (
                constrained
                and remaining_attempts is not None
                and remaining_attempts < attempts_per_canary
            ):
                assert max_attempts is not None
                comparison, execution = _budget_skipped_canary(
                    canary,
                    repetitions=self.repetitions,
                    max_attempts=max_attempts,
                    attempts_per_canary=attempts_per_canary,
                )
                comparisons_by_index[index] = comparison
                executions_by_index[index] = execution
                continue

            prepared = self.backend.prepare(canary, qualification_id)
            attempts: list[AttemptResult] = []
            for slot in paired_schedule(canary.id, self.repetitions, qualification_id):
                binary = baseline_binary if slot.side is Side.BASELINE else candidate_binary
                attempt = self.backend.run_attempt(
                    canary=canary,
                    prepared=prepared,
                    binary=binary,
                    side=slot.side,
                    repetition=slot.repetition,
                )
                attempts.append(attempt)
                run_order.append((canary.id, slot.side.value, slot.repetition))

            baseline_attempts = [item for item in attempts if item.side == Side.BASELINE.value]
            candidate_attempts = [item for item in attempts if item.side == Side.CANDIDATE.value]
            baseline_valid = sum(item.valid for item in baseline_attempts)
            candidate_valid = sum(item.valid for item in candidate_attempts)
            baseline_successes = sum(item.valid and item.success for item in baseline_attempts)
            candidate_successes = sum(item.valid and item.success for item in candidate_attempts)

            comparison = qualify_canary(
                canary.id,
                CanaryAggregate(
                    valid_runs=baseline_valid,
                    successes=baseline_successes,
                    expected_runs=self.repetitions,
                ),
                CanaryAggregate(
                    valid_runs=candidate_valid,
                    successes=candidate_successes,
                    expected_runs=self.repetitions,
                ),
                critical=canary.critical,
            )
            comparisons_by_index[index] = comparison
            executions_by_index[index] = CanaryExecution(
                canary_id=canary.id,
                critical=canary.critical,
                prepared_image_digest=prepared.digest,
                attempts=tuple(attempts),
                baseline_successes=baseline_successes,
                candidate_successes=candidate_successes,
                baseline_valid=baseline_valid,
                candidate_valid=candidate_valid,
                verdict=comparison.verdict,
                reason=comparison.reason,
            )

            if remaining_attempts is not None:
                remaining_attempts -= attempts_per_canary

        comparisons = tuple(comparisons_by_index[index] for index, _ in indexed_suite)
        executions = tuple(executions_by_index[index] for index, _ in indexed_suite)
        suite_verdict = qualify_suite(comparisons)
        return QualificationResult(
            qualification_id=qualification_id,
            baseline_version=baseline_binary.version,
            candidate_version=candidate_binary.version,
            verdict=suite_verdict.verdict,
            executions=executions,
            reasons=suite_verdict.reasons,
            run_order=tuple(run_order),
        )


def _budget_skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    max_attempts: int,
    attempts_per_canary: int,
) -> tuple[CanaryComparison, CanaryExecution]:
    aggregate = CanaryAggregate(
        valid_runs=0,
        successes=0,
        expected_runs=repetitions,
    )
    reason = (
        "INCOMPLETE: skipped by attempt budget "
        f"(max_attempts={max_attempts}, "
        f"complete_canary_attempts={attempts_per_canary})"
    )
    comparison = CanaryComparison(
        canary_id=canary.id,
        baseline=aggregate,
        candidate=aggregate,
        critical=canary.critical,
        verdict=Verdict.INCOMPLETE,
        reason=reason,
        baseline_stable=False,
    )
    execution = CanaryExecution(
        canary_id=canary.id,
        critical=canary.critical,
        prepared_image_digest="",
        attempts=(),
        baseline_successes=0,
        candidate_successes=0,
        baseline_valid=0,
        candidate_valid=0,
        verdict=Verdict.INCOMPLETE,
        reason=reason,
    )
    return comparison, execution
