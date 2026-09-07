import statistics
from collections.abc import Sequence

from qualock.history.models import (
    CanaryEffectiveness,
    CanaryEstimate,
    HistoricalExecution,
    HistoryAnalysis,
    HistorySummary,
    SuiteEstimate,
)


def _has_usable_pairing(execution: HistoricalExecution) -> bool:
    slots: set[tuple[str, int]] = set()
    baseline: set[int] = set()
    candidate: set[int] = set()
    for attempt in execution.attempts:
        side = attempt.side
        repetition = attempt.repetition
        if side is None or side not in {"baseline", "candidate"}:
            return False
        if repetition is None or repetition < 1:
            return False
        slot = (side, repetition)
        if slot in slots:
            return False
        slots.add(slot)
        (baseline if side == "baseline" else candidate).add(repetition)
    return bool(baseline) and baseline == candidate


def _is_effectiveness_eligible(execution: HistoricalExecution) -> bool:
    for attempt in execution.attempts:
        if attempt.success is None or attempt.valid is None:
            return False
        if attempt.valid is not True:
            return False
        if attempt.side == "baseline" and attempt.success is not True:
            return False
    return True


def _is_detection(execution: HistoricalExecution) -> bool:
    return any(
        attempt.side == "candidate" and attempt.success is False for attempt in execution.attempts
    )


def _runtime_sample(execution: HistoricalExecution) -> int | None:
    total = 0
    for attempt in execution.attempts:
        duration = attempt.duration_ms
        if duration is None or duration < 0:
            return None
        total += duration
    return total


def _token_sample(execution: HistoricalExecution) -> int | None:
    total = 0
    for attempt in execution.attempts:
        if not attempt.usage_observed:
            return None
        input_tokens = attempt.input_tokens
        output_tokens = attempt.output_tokens
        if input_tokens is None or input_tokens < 0:
            return None
        if output_tokens is None or output_tokens < 0:
            return None
        total += input_tokens + output_tokens
    return total


def analyze_history(
    summary: HistorySummary,
    current_canary_ids: Sequence[str],
) -> HistoryAnalysis:
    current_ids = set(current_canary_ids)

    eligible_samples: dict[str, int] = {canary_id: 0 for canary_id in current_ids}
    detections: dict[str, int] = {canary_id: 0 for canary_id in current_ids}
    runtime_samples: dict[str, list[int]] = {canary_id: [] for canary_id in current_ids}
    token_samples: dict[str, list[int]] = {canary_id: [] for canary_id in current_ids}

    for report in summary.loaded:
        for execution in report.executions:
            canary_id = execution.canary_id
            if canary_id not in current_ids:
                continue
            if not _has_usable_pairing(execution):
                continue

            if _is_effectiveness_eligible(execution):
                eligible_samples[canary_id] += 1
                if _is_detection(execution):
                    detections[canary_id] += 1

            runtime_sample = _runtime_sample(execution)
            if runtime_sample is not None:
                runtime_samples[canary_id].append(runtime_sample)

            token_sample = _token_sample(execution)
            if token_sample is not None:
                token_samples[canary_id].append(token_sample)

    ranked: list[CanaryEffectiveness] = []
    not_enough_history: list[CanaryEffectiveness] = []
    for canary_id in current_canary_ids:
        samples = eligible_samples[canary_id]
        hits = detections[canary_id]
        rate = hits / samples if samples > 0 else None
        effectiveness = CanaryEffectiveness(
            canary_id=canary_id,
            eligible_samples=samples,
            detections=hits,
            detection_rate=rate,
        )
        if samples >= 3:
            ranked.append(effectiveness)
        else:
            not_enough_history.append(effectiveness)

    ranked.sort(
        key=lambda item: (
            -item.detection_rate if item.detection_rate is not None else 0.0,
            -item.detections,
            -item.eligible_samples,
            item.canary_id,
        )
    )

    per_canary_estimates: list[CanaryEstimate] = []
    runtime_medians: dict[str, float] = {}
    token_medians: dict[str, float] = {}
    for canary_id in current_canary_ids:
        runtime = tuple(runtime_samples[canary_id])
        tokens = tuple(token_samples[canary_id])
        runtime_median = statistics.median(runtime) if runtime else None
        token_median = statistics.median(tokens) if tokens else None
        if runtime_median is not None:
            runtime_medians[canary_id] = runtime_median
        if token_median is not None:
            token_medians[canary_id] = token_median
        per_canary_estimates.append(
            CanaryEstimate(
                canary_id=canary_id,
                runtime_samples_ms=runtime,
                token_samples=tokens,
                runtime_median_ms=runtime_median,
                token_median=token_median,
            )
        )

    missing_runtime = tuple(
        canary_id for canary_id in current_canary_ids if canary_id not in runtime_medians
    )
    missing_token = tuple(
        canary_id for canary_id in current_canary_ids if canary_id not in token_medians
    )
    suite_runtime = sum(runtime_medians.values()) if not missing_runtime else None
    suite_tokens = sum(token_medians.values()) if not missing_token else None

    suite_estimate = SuiteEstimate(
        runtime_ms=suite_runtime,
        tokens=suite_tokens,
        missing_runtime_canaries=missing_runtime,
        missing_token_canaries=missing_token,
    )

    return HistoryAnalysis(
        loaded_reports=len(summary.loaded),
        ignored_reports=summary.ignored,
        ranked=tuple(ranked),
        not_enough_history=tuple(not_enough_history),
        per_canary_estimates=tuple(per_canary_estimates),
        suite_estimate=suite_estimate,
    )
