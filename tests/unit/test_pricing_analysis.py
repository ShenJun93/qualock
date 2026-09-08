from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from qualock.history.models import (
    HistoricalAttempt,
    HistoricalExecution,
    HistorySummary,
    LoadedReport,
)
from qualock.pricing.analysis import analyze_cost
from qualock.pricing.models import (
    AttemptUsageTrust,
    PricingHistory,
    PricingLoadFailure,
    PricingSidecar,
    RateComponents,
)

BASE_RATES = RateComponents(
    Decimal("2.00"), Decimal("0.20"), Decimal("2.50"), Decimal("2.50"), Decimal("12.00")
)
FINISH = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_PROVIDER_BY_AGENT = {
    "codex": "openai",
    "claude": "anthropic",
    "antigravity": "google",
    "gemini": "google",
}
_CURRENT = {"agent": "codex", "configured_model": "gpt-5.6-terra", "reasoning_effort": "high"}


def paired_execution(
    canary_id: str = "canary-a", *, input_tokens: int = 1000, output_tokens: int = 100
) -> HistoricalExecution:
    def attempt(side: str) -> HistoricalAttempt:
        return HistoricalAttempt(
            side=side,
            repetition=1,
            success=True,
            valid=True,
            duration_ms=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_observed=True,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
        )

    return HistoricalExecution(canary_id=canary_id, attempts=(attempt("baseline"), attempt("candidate")))


def report(
    qualification_id: str,
    *,
    canary_ids: tuple[str, ...] = ("canary-a",),
    input_tokens: int = 1000,
    output_tokens: int = 100,
) -> LoadedReport:
    executions = tuple(
        paired_execution(canary_id, input_tokens=input_tokens, output_tokens=output_tokens)
        for canary_id in canary_ids
    )
    return LoadedReport(
        qualification_id=qualification_id,
        qualification_dir=Path(qualification_id),
        executions=executions,
    )


def _trust_for(canary_ids: tuple[str, ...]) -> tuple[AttemptUsageTrust, ...]:
    entries = []
    for canary_id in canary_ids:
        entries.append(AttemptUsageTrust(canary_id, "baseline", 1, "known_zero", "known_zero"))
        entries.append(AttemptUsageTrust(canary_id, "candidate", 1, "known_zero", "known_zero"))
    return tuple(entries)


def sidecar(
    qualification_id: str,
    *,
    finished: datetime,
    agent: str = "codex",
    configured_model: str = "gpt-5.6-terra",
    reasoning_effort: str = "high",
    canonical_model: str | None = "gpt-5.6-terra",
    rate_card_id: str | None = "openai:gpt-5.6-terra:standard:2026-09-07",
    rates: RateComponents | None = BASE_RATES,
    limitations: tuple[str, ...] = ("standard limitation",),
    availability: str = "priced",
    canary_ids: tuple[str, ...] = ("canary-a",),
    unavailable_reason: str | None = None,
) -> PricingSidecar:
    is_priced = availability == "priced"
    return PricingSidecar(
        qualification_id=qualification_id,
        qualification_dir=Path(qualification_id),
        availability=availability,
        run_started_at=finished - timedelta(minutes=5),
        run_finished_at=finished,
        agent=agent,
        provider=_PROVIDER_BY_AGENT[agent],
        configured_model=configured_model,
        reasoning_effort=reasoning_effort,
        canonical_model=canonical_model if is_priced else None,
        model_identity_source="configured_exact",
        catalog_version="2026-09-07.1",
        rate_card_id=rate_card_id if is_priced else None,
        source_url="https://example.test/model" if is_priced else None,
        source_checked_at=date(2026, 9, 7) if is_priced else None,
        effective_from=date(2026, 9, 7) if is_priced else None,
        effective_until=None,
        rates=rates if is_priced else None,
        usage_detail_trust=_trust_for(canary_ids),
        limitations=limitations if is_priced else (),
        unavailable_reason=unavailable_reason if not is_priced else None,
    )


def priced_history(
    *records: PricingSidecar,
    older_unpinned: tuple[str, ...] = (),
    failures: tuple[PricingLoadFailure, ...] = (),
) -> PricingHistory:
    return PricingHistory(
        records=records, older_unpinned_qualification_ids=older_unpinned, failures=failures
    )


def test_current_agent_model_effort_must_match_exactly() -> None:
    matching = report("q-1")
    mismatched = report("q-2")
    summary = HistorySummary(loaded=(matching, mismatched), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH),
        sidecar("q-2", finished=FINISH, reasoning_effort="low"),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_cohort_runs == 1
    assert analysis.excluded_config_runs == 1


def test_trusted_pinned_gemini_cohort_is_priceable() -> None:
    r = report("q-gemini")
    summary = HistorySummary(loaded=(r,), ignored=())
    google_rates = RateComponents(
        Decimal("0.75"), Decimal("0.075"), None, None, Decimal("3.75")
    )
    pricing = priced_history(
        sidecar(
            "q-gemini",
            finished=FINISH,
            agent="gemini",
            configured_model="gemini-3.8-flash",
            reasoning_effort="provider-default",
            canonical_model="gemini-3.8-flash",
            rate_card_id=(
                "google:gemini-3.8-flash:standard:through-2026-12-31"
            ),
            rates=google_rates,
        )
    )

    analysis = analyze_cost(
        summary,
        pricing,
        ["canary-a"],
        agent="gemini",
        configured_model="gemini-3.8-flash",
        reasoning_effort="provider-default",
    )

    assert analysis.selected_canonical_model == "gemini-3.8-flash"
    assert analysis.priceable_qualification_runs == 1
    assert analysis.suite.lower_usd == Decimal("0.00225")
    assert analysis.suite.upper_usd == Decimal("0.00225")


def test_canonical_models_never_mix() -> None:
    r1 = report("q-1")
    r2 = report("q-2")
    summary = HistorySummary(loaded=(r1, r2), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH),
        sidecar(
            "q-2",
            finished=FINISH - timedelta(minutes=1),
            canonical_model="gpt-5.6-sol",
            rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        ),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_canonical_model == "gpt-5.6-terra"
    assert analysis.selected_cohort_runs == 1
    assert analysis.excluded_cohort_runs == 1


def test_rate_card_ids_never_mix() -> None:
    r1 = report("q-1")
    r2 = report("q-2")
    summary = HistorySummary(loaded=(r1, r2), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH),
        sidecar(
            "q-2",
            finished=FINISH - timedelta(minutes=1),
            rate_card_id="openai:gpt-5.6-terra:standard:2099-01-01",
        ),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_rate_card_id == "openai:gpt-5.6-terra:standard:2026-09-07"
    assert analysis.selected_cohort_runs == 1
    assert analysis.excluded_cohort_runs == 1


def test_latest_finished_cohort_wins() -> None:
    older = report("q-1")
    newer = report("q-2")
    summary = HistorySummary(loaded=(older, newer), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH - timedelta(days=1)),
        sidecar(
            "q-2",
            finished=FINISH,
            canonical_model="gpt-5.6-sol",
            rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        ),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_canonical_model == "gpt-5.6-sol"


def test_latest_tie_uses_lexicographically_smallest_cohort() -> None:
    r1 = report("q-1")
    r2 = report("q-2")
    summary = HistorySummary(loaded=(r1, r2), ignored=())
    pricing = priced_history(
        sidecar(
            "q-1",
            finished=FINISH,
            canonical_model="gpt-5.6-terra",
            rate_card_id="openai:gpt-5.6-terra:standard:2026-09-07",
        ),
        sidecar(
            "q-2",
            finished=FINISH,
            canonical_model="gpt-5.6-sol",
            rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        ),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_canonical_model == "gpt-5.6-sol"


def test_removed_historical_canaries_do_not_enter_estimates() -> None:
    historical = report("q-1", canary_ids=("canary-a", "canary-removed"))
    summary = HistorySummary(loaded=(historical,), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH, canary_ids=("canary-a", "canary-removed"))
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert [estimate.canary_id for estimate in analysis.per_canary] == ["canary-a"]
    assert analysis.per_canary[0].samples


def test_per_canary_medians_are_decimal_without_prerounding() -> None:
    reports = tuple(report(f"q-{i}", input_tokens=1000 + i, output_tokens=100) for i in range(3))
    summary = HistorySummary(loaded=reports, ignored=())
    pricing = priced_history(
        *(sidecar(f"q-{i}", finished=FINISH + timedelta(minutes=i)) for i in range(3))
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    estimate = analysis.per_canary[0]
    assert all(isinstance(sample.lower_usd, Decimal) for sample in estimate.samples)
    assert estimate.lower_median_usd == Decimal("0.006404")


def test_even_decimal_median_averages_middle_values() -> None:
    reports = (report("q-0", input_tokens=1000), report("q-1", input_tokens=1001))
    summary = HistorySummary(loaded=reports, ignored=())
    pricing = priced_history(
        sidecar("q-0", finished=FINISH),
        sidecar("q-1", finished=FINISH + timedelta(minutes=1)),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.per_canary[0].lower_median_usd == Decimal("0.006402")


def test_suite_sums_unrounded_per_canary_medians() -> None:
    reports = (report("q-1", canary_ids=("canary-a", "canary-b"), input_tokens=1000),)
    summary = HistorySummary(loaded=reports, ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH, canary_ids=("canary-a", "canary-b")),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a", "canary-b"], **_CURRENT)

    expected = Decimal("0.0064") + Decimal("0.0064")
    assert analysis.suite.lower_usd == expected
    assert analysis.suite.upper_usd == expected


def test_missing_current_canary_makes_suite_unavailable() -> None:
    reports = (report("q-1", canary_ids=("canary-a",)),)
    summary = HistorySummary(loaded=reports, ignored=())
    pricing = priced_history(sidecar("q-1", finished=FINISH, canary_ids=("canary-a",)))

    analysis = analyze_cost(summary, pricing, ["canary-a", "canary-b"], **_CURRENT)

    assert analysis.suite.lower_usd is None
    assert analysis.suite.upper_usd is None
    assert analysis.suite.missing_cost_canaries == ("canary-b",)


def test_missing_canaries_preserve_current_config_order() -> None:
    reports = (report("q-1", canary_ids=("canary-b",)),)
    summary = HistorySummary(loaded=reports, ignored=())
    pricing = priced_history(sidecar("q-1", finished=FINISH, canary_ids=("canary-b",)))

    analysis = analyze_cost(
        summary, pricing, ["canary-c", "canary-a", "canary-b"], **_CURRENT
    )

    assert analysis.suite.missing_cost_canaries == ("canary-c", "canary-a")
    assert [estimate.canary_id for estimate in analysis.per_canary] == [
        "canary-c",
        "canary-a",
        "canary-b",
    ]


def test_primary_classifications_are_disjoint_and_exhaustive() -> None:
    older = report("q-older")
    failed = report("q-failed")
    excluded_config = report("q-excluded-config")
    unavailable = report("q-unavailable")
    excluded_cohort = report("q-excluded-cohort")
    selected = report("q-selected")

    summary = HistorySummary(
        loaded=(older, failed, excluded_config, unavailable, excluded_cohort, selected),
        ignored=(),
    )
    pricing = priced_history(
        sidecar("q-excluded-config", finished=FINISH, reasoning_effort="low"),
        sidecar(
            "q-unavailable",
            finished=FINISH,
            availability="unavailable",
            unavailable_reason="no_rate_card",
        ),
        sidecar(
            "q-excluded-cohort",
            finished=FINISH - timedelta(minutes=1),
            canonical_model="gpt-5.6-sol",
            rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        ),
        sidecar("q-selected", finished=FINISH),
        older_unpinned=("q-older",),
        failures=(PricingLoadFailure("q-failed", Path("q-failed"), "malformed pricing sidecar"),),
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    primary_total = (
        analysis.selected_cohort_runs
        + analysis.older_unpinned_runs
        + analysis.unavailable_pricing_runs
        + analysis.excluded_config_runs
        + analysis.excluded_cohort_runs
        + len(analysis.pricing_failures)
    )
    assert primary_total == len(summary.loaded)
    assert analysis.priceable_qualification_runs <= analysis.selected_cohort_runs
    assert analysis.older_unpinned_runs == 1
    assert analysis.unavailable_pricing_runs == 1
    assert analysis.excluded_config_runs == 1
    assert analysis.excluded_cohort_runs == 1
    assert analysis.selected_cohort_runs == 1


def test_one_multicanary_run_counts_priceable_qualification_once() -> None:
    multi = report("q-1", canary_ids=("canary-a", "canary-b"))
    summary = HistorySummary(loaded=(multi,), ignored=())
    pricing = priced_history(sidecar("q-1", finished=FINISH, canary_ids=("canary-a", "canary-b")))

    analysis = analyze_cost(summary, pricing, ["canary-a", "canary-b"], **_CURRENT)

    assert analysis.priceable_qualification_runs == 1
    assert analysis.selected_cohort_runs == 1


def test_selected_run_without_samples_is_not_priceable_run() -> None:
    unpriceable = report("q-1")
    summary = HistorySummary(loaded=(unpriceable,), ignored=())
    trust = (
        AttemptUsageTrust("canary-a", "baseline", 1, "unobserved", "known_zero"),
        AttemptUsageTrust("canary-a", "candidate", 1, "unobserved", "known_zero"),
    )
    bad_sidecar = replace(sidecar("q-1", finished=FINISH), usage_detail_trust=trust)
    pricing = priced_history(bad_sidecar)

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_cohort_runs == 1
    assert analysis.priceable_qualification_runs == 0


def test_historical_samples_use_pinned_rates_not_current_catalog() -> None:
    custom_rates = RateComponents(
        Decimal("999.00"), Decimal("0.20"), Decimal("2.50"), Decimal("2.50"), Decimal("12.00")
    )
    r = report("q-1", input_tokens=1000, output_tokens=0)
    summary = HistorySummary(loaded=(r,), ignored=())
    pricing = priced_history(sidecar("q-1", finished=FINISH, rates=custom_rates))

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.per_canary[0].lower_median_usd == Decimal("1.998")


def test_selected_limitations_come_only_from_pinned_snapshot() -> None:
    r = report("q-1")
    summary = HistorySummary(loaded=(r,), ignored=())
    pricing = priced_history(
        sidecar("q-1", finished=FINISH, limitations=("pinned limitation text",))
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.limitations == ("pinned limitation text",)


def test_unavailable_sidecar_never_enters_priced_cohort() -> None:
    r = report("q-1")
    summary = HistorySummary(loaded=(r,), ignored=())
    pricing = priced_history(
        sidecar(
            "q-1", finished=FINISH, availability="unavailable", unavailable_reason="no_rate_card"
        )
    )

    analysis = analyze_cost(summary, pricing, ["canary-a"], **_CURRENT)

    assert analysis.selected_canonical_model is None
    assert analysis.selected_rate_card_id is None
    assert analysis.selected_cohort_runs == 0
    assert analysis.unavailable_pricing_runs == 1
