import hashlib
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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

from .models import (
    AttemptControlContext,
    AttemptControlProfiles,
    AttemptExecution,
    AttemptRunTrace,
    PreparedTarget,
)
from .schedule import Side, paired_schedule


class QualificationBackend(Protocol):
    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedTarget: ...

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedTarget,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult: ...


@runtime_checkable
class ProtocolAwareQualificationBackend(QualificationBackend, Protocol):
    def control_profiles(self, canary: CanarySpec) -> AttemptControlProfiles: ...

    def run_attempt_with_context(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedTarget,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptExecution: ...


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
        max_tokens: int | None = None,
        trace_sink: list[AttemptRunTrace] | None = None,
        trace_design_sha256: str | None = None,
    ) -> QualificationResult:
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be greater than zero")
        if trace_sink is not None and trace_design_sha256 is None:
            raise ValueError("trace_design_sha256 is required when trace_sink is provided")

        trace_epoch_ns = time.monotonic_ns() if trace_sink is not None else None
        indexed_suite = tuple(enumerate(suite))
        attempts_per_canary = self.repetitions * 2
        full_suite_attempts = len(indexed_suite) * attempts_per_canary
        attempt_constrained = max_attempts is not None and max_attempts < full_suite_attempts
        prioritize_critical = attempt_constrained or max_tokens is not None

        if prioritize_critical:
            execution_order = tuple(pair for pair in indexed_suite if pair[1].critical) + tuple(
                pair for pair in indexed_suite if not pair[1].critical
            )
        else:
            execution_order = indexed_suite

        remaining_attempts = max_attempts if attempt_constrained else None

        executions_by_index: dict[int, CanaryExecution] = {}
        comparisons_by_index: dict[int, CanaryComparison] = {}
        run_order: list[tuple[str, str, int]] = []

        attempts_used = 0
        observed_tokens: int | None = 0
        completed_canaries = 0

        for index, canary in execution_order:
            if (
                attempt_constrained
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

            if (
                max_tokens is not None
                and completed_canaries > 0
                and (observed_tokens is None or observed_tokens >= max_tokens)
            ):
                comparison, execution = _token_budget_skipped_canary(
                    canary,
                    repetitions=self.repetitions,
                    max_tokens=max_tokens,
                    observed_tokens=observed_tokens,
                )
                comparisons_by_index[index] = comparison
                executions_by_index[index] = execution
                continue

            prepared = self.backend.prepare(canary, qualification_id)
            attempts: list[AttemptResult] = []
            for slot in paired_schedule(canary.id, self.repetitions, qualification_id):
                binary = baseline_binary if slot.side is Side.BASELINE else candidate_binary
                started_ns = time.monotonic_ns() if trace_sink is not None else None
                if trace_sink is not None and isinstance(
                    self.backend, ProtocolAwareQualificationBackend
                ):
                    attempt_execution = self.backend.run_attempt_with_context(
                        canary=canary,
                        prepared=prepared,
                        binary=binary,
                        side=slot.side,
                        repetition=slot.repetition,
                    )
                    attempt = attempt_execution.result
                    context = attempt_execution.context
                else:
                    attempt = self.backend.run_attempt(
                        canary=canary,
                        prepared=prepared,
                        binary=binary,
                        side=slot.side,
                        repetition=slot.repetition,
                    )
                    context = AttemptControlContext(
                        profiles=AttemptControlProfiles(
                            preparation_sha256=None,
                            isolation_sha256=None,
                            resource_sha256=None,
                            runtime_sha256=None,
                        ),
                        isolation_instance_sha256=None,
                    )
                finished_ns = time.monotonic_ns() if trace_sink is not None else None
                if trace_sink is not None:
                    if trace_epoch_ns is None or started_ns is None or finished_ns is None:
                        raise RuntimeError("trace clock invariant violated")
                    trace_sink.append(
                        AttemptRunTrace(
                            canary_id=canary.id,
                            side=slot.side.value,
                            repetition=slot.repetition,
                            trace_design_sha256=trace_design_sha256,
                            started_offset_ms=(started_ns - trace_epoch_ns) // 1_000_000,
                            finished_offset_ms=(finished_ns - trace_epoch_ns) // 1_000_000,
                            events_sha256=hashlib.sha256(attempt.events_jsonl.encode()).hexdigest(),
                            context=context,
                        )
                    )
                attempts.append(attempt)
                run_order.append((canary.id, slot.side.value, slot.repetition))

                attempts_used += 1
                if observed_tokens is not None:
                    if attempt.usage.observed:
                        observed_tokens += attempt.usage.total_tokens
                    else:
                        observed_tokens = None

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

            completed_canaries += 1

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
            max_attempts=max_attempts,
            max_tokens=max_tokens,
            attempts_used=attempts_used,
            observed_tokens=observed_tokens,
        )


def _budget_skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    max_attempts: int,
    attempts_per_canary: int,
) -> tuple[CanaryComparison, CanaryExecution]:
    reason = (
        "INCOMPLETE: skipped by attempt budget "
        f"(max_attempts={max_attempts}, "
        f"complete_canary_attempts={attempts_per_canary})"
    )
    return _skipped_canary(canary, repetitions=repetitions, reason=reason)


def _token_budget_skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    max_tokens: int,
    observed_tokens: int | None,
) -> tuple[CanaryComparison, CanaryExecution]:
    if observed_tokens is None:
        reason = (
            "INCOMPLETE: skipped because token usage was unavailable "
            f"for one or more attempts (max_tokens={max_tokens})"
        )
    else:
        reason = (
            "INCOMPLETE: skipped by token budget "
            f"(max_tokens={max_tokens}, observed_tokens={observed_tokens})"
        )
    return _skipped_canary(canary, repetitions=repetitions, reason=reason)


def _skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    reason: str,
) -> tuple[CanaryComparison, CanaryExecution]:
    comparison = CanaryComparison(
        canary_id=canary.id,
        baseline=CanaryAggregate(valid_runs=0, successes=0, expected_runs=repetitions),
        candidate=CanaryAggregate(valid_runs=0, successes=0, expected_runs=repetitions),
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
