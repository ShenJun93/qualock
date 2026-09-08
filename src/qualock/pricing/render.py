from decimal import ROUND_HALF_EVEN, Decimal

from qualock.pricing.models import CostAnalysis

_CENT = Decimal("0.01")


def _format_money(lower: Decimal, upper: Decimal) -> str:
    lower_q = lower.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    upper_q = upper.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    if lower_q == upper_q:
        return f"${lower_q}"
    return f"${lower_q}–${upper_q}"


def _deduplicate(items: tuple[str, ...]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def render_cost_text(analysis: CostAnalysis) -> str:
    lines: list[str] = ["QuaLock Reference Cost", ""]

    if analysis.selected_canonical_model is not None:
        lines.append("Latest observed model/rate cohort")
        lines.append(f"- Model: {analysis.selected_canonical_model}")
        lines.append(f"- Rate card: {analysis.selected_rate_card_id}")
        lines.append("")
        lines.append("Typical complete qualification")

        missing = analysis.suite.missing_cost_canaries
        if not missing:
            assert analysis.suite.lower_usd is not None
            assert analysis.suite.upper_usd is not None
            money = _format_money(analysis.suite.lower_usd, analysis.suite.upper_usd)
            lines.append(f"About {money}")
        elif len(missing) == len(analysis.per_canary):
            lines.append("Unavailable")
            lines.append(
                "No trustworthy monetary samples are available for the "
                "current canaries in this cohort."
            )
            lines.append("")
            lines.append("Missing current canaries")
            for canary_id in missing:
                lines.append(f"- {canary_id}")
        else:
            lines.append(
                f"Unavailable: missing monetary history for {len(missing)} "
                "current canary/canaries"
            )
            lines.append("")
            for estimate in analysis.per_canary:
                if estimate.lower_median_usd is not None and estimate.upper_median_usd is not None:
                    money = _format_money(estimate.lower_median_usd, estimate.upper_median_usd)
                    lines.append(f"- {estimate.canary_id}: About {money}")
            lines.append("")
            lines.append("Missing current canaries")
            for canary_id in missing:
                lines.append(f"- {canary_id}")
        lines.append("")

        deduped_limitations = _deduplicate(analysis.limitations)
        if deduped_limitations:
            lines.append("Limitations")
            for item in deduped_limitations:
                lines.append(f"- {item}")
            lines.append("")
    else:
        lines.append("Reference cost unavailable.")
        lines.append("")
        lines.append(
            "No priced model/rate cohort with trustworthy pricing provenance is available"
        )
        lines.append("for the current configured agent/model/effort.")
        lines.append("")
        lines.append("Run a normal qualification after pricing provenance is available;")
        lines.append("QuaLock will preserve it for future estimates.")
        lines.append("")

    lines.append("History")
    lines.append(f"- Selected cohort runs: {analysis.selected_cohort_runs}")
    lines.append(f"- Priceable matching runs: {analysis.priceable_qualification_runs}")
    lines.append(f"- Older/unpinned runs: {analysis.older_unpinned_runs}")
    lines.append(f"- Unavailable pricing runs: {analysis.unavailable_pricing_runs}")
    lines.append(f"- Excluded config runs: {analysis.excluded_config_runs}")
    lines.append(f"- Excluded older cohorts: {analysis.excluded_cohort_runs}")
    lines.append(f"- Ignored pricing sidecars: {len(analysis.pricing_failures)}")
    for failure in sorted(analysis.pricing_failures, key=lambda item: item.qualification_id):
        lines.append(f"- {failure.qualification_id}: {failure.reason}")
    lines.append("")

    lines.append("Basis")
    lines.append("Public standard API list rates, USD.")
    lines.append("Reference estimate, not your actual bill.")

    return "\n".join(lines) + "\n"
