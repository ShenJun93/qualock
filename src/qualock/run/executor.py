from collections.abc import Sequence
from typing import Protocol

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import (
    AttemptResult,
    CanaryAggregate,
    CanaryExecution,
    QualificationResult,
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
    ) -> QualificationResult:
        executions: list[CanaryExecution] = []
        comparisons = []
        run_order: list[tuple[str, str, int]] = []

        for canary in suite:
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
            comparisons.append(comparison)
            executions.append(
                CanaryExecution(
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
            )

        suite_verdict = qualify_suite(comparisons)
        return QualificationResult(
            qualification_id=qualification_id,
            baseline_version=baseline_binary.version,
            candidate_version=candidate_binary.version,
            verdict=suite_verdict.verdict,
            executions=tuple(executions),
            reasons=suite_verdict.reasons,
            run_order=tuple(run_order),
        )
