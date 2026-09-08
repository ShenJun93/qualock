from decimal import Decimal

from qualock.history.models import HistoricalAttempt, HistoricalExecution
from qualock.pricing.calculate import price_execution
from qualock.pricing.models import AttemptUsageTrust, CostSample, RateComponents

RATE = RateComponents(
    Decimal("2.00"), Decimal("0.20"), Decimal("2.50"), Decimal("2.50"), Decimal("12.00")
)
CLAUDE_RATE = RateComponents(
    Decimal("2.00"), Decimal("0.20"), Decimal("2.50"), Decimal("4.00"), Decimal("10.00")
)


def hist_attempt(
    side: str,
    repetition: int,
    *,
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
    usage_observed: bool = True,
    cached_input_tokens: int | None = 0,
    cache_write_input_tokens: int | None = 0,
    reasoning_output_tokens: int | None = None,
    success: bool | None = True,
    valid: bool | None = True,
    duration_ms: int | None = 100,
) -> HistoricalAttempt:
    return HistoricalAttempt(
        side=side,
        repetition=repetition,
        success=success,
        valid=valid,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_observed=usage_observed,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def zero_attempt(side: str, repetition: int) -> HistoricalAttempt:
    return hist_attempt(side, repetition, input_tokens=0, output_tokens=0)


def execution(*attempts: HistoricalAttempt, canary_id: str = "canary-a") -> HistoricalExecution:
    return HistoricalExecution(canary_id=canary_id, attempts=attempts)


def trust(
    canary_id: str,
    side: str,
    repetition: int,
    *,
    cached: str = "known_zero",
    write: str = "known_zero",
) -> AttemptUsageTrust:
    return AttemptUsageTrust(canary_id, side, repetition, cached, write)


def paired_trust(
    *, cached: str = "known_zero", write: str = "known_zero", canary_id: str = "canary-a"
) -> dict[tuple[str, str, int], AttemptUsageTrust]:
    return {
        (canary_id, "baseline", 1): trust(canary_id, "baseline", 1, cached=cached, write=write),
        (canary_id, "candidate", 1): trust(canary_id, "candidate", 1),
    }


def test_decimal_exact_cost_with_trusted_zero_cache_categories() -> None:
    exec_ = execution(
        hist_attempt("baseline", 1, input_tokens=1_000_000, output_tokens=100_000),
        hist_attempt("candidate", 1, input_tokens=500_000, output_tokens=50_000),
    )
    sample = price_execution(exec_, paired_trust(), RATE)
    assert sample == CostSample(Decimal("4.8"), Decimal("4.8"))


def test_cached_input_is_subtracted_from_uncached() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cached_input_tokens=200
        ),
        zero_attempt("candidate", 1),
    )
    trust_by_identity = paired_trust(cached="observed")
    sample = price_execution(exec_, trust_by_identity, RATE)
    assert sample == CostSample(Decimal("0.00164"), Decimal("0.00164"))


def test_cache_write_input_is_subtracted_from_uncached() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cache_write_input_tokens=300
        ),
        zero_attempt("candidate", 1),
    )
    trust_by_identity = paired_trust(write="observed")
    sample = price_execution(exec_, trust_by_identity, RATE)
    assert sample == CostSample(Decimal("0.00215"), Decimal("0.00215"))


def test_reasoning_tokens_are_validated_but_not_double_counted() -> None:
    trust_by_identity = paired_trust()

    with_reasoning = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=100, reasoning_output_tokens=40
        ),
        zero_attempt("candidate", 1),
    )
    without_reasoning = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100),
        zero_attempt("candidate", 1),
    )
    sample_with = price_execution(with_reasoning, trust_by_identity, RATE)
    sample_without = price_execution(without_reasoning, trust_by_identity, RATE)
    assert sample_with == sample_without == CostSample(Decimal("0.0032"), Decimal("0.0032"))

    invalid_reasoning = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=100, reasoning_output_tokens=101
        ),
        zero_attempt("candidate", 1),
    )
    assert price_execution(invalid_reasoning, trust_by_identity, RATE) is None


def test_negative_or_inconsistent_subsets_are_unpriceable() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline",
            1,
            input_tokens=1000,
            output_tokens=0,
            cached_input_tokens=600,
            cache_write_input_tokens=600,
        ),
        zero_attempt("candidate", 1),
    )
    trust_by_identity = paired_trust(cached="observed", write="observed")
    assert price_execution(exec_, trust_by_identity, RATE) is None


def test_unobserved_usage_is_unpriceable() -> None:
    exec_ = execution(
        hist_attempt("baseline", 1, usage_observed=False),
        zero_attempt("candidate", 1),
    )
    assert price_execution(exec_, paired_trust(), RATE) is None


def test_nonzero_category_requires_corresponding_rate() -> None:
    no_cached_rate = RateComponents(
        Decimal("2.00"), None, Decimal("2.50"), Decimal("2.50"), Decimal("12.00")
    )
    exec_cached = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cached_input_tokens=200
        ),
        zero_attempt("candidate", 1),
    )
    assert price_execution(exec_cached, paired_trust(cached="observed"), no_cached_rate) is None

    no_write_rate = RateComponents(
        Decimal("2.00"), Decimal("0.20"), None, None, Decimal("12.00")
    )
    exec_write = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cache_write_input_tokens=300
        ),
        zero_attempt("candidate", 1),
    )
    assert price_execution(exec_write, paired_trust(write="observed"), no_write_rate) is None


def test_claude_cache_write_rates_produce_lower_upper_range() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cache_write_input_tokens=300
        ),
        zero_attempt("candidate", 1),
    )
    sample = price_execution(exec_, paired_trust(write="observed"), CLAUDE_RATE)
    assert sample == CostSample(Decimal("0.00215"), Decimal("0.0026"))
    assert sample.lower_usd != sample.upper_usd


def test_explicit_zero_cache_write_is_exact_but_unobserved_zero_is_unpriceable() -> None:
    exec_ = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=0),
        zero_attempt("candidate", 1),
    )
    sample = price_execution(exec_, paired_trust(write="observed"), CLAUDE_RATE)
    assert sample == CostSample(Decimal("0.002"), Decimal("0.002"))

    assert price_execution(exec_, paired_trust(write="unobserved"), CLAUDE_RATE) is None


def test_failed_or_invalid_outcomes_still_price() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=100, success=False, valid=False
        ),
        zero_attempt("candidate", 1),
    )
    sample = price_execution(exec_, paired_trust(), RATE)
    assert sample == CostSample(Decimal("0.0032"), Decimal("0.0032"))


def test_malformed_duration_does_not_block_price() -> None:
    trust_by_identity = paired_trust()

    negative_duration = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100, duration_ms=-1),
        zero_attempt("candidate", 1),
    )
    assert price_execution(negative_duration, trust_by_identity, RATE) == CostSample(
        Decimal("0.0032"), Decimal("0.0032")
    )

    none_duration = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100, duration_ms=None),
        zero_attempt("candidate", 1),
    )
    assert price_execution(none_duration, trust_by_identity, RATE) == CostSample(
        Decimal("0.0032"), Decimal("0.0032")
    )


def test_skipped_execution_is_not_sample() -> None:
    exec_ = HistoricalExecution(canary_id="canary-a", attempts=())
    assert price_execution(exec_, {}, RATE) is None


def test_mismatched_or_duplicate_pairing_is_not_sample() -> None:
    missing_candidate = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100)
    )
    only_baseline_trust = {("canary-a", "baseline", 1): trust("canary-a", "baseline", 1)}
    assert price_execution(missing_candidate, only_baseline_trust, RATE) is None

    duplicate_slot = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100),
        hist_attempt("baseline", 1, input_tokens=500, output_tokens=50),
        hist_attempt("candidate", 1, input_tokens=1000, output_tokens=100),
    )
    assert price_execution(duplicate_slot, paired_trust(), RATE) is None


def test_known_zero_requires_persisted_numeric_zero() -> None:
    exec_ = execution(
        hist_attempt(
            "baseline", 1, input_tokens=1000, output_tokens=0, cached_input_tokens=5
        ),
        zero_attempt("candidate", 1),
    )
    assert price_execution(exec_, paired_trust(), RATE) is None


def test_missing_or_duplicate_trust_binding_is_unpriceable() -> None:
    exec_ = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100),
        zero_attempt("candidate", 1),
    )
    missing = {("canary-a", "candidate", 1): trust("canary-a", "candidate", 1)}
    assert price_execution(exec_, missing, RATE) is None

    colliding_entries = [
        trust("canary-a", "baseline", 1, cached="observed", write="observed"),
        trust("canary-a", "baseline", 1, cached="unobserved", write="unobserved"),
    ]
    duplicate = {
        (entry.canary_id, entry.side, entry.repetition): entry for entry in colliding_entries
    }
    duplicate[("canary-a", "candidate", 1)] = trust("canary-a", "candidate", 1)
    assert price_execution(exec_, duplicate, RATE) is None


def test_every_attempt_in_execution_must_be_priceable() -> None:
    exec_ = execution(
        hist_attempt("baseline", 1, input_tokens=1000, output_tokens=100),
        hist_attempt(
            "candidate",
            1,
            input_tokens=500,
            output_tokens=50,
            cached_input_tokens=600,
        ),
    )
    trust_by_identity = {
        ("canary-a", "baseline", 1): trust("canary-a", "baseline", 1),
        ("canary-a", "candidate", 1): trust("canary-a", "candidate", 1, cached="observed"),
    }
    assert price_execution(exec_, trust_by_identity, RATE) is None
