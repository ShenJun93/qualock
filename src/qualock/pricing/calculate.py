from collections.abc import Mapping
from decimal import Decimal

from qualock.history.models import HistoricalAttempt, HistoricalExecution
from qualock.pricing.models import AttemptUsageTrust, CostSample, RateComponents

_MILLION = Decimal(1_000_000)
_SIDES = frozenset({"baseline", "candidate"})
_TRUSTED_LEVELS = frozenset({"observed", "known_zero"})


def _has_usable_pairing(execution: HistoricalExecution) -> bool:
    slots: set[tuple[str, int]] = set()
    baseline: set[int] = set()
    candidate: set[int] = set()
    for attempt in execution.attempts:
        side = attempt.side
        repetition = attempt.repetition
        if side is None or side not in _SIDES:
            return False
        if repetition is None or isinstance(repetition, bool) or repetition < 1:
            return False
        slot = (side, repetition)
        if slot in slots:
            return False
        slots.add(slot)
        (baseline if side == "baseline" else candidate).add(repetition)
    return bool(baseline) and baseline == candidate


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _term(count: int, rate: Decimal | None) -> Decimal | None:
    if count == 0:
        return Decimal(0)
    if rate is None:
        return None
    return Decimal(count) * rate


def _price_attempt(
    canary_id: str,
    attempt: HistoricalAttempt,
    trust_by_identity: Mapping[tuple[str, str, int], AttemptUsageTrust],
    rates: RateComponents,
) -> tuple[Decimal, Decimal] | None:
    if not attempt.usage_observed:
        return None

    input_tokens = attempt.input_tokens
    output_tokens = attempt.output_tokens
    if not _is_nonnegative_int(input_tokens) or not _is_nonnegative_int(output_tokens):
        return None

    trust = trust_by_identity.get((canary_id, attempt.side, attempt.repetition))
    if trust is None:
        return None

    cached = attempt.cached_input_tokens
    write = attempt.cache_write_input_tokens
    if not _is_nonnegative_int(cached) or not _is_nonnegative_int(write):
        return None

    if trust.cached_input_tokens_trust not in _TRUSTED_LEVELS:
        return None
    if trust.cache_write_input_tokens_trust not in _TRUSTED_LEVELS:
        return None
    if trust.cached_input_tokens_trust == "known_zero" and cached != 0:
        return None
    if trust.cache_write_input_tokens_trust == "known_zero" and write != 0:
        return None

    reasoning = attempt.reasoning_output_tokens
    if reasoning is not None:
        if isinstance(reasoning, bool) or not isinstance(reasoning, int):
            return None
        if reasoning < 0 or reasoning > output_tokens:
            return None

    uncached = input_tokens - cached - write
    if uncached < 0:
        return None

    lower_uncached = _term(uncached, rates.input_uncached)
    lower_cached = _term(cached, rates.input_cached)
    lower_write = _term(write, rates.cache_write_lower)
    upper_write = _term(write, rates.cache_write_upper)
    output_term = _term(output_tokens, rates.output)

    parts_lower = (lower_uncached, lower_cached, lower_write, output_term)
    parts_upper = (lower_uncached, lower_cached, upper_write, output_term)
    if any(part is None for part in parts_lower) or any(part is None for part in parts_upper):
        return None

    lower_total = sum(parts_lower, Decimal(0))
    upper_total = sum(parts_upper, Decimal(0))
    return lower_total, upper_total


def price_execution(
    execution: HistoricalExecution,
    trust_by_identity: Mapping[tuple[str, str, int], AttemptUsageTrust],
    rates: RateComponents,
) -> CostSample | None:
    if not _has_usable_pairing(execution):
        return None

    lower_numerator = Decimal(0)
    upper_numerator = Decimal(0)
    for attempt in execution.attempts:
        priced = _price_attempt(execution.canary_id, attempt, trust_by_identity, rates)
        if priced is None:
            return None
        attempt_lower, attempt_upper = priced
        lower_numerator += attempt_lower
        upper_numerator += attempt_upper

    return CostSample(lower_numerator / _MILLION, upper_numerator / _MILLION)
