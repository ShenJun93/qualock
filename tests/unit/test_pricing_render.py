from decimal import Decimal
from pathlib import Path

from qualock.pricing.models import (
    CanaryCostEstimate,
    CostAnalysis,
    PricingLoadFailure,
    SuiteCostEstimate,
)
from qualock.pricing.render import render_cost_text


def cost_analysis(
    *,
    suite: SuiteCostEstimate,
    per_canary: tuple[CanaryCostEstimate, ...] = (),
    selected_canonical_model: str | None = "gpt-5.6-sol",
    selected_rate_card_id: str | None = "openai:gpt-5.6-sol:standard:2026-09-07",
    selected_cohort_runs: int = 1,
    priceable_qualification_runs: int = 1,
    older_unpinned_runs: int = 0,
    unavailable_pricing_runs: int = 0,
    excluded_config_runs: int = 0,
    excluded_cohort_runs: int = 0,
    pricing_failures: tuple[PricingLoadFailure, ...] = (),
    limitations: tuple[str, ...] = (),
) -> CostAnalysis:
    return CostAnalysis(
        current_agent="codex",
        configured_model="gpt-5.6",
        reasoning_effort="high",
        selected_canonical_model=selected_canonical_model,
        selected_rate_card_id=selected_rate_card_id,
        per_canary=per_canary,
        suite=suite,
        selected_cohort_runs=selected_cohort_runs,
        priceable_qualification_runs=priceable_qualification_runs,
        older_unpinned_runs=older_unpinned_runs,
        unavailable_pricing_runs=unavailable_pricing_runs,
        excluded_config_runs=excluded_config_runs,
        excluded_cohort_runs=excluded_cohort_runs,
        pricing_failures=pricing_failures,
        limitations=limitations,
    )


def test_output_starts_with_exact_title() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("1.42"), Decimal("1.42"), ()))
    )
    assert text.startswith("QuaLock Reference Cost")


def test_complete_suite_renders_latest_cohort_and_one_amount() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("1.424"), Decimal("1.425"), ()))
    )
    assert "Latest observed model/rate cohort" in text
    assert "- Model: gpt-5.6-sol" in text
    assert "- Rate card: openai:gpt-5.6-sol:standard:2026-09-07" in text
    assert "About $1.42" in text
    assert "$1.42–$1.42" not in text
    assert "Public standard API list rates, USD." in text
    assert "Reference estimate, not your actual bill." in text


def test_distinct_range_renders_en_dash_endpoints() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("1.42"), Decimal("1.55"), ()))
    )
    assert "About $1.42–$1.55" in text


def test_subcent_range_collapses_to_one_display_amount() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("1.4201"), Decimal("1.4249"), ()))
    )
    assert "About $1.42" in text
    assert "–" not in text


def test_money_rounds_only_at_final_boundary_half_even() -> None:
    tie_down = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("0.125"), Decimal("0.125"), ()))
    )
    assert "About $0.12" in tie_down
    assert "$0.13" not in tie_down

    tie_up = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("0.135"), Decimal("0.135"), ()))
    )
    assert "About $0.14" in tie_up
    assert "$0.13" not in tie_up


def test_money_never_abbreviates_thousands() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal("12345.678"), Decimal("12345.678"), ()))
    )
    assert "About $12345.68" in text
    assert "12,345" not in text
    assert "12.3k" not in text.lower()


def test_partial_suite_renders_missing_count_and_config_order() -> None:
    per_canary = (
        CanaryCostEstimate("alpha", (), Decimal("0.50"), Decimal("0.50")),
        CanaryCostEstimate("beta", (), None, None),
    )
    text = render_cost_text(
        cost_analysis(
            suite=SuiteCostEstimate(None, None, ("beta",)),
            per_canary=per_canary,
        )
    )
    assert "Unavailable: missing monetary history for 1 current canary/canaries" in text
    assert "Missing current canaries" in text
    assert text.index("alpha") < text.index("beta")


def test_selected_cohort_with_zero_samples_has_specific_guidance() -> None:
    per_canary = (CanaryCostEstimate("alpha", (), None, None),)
    text = render_cost_text(
        cost_analysis(
            suite=SuiteCostEstimate(None, None, ("alpha",)),
            per_canary=per_canary,
        )
    )
    assert (
        "No trustworthy monetary samples are available for the "
        "current canaries in this cohort." in text
    )
    assert "Unavailable" in text
    assert "alpha" in text


def test_no_selected_cohort_is_unavailable_not_error() -> None:
    text = render_cost_text(
        cost_analysis(
            suite=SuiteCostEstimate(None, None, ()),
            selected_canonical_model=None,
            selected_rate_card_id=None,
        )
    )
    assert "Reference cost unavailable." in text
    assert "error" not in text.lower()


def test_basis_and_not_actual_bill_are_always_present() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal(1), Decimal(1), ()))
    )
    assert "Public standard API list rates, USD." in text
    assert "Reference estimate, not your actual bill." in text


def test_limitations_are_deduplicated_in_first_seen_order() -> None:
    text = render_cost_text(
        cost_analysis(
            suite=SuiteCostEstimate(Decimal(1), Decimal(1), ()),
            limitations=("b-limit", "a-limit", "b-limit"),
        )
    )
    assert text.count("b-limit") == 1
    assert text.index("b-limit") < text.index("a-limit")


def test_every_history_counter_renders_even_when_zero() -> None:
    text = render_cost_text(
        cost_analysis(suite=SuiteCostEstimate(Decimal(1), Decimal(1), ()))
    )
    assert "- Selected cohort runs: 1" in text
    assert "- Priceable matching runs: 1" in text
    assert "- Older/unpinned runs: 0" in text
    assert "- Unavailable pricing runs: 0" in text
    assert "- Excluded config runs: 0" in text
    assert "- Excluded older cohorts: 0" in text
    assert "- Ignored pricing sidecars: 0" in text


def test_pricing_failures_are_lexical_fixed_rows_without_paths_or_exceptions() -> None:
    failures = (
        PricingLoadFailure(
            "qual-b",
            Path("/etc/secret/Traceback (most recent call last)"),
            "malformed pricing sidecar",
        ),
        PricingLoadFailure(
            "qual-a",
            Path("/home/user/.aws/credentials"),
            "malformed pricing sidecar",
        ),
    )
    text = render_cost_text(
        cost_analysis(
            suite=SuiteCostEstimate(Decimal(1), Decimal(1), ()),
            pricing_failures=failures,
        )
    )
    assert "- qual-a: malformed pricing sidecar" in text
    assert "- qual-b: malformed pricing sidecar" in text
    assert text.index("qual-a") < text.index("qual-b")
    assert "/etc/secret" not in text
    assert "/home/user/.aws" not in text
    assert "Traceback" not in text
    assert "credentials" not in text
