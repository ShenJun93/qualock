from collections.abc import Mapping, Sequence
from decimal import Decimal

from qualock.history.models import HistorySummary
from qualock.pricing.calculate import price_execution
from qualock.pricing.models import (
    AttemptUsageTrust,
    CanaryCostEstimate,
    CostAnalysis,
    CostSample,
    PricingHistory,
    PricingSidecar,
    SuiteCostEstimate,
)


def _decimal_median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _config_key(agent: str, configured_model: str, reasoning_effort: str) -> tuple[str, str, str]:
    return (agent, configured_model, reasoning_effort)


def _select_cohort(priced_candidates: Sequence[PricingSidecar]) -> tuple[str, str] | None:
    if not priced_candidates:
        return None

    cohorts: dict[tuple[str, str], list[PricingSidecar]] = {}
    for record in priced_candidates:
        key = (record.canonical_model, record.rate_card_id)
        cohorts.setdefault(key, []).append(record)

    best_finish = max(record.run_finished_at for record in priced_candidates)
    tied_keys = {
        key
        for key, members in cohorts.items()
        if any(record.run_finished_at == best_finish for record in members)
    }
    return min(tied_keys)


def analyze_cost(
    summary: HistorySummary,
    pricing: PricingHistory,
    current_canary_ids: Sequence[str],
    *,
    agent: str,
    configured_model: str,
    reasoning_effort: str,
) -> CostAnalysis:
    current_config = _config_key(agent, configured_model, reasoning_effort)

    sidecar_by_id = {record.qualification_id: record for record in pricing.records}
    older_ids = set(pricing.older_unpinned_qualification_ids)

    priced_candidates = [
        record
        for record in pricing.records
        if record.availability == "priced"
        and _config_key(record.agent, record.configured_model, record.reasoning_effort)
        == current_config
    ]
    selected_key = _select_cohort(priced_candidates)

    older_unpinned_runs = 0
    excluded_config_runs = 0
    unavailable_pricing_runs = 0
    excluded_cohort_runs = 0
    selected_cohort_runs = 0
    selected_records: list[PricingSidecar] = []

    for loaded in summary.loaded:
        qualification_id = loaded.qualification_id
        if qualification_id in older_ids:
            older_unpinned_runs += 1
            continue

        record = sidecar_by_id.get(qualification_id)
        if record is None:
            continue

        if (
            _config_key(record.agent, record.configured_model, record.reasoning_effort)
            != current_config
        ):
            excluded_config_runs += 1
            continue

        if record.availability == "unavailable":
            unavailable_pricing_runs += 1
            continue

        key = (record.canonical_model, record.rate_card_id)
        if key == selected_key:
            selected_cohort_runs += 1
            selected_records.append(record)
        else:
            excluded_cohort_runs += 1

    loaded_by_id = {loaded.qualification_id: loaded for loaded in summary.loaded}
    current_ids = tuple(current_canary_ids)
    samples_by_canary: dict[str, list[CostSample]] = {canary_id: [] for canary_id in current_ids}
    priceable_ids: set[str] = set()

    for record in selected_records:
        loaded = loaded_by_id[record.qualification_id]
        trust_by_identity: Mapping[tuple[str, str, int], AttemptUsageTrust] = {
            (trust.canary_id, trust.side, trust.repetition): trust
            for trust in record.usage_detail_trust
        }
        for execution in loaded.executions:
            if execution.canary_id not in samples_by_canary:
                continue
            sample = price_execution(execution, trust_by_identity, record.rates)
            if sample is not None:
                samples_by_canary[execution.canary_id].append(sample)
                priceable_ids.add(record.qualification_id)

    per_canary: list[CanaryCostEstimate] = []
    for canary_id in current_ids:
        samples = tuple(samples_by_canary[canary_id])
        lower_median = _decimal_median(tuple(sample.lower_usd for sample in samples))
        upper_median = _decimal_median(tuple(sample.upper_usd for sample in samples))
        per_canary.append(
            CanaryCostEstimate(
                canary_id=canary_id,
                samples=samples,
                lower_median_usd=lower_median,
                upper_median_usd=upper_median,
            )
        )

    missing_cost_canaries = tuple(
        canary_id for canary_id in current_ids if not samples_by_canary[canary_id]
    )
    if missing_cost_canaries:
        suite = SuiteCostEstimate(None, None, missing_cost_canaries)
    else:
        lower_sum = sum((estimate.lower_median_usd for estimate in per_canary), Decimal(0))
        upper_sum = sum((estimate.upper_median_usd for estimate in per_canary), Decimal(0))
        suite = SuiteCostEstimate(lower_sum, upper_sum, ())

    selected_canonical_model, selected_rate_card_id = (
        selected_key if selected_key is not None else (None, None)
    )
    limitations = selected_records[0].limitations if selected_records else ()

    return CostAnalysis(
        current_agent=agent,
        configured_model=configured_model,
        reasoning_effort=reasoning_effort,
        selected_canonical_model=selected_canonical_model,
        selected_rate_card_id=selected_rate_card_id,
        per_canary=tuple(per_canary),
        suite=suite,
        selected_cohort_runs=selected_cohort_runs,
        priceable_qualification_runs=len(priceable_ids),
        older_unpinned_runs=older_unpinned_runs,
        unavailable_pricing_runs=unavailable_pricing_runs,
        excluded_config_runs=excluded_config_runs,
        excluded_cohort_runs=excluded_cohort_runs,
        pricing_failures=pricing.failures,
        limitations=limitations,
    )
