from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class RateComponents:
    input_uncached: Decimal
    input_cached: Decimal | None
    cache_write_lower: Decimal | None
    cache_write_upper: Decimal | None
    output: Decimal


@dataclass(frozen=True)
class RateCard:
    rate_card_id: str
    provider: str
    canonical_model: str
    effective_from: date | None
    effective_until: date | None
    source_url: str
    source_checked_at: date
    rates: RateComponents
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ModelIdentity:
    canonical_model: str | None
    source: str
    unavailable_reason: str | None


@dataclass(frozen=True)
class AttemptUsageTrust:
    canary_id: str
    side: str
    repetition: int
    cached_input_tokens_trust: str
    cache_write_input_tokens_trust: str


@dataclass(frozen=True)
class PricingSidecar:
    qualification_id: str
    qualification_dir: Path
    availability: str
    run_started_at: datetime
    run_finished_at: datetime
    agent: str
    provider: str
    configured_model: str
    reasoning_effort: str
    canonical_model: str | None
    model_identity_source: str
    catalog_version: str
    rate_card_id: str | None
    source_url: str | None
    source_checked_at: date | None
    effective_from: date | None
    effective_until: date | None
    rates: RateComponents | None
    usage_detail_trust: tuple[AttemptUsageTrust, ...]
    limitations: tuple[str, ...]
    unavailable_reason: str | None


@dataclass(frozen=True)
class PricingLoadFailure:
    qualification_id: str
    qualification_dir: Path
    reason: str


@dataclass(frozen=True)
class PricingHistory:
    records: tuple[PricingSidecar, ...]
    older_unpinned_qualification_ids: tuple[str, ...]
    failures: tuple[PricingLoadFailure, ...]


@dataclass(frozen=True)
class CostSample:
    lower_usd: Decimal
    upper_usd: Decimal


@dataclass(frozen=True)
class CanaryCostEstimate:
    canary_id: str
    samples: tuple[CostSample, ...]
    lower_median_usd: Decimal | None
    upper_median_usd: Decimal | None


@dataclass(frozen=True)
class SuiteCostEstimate:
    lower_usd: Decimal | None
    upper_usd: Decimal | None
    missing_cost_canaries: tuple[str, ...]


@dataclass(frozen=True)
class CostAnalysis:
    current_agent: str
    configured_model: str
    reasoning_effort: str
    selected_canonical_model: str | None
    selected_rate_card_id: str | None
    per_canary: tuple[CanaryCostEstimate, ...]
    suite: SuiteCostEstimate
    selected_cohort_runs: int
    priceable_qualification_runs: int
    older_unpinned_runs: int
    unavailable_pricing_runs: int
    excluded_config_runs: int
    excluded_cohort_runs: int
    pricing_failures: tuple[PricingLoadFailure, ...]
    limitations: tuple[str, ...]
