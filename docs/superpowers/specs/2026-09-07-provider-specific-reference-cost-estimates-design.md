# Provider-Specific API-Equivalent Reference Cost Estimates

- **Batch:** #42
- **Status:** Delivered
- **Branch:** `feat/provider-cost-estimates`
- **Base:** `5800a814e295cf1126bca227bfb043438fd22c27`
- **Depends on:** Batch #40 normalized usage; Batch #41 read-only historical analytics

## 1. Decision summary

Batch #42 adds an offline, advisory monetary-reference layer over already observed qualification token usage. The user-facing entry point is a new argument-free command:

```bash
qualock cost
```

The number is an **API-equivalent public list-rate reference estimate in USD**, not an invoice, account charge, subscription allocation, or pass/fail signal. `qualock history` remains provider-neutral and its existing output/semantics must not change.

Each new `qualock check` best-effort writes a `pricing.json` sidecar beside the existing `report.json` and `qualification.json`. That sidecar pins the model provenance and exact bundled rate card selected when the qualification ran. Old artifacts are never rewritten or retroactively priced.

## 2. Goals

1. Estimate historical standard token-processing reference cost for current qualification canaries.
2. Preserve enough per-run pricing provenance that the same historical artifact remains reproducible after provider prices or aliases change.
3. Support exact dollar values when telemetry is sufficient, a lower/upper range when telemetry supports only bounded pricing, and `unavailable` otherwise.
4. Keep all runtime behavior offline and deterministic.
5. Keep monetary estimates completely outside qualification policy and token/attempt admission budgets.

## 3. Non-goals and protected surfaces

Batch #42 MUST NOT:

- change `qualification/policy.py`, `run/executor.py`, verdict semantics, canary admission/order, baseline stability, `max_attempts`, or `max_tokens`;
- introduce `--max-cost`, monetary gating, a cost budget, or automatic canary reordering;
- change `monitor`, `bisect`, GitHub PR qualification, release scheduling, source management, agent resolution, canary schema, or config schema;
- call provider APIs, billing/account APIs, pricing websites, or any network endpoint at runtime;
- claim to reproduce an actual invoice or subscription/seat consumption;
- add a database, analytics index, JSON CLI mode, artifact migration, or background refresh;
- retroactively infer provider/model identity for reports created before this batch.

The following remain explicitly out of the reference estimate when QuaLock lacks trustworthy telemetry at the required granularity: subscription or seat inclusion, credits, discounts, taxes, regional/data-residency multipliers, Batch/Priority/Fast modes, tool/search fees, OpenAI long-context request surcharges, Gemini cache-storage token-hours, external-service charges, and any other non-token or unobserved charge.

## 4. Architecture

New package:

```text
src/qualock/pricing/
    __init__.py
    models.py
    catalog.py
    resolve.py
    sidecar.py
    calculate.py
    analysis.py
    render.py
```

Existing integration points:

- `src/qualock/evidence/storage.py`: canonical qualification artifacts remain authoritative and unchanged; pricing sidecar is separate.
- `src/qualock/commands.py`: after canonical qualification artifacts are successfully written, a best-effort advisory sidecar step runs; add `execute_cost(root: Path) -> CostAnalysis`.
- `src/qualock/history/models.py` / `loader.py`: add optional cache/reasoning subsets needed by pricing without changing Batch #41 analysis semantics.
- `src/qualock/cli.py`: add argument-free `cost` command.

`pricing/` may depend on `history` normalized artifacts and config models. `history/` MUST NOT depend on `pricing/`, preserving the provider-neutral direction of dependency.

## 5. Historical usage extension

`HistoricalAttempt` gains these backward-compatible optional fields, all with `None` defaults so existing constructors remain source-compatible:

```python
cached_input_tokens: int | None = None
cache_write_input_tokens: int | None = None
reasoning_output_tokens: int | None = None
```

`_normalize_attempt()` reads them only from persisted `usage`. Missing, non-integer, boolean, or otherwise malformed values normalize to `None`. Existing `input_tokens`, `output_tokens`, and `usage_observed` behavior is unchanged. These numeric details alone are **not** sufficient evidence that a cache category was actually observed: Batch #40 intentionally normalizes some absent provider details to compatibility zero. Monetary trust therefore comes from the #42 pricing sidecar's per-attempt `usage_detail_trust`, not from an integer `0` in `report.json`.

**Invariant H1:** Batch #41 effectiveness eligibility must not read any new pricing field.

**Invariant H2:** Batch #41 runtime estimation must not read any new pricing field.

**Invariant H3:** Batch #41 provider-neutral token estimation remains exactly `input_tokens + output_tokens`; cache/reasoning subsets are informational and must not alter its totals or rendering.

## 6. Pricing sidecar schema

Each new qualification directory may contain `pricing.json`. Schema version 1 has exactly these logical fields:

```text
schema_version: 1
availability: "priced" | "unavailable"
basis: "api-equivalent-reference"
currency: "USD"
qualification_id: string
run_started_at: UTC ISO-8601 string
run_finished_at: UTC ISO-8601 string
agent: "codex" | "claude" | "antigravity"
provider: "openai" | "anthropic" | "google"
configured_model: string
reasoning_effort: string
canonical_model: string | null
model_identity_source: string
catalog_version: string
rate_card_id: string | null
source_url: string | null
source_checked_at: YYYY-MM-DD | null
effective_from: YYYY-MM-DD | null
effective_until: YYYY-MM-DD | null
rates_per_million: object | null
usage_detail_trust: list[object]
limitations: list[string]
unavailable_reason: string | null
```

`rates_per_million`, when present, contains decimal strings, never JSON floats:

```text
input_uncached
input_cached
cache_write_lower
cache_write_upper
output
```

Each `usage_detail_trust` entry has exactly:

```text
canary_id: string
side: "baseline" | "candidate"
repetition: positive integer
cached_input_tokens_trust: "observed" | "known_zero" | "unobserved"
cache_write_input_tokens_trust: "observed" | "known_zero" | "unobserved"
```

Entries MUST bind one-to-one to every started attempt in the owning qualification report by `(canary_id, side, repetition)`, with no duplicates or extras. `observed` means the provider runtime explicitly reported the category for the complete attempt; `known_zero` is allowed only where the provider protocol contract explicitly establishes zero; `unobserved` means no exact monetary inference is permitted from that category. Cache-read and cache-write must be protocol-established mutually exclusive partitions of total input before either can be trusted for pricing.

A rate component that is unsupported/not separately billed is `null`, not fabricated as zero. A rate-card ID identifies the **entire material snapshot**: provider, canonical model, source URL/check date, effective interval, all rates, and ordered limitations are immutable under that ID. Any change to any of those fields requires a new ID.
Fixed unavailable reasons written by sidecar generation are:

```text
unknown_model
missing_observed_model
inconsistent_observed_model
malformed_model_evidence
no_rate_card
invalid_capture_time
rate_boundary_crossed
```

The sidecar builder must return an `unavailable` payload for all expected resolution failures rather than raising. `qualification_id` MUST exactly equal the owning successfully loaded report ID; a mismatch is a malformed pricing sidecar, never silently rebound to another report.

**Invariant S0:** reports without `pricing.json` are older/unpinned history, not malformed qualification reports.

**Invariant S1:** a malformed `pricing.json` affects only monetary analysis. It must never make an otherwise valid `report.json` disappear from `qualock history`.

**Invariant S2:** sidecar contents are immutable evidence. Catalog upgrades never rewrite historical sidecars.

**Invariant S3:** sidecar creation occurs only after canonical `report.md`, `report.json`, and `qualification.json` have been written successfully. Pricing sidecar generation/write is a best-effort advisory boundary; any exception from this boundary is swallowed and MUST NOT change `qualock check` stdout, exit code, returned `QualificationResult`, or verdict.

**Invariant S4:** `pricing.json` is exclusive-create immutable evidence. The writer MUST never overwrite or update an existing sidecar; an existing-file error is swallowed by the same advisory boundary.

**Invariant S5 (component trust):** `usage_detail_trust` is pricing provenance, not qualification policy. For current protocols: Codex cache-read is `observed` only if every completed-turn usage object explicitly carries a valid non-negative `cached_input_tokens`; Codex cache-write is `unobserved` because no checked-in/runtime contract exposes that category. Claude cache-read is `observed` when its strict terminal usage parse succeeds because `cache_read_input_tokens` is required; Claude cache-write is `observed` only when `cache_creation_input_tokens` is explicitly present and valid, otherwise `unobserved`. Antigravity cache-read is `observed` after its strict terminal parse and cache-write is `known_zero` because Batch #40 explicitly pins the protocol as having no cache-write concept.

**Invariant S6 (atomic publication):** sidecar publication is no-replace and atomic with respect to the final path. The writer serializes the complete bytes to a temporary file in the qualification directory, flushes/fsyncs it, then publishes with a same-filesystem no-replace primitive such as `os.link(temp, pricing.json)`, and removes the temporary file. The final `pricing.json` must never become visible partially written; unsupported publication is an advisory writer failure.

## 7. Model provenance

Model identity resolution is provider-specific and fail-closed. Generic prefix/suffix stripping, fuzzy matching, edit-distance matching, or "closest model" lookup is forbidden.

Agent-to-provider mapping is exact and closed: `codex -> openai`, `claude -> anthropic`, `antigravity -> google`. No other mapping is inferred. Fixed successful identity-source values are `runtime_observed`, `configured_exact`, `documented_alias`, and `agent_exact_mapping`; expected failures use source `unavailable`.

### 7.1 Claude

Claude convenience aliases such as `sonnet` are priceable only when the completed qualification's raw `events_jsonl` yields a canonical runtime model ID. The pricing-only extractor accepts model observations only from exact paths `event["model"]` on `type="system", subtype="init"` events and `event["message"]["model"]` on `type="assistant"` events. Missing, null, empty, or whitespace-only model values are treated as absent. A present non-string model field is malformed. Any non-empty line that is invalid JSON or is not a JSON object produces `malformed_model_evidence`; valid unrelated event types are ignored. All non-empty observed model strings across all attempts must agree. A disagreement produces `inconsistent_observed_model`; no observed ID for an alias produces `missing_observed_model`. A consistent observed string not represented by an exact catalog canonical model produces `unknown_model`. Raw parser text is never copied into the sidecar or CLI output.

If `configured_model` is already an exact canonical catalog ID (for example `claude-sonnet-5`), it may resolve as `configured_exact` when no runtime model is present. If runtime observation is available, it is authoritative and the source becomes `runtime_observed`; it must agree with the configured exact ID or resolution fails closed.

### 7.2 Codex / OpenAI

Exact canonical OpenAI model IDs may resolve from configuration. Only aliases explicitly documented in the bundled catalog may resolve, for example `gpt-5.6` -> `gpt-5.6-sol`. Such resolution is recorded as `documented_alias`. Unknown or convenience-like IDs fail closed.

### 7.3 Antigravity / Gemini

Only explicit mapping-table entries are allowed. Initial mapping includes:

```text
gemini-3.8-flash-low    -> gemini-3.8-flash
gemini-3.8-flash-medium -> gemini-3.8-flash
gemini-3.8-flash-high   -> gemini-3.8-flash
```

The source is recorded as `agent_exact_mapping`. No generic removal of `-low`, `-medium`, or `-high` is allowed.

**Invariant P0:** canonical model identity is pinned at qualification time. `qualock cost` never re-resolves historical aliases using today's config/catalog.

**Invariant P1:** current config is used only to select relevant historical configured-model/effort records, never to invent provenance for an old report.

### 7.4 Usage-detail trust extraction

`build_usage_detail_trust(agent, result)` derives one `AttemptUsageTrust` record for every started attempt by inspecting that attempt's persisted raw `events_jsonl`; it never mutates or reinterprets canonical `Usage` totals. Repetition identities are 1-based, matching `paired_schedule()`.

- **Codex:** inspect every non-empty JSON object with `type="turn.completed"`. Cache-read is `observed` only when at least one completed turn exists and every completed turn has a `usage` object whose `cached_input_tokens` key is explicitly present as a non-boolean, non-negative integer. Missing/malformed detail on any completed turn makes cache-read `unobserved`. Cache-write is always `unobserved` in #42 because the current Codex contract exposes no cache-write wire key.
- **Claude:** inspect the unique terminal `type="result"` event's top-level `usage` object. Cache-read is `observed` only when the owning attempt has trustworthy usage and required `cache_read_input_tokens` is explicitly present/valid. Cache-write is `observed` only when `cache_creation_input_tokens` is explicitly present as a non-boolean, non-negative integer; absence is `unobserved`, never inferred zero.
- **Antigravity:** inspect the unique terminal `event="result"` event at `event["result"]["usage"]`. `cache_read_tokens` is required by the strict parser and therefore `observed` on a trustworthy attempt. Cache-write is `known_zero` under the explicit Batch #40 protocol contract.

Malformed/absent raw detail never upgrades to `known_zero`; it produces `unobserved` for that component. Pricing-trust extraction failures are local to the attempt/component and do not change qualification validity or `Usage.observed`. Raw parser text is never persisted in the trust record.

## 8. Bundled offline catalog

`catalog.py` exposes immutable `RateCard` records and `CATALOG_VERSION = "2026-09-07.1"`. Runtime selection uses only bundled data and the sidecar capture date; there is no web refresh.

Initial cards, all USD per 1M tokens and checked 2026-09-07:

| Rate card | Effective interval | Uncached input | Cached input | Cache write lower/upper | Output |
| --- | --- | ---: | ---: | ---: | ---: |
| `openai:gpt-5.6-terra:standard:2026-09-07` | 2026-09-07 onward | 2.00 | 0.20 | 2.50/2.50 | 12.00 |
| `openai:gpt-5.6-sol:standard:2026-09-07` | 2026-09-07 onward | 4.00 | 0.40 | 5.00/5.00 | 20.00 |
| `anthropic:claude-sonnet-5:standard-global:2026-09-07` | 2026-09-07 onward | 2.00 | 0.20 | 2.50/4.00 | 10.00 |
| `anthropic:claude-sonnet-4-6:standard-global:2026-09-07` | 2026-09-07 onward | 3.00 | 0.30 | 3.75/6.00 | 15.00 |
| `google:gemini-3.8-flash:standard:through-2026-12-31` | 2026-09-07 .. 2026-12-31 | 0.75 | 0.075 | null/null | 3.75 |
| `google:gemini-3.8-flash:standard:from-2027-01-01` | >= 2027-01-01 | 1.50 | 0.15 | null/null | 7.50 |

Official sources are pinned in each card:

- https://developers.openai.com/api/docs/models/gpt-5.6-terra
- https://developers.openai.com/api/docs/models/gpt-5.6-sol
- https://platform.claude.com/docs/en/about-claude/pricing
- https://platform.claude.com/docs/en/models/sonnet-4-6/overview
- https://ai.google.dev/gemini-api/docs/pricing

OpenAI's current model pages were rechecked on 2026-09-07 and publish the rates above plus `Cache writes are billed at 1.25x the uncached input token rate`; they do not publish a future effective-date transition for these cards, so QuaLock MUST NOT invent one. For all initial cards except Gemini's published 2027 transition, `2026-09-07` is a conservative QuaLock catalog applicability floor (the source-check date), not a claim that the provider changed price on that date. Gemini publishes an exact scheduled change on 2027-01-01, so two time-bounded cards are valid.
Each `RateCard` also carries fixed `limitations`. Initial limitations include:

- OpenAI: the pinned standard card includes the published 1.25x cache-write token rate. Long-context, tool/search, Batch/Priority/Flex/other serving modifiers remain excluded because current attempt telemetry does not preserve correct per-request modifier data.
- Anthropic: Batch, data residency, fast mode, server-side tool fees and subscription/seat economics are excluded. Cache-write TTL is not observed, so cache-write tokens create a bounded range.
- Gemini: explicit cache-storage token-hour fees, tools/search, tier discounts and regional/enterprise terms are excluded. Output rate already includes thinking tokens.

Catalog lookup:

```python
resolve_rate_card(
    provider: str,
    canonical_model: str,
    instant: datetime,
) -> RateCard | None
```

Effective dates are interpreted as UTC calendar dates; `effective_until` is inclusive. If multiple cards match an instant, that is a catalog-programming error caught by tests; runtime provenance generation fails closed to `no_rate_card` rather than selecting arbitrarily.

### 8.1 Semantic validation

Catalog construction and sidecar parsing use the same monetary validation rules. Every present rate string must parse to a **finite, non-negative** `Decimal`; `NaN`, infinities, signed negative values, booleans, and non-strings are invalid. `input_uncached` and `output` are required for a priced card. `cache_write_lower` and `cache_write_upper` are either both null or both finite/non-negative with lower <= upper. If both effective dates exist, `effective_from <= effective_until`.

Sidecar `basis` must equal `api-equivalent-reference`, `currency` must equal `USD`, and `agent`/`provider` must match the exact closed map in §7. `model_identity_source` must be exactly one of `runtime_observed`, `configured_exact`, `documented_alias`, `agent_exact_mapping`, or `unavailable`. For `unknown_model`, `missing_observed_model`, `inconsistent_observed_model`, `malformed_model_evidence`, and `invalid_capture_time`, `canonical_model` is null and `model_identity_source=unavailable`. For `no_rate_card` and `rate_boundary_crossed`, model resolution has succeeded, so `canonical_model` is non-null and `model_identity_source` retains its successful source. `run_started_at` and `run_finished_at` must parse as timezone-aware timestamps; readers normalize aware offsets to UTC and require start <= finish. Naive datetimes are invalid. A `priced` sidecar requires non-null canonical model, rate-card ID, source URL/check date, rates, and `unavailable_reason=null`. An `unavailable` sidecar requires `rates_per_million=null`, `rate_card_id=null`, `source_url=null`, `source_checked_at=null`, `effective_from=null`, and `effective_until=null`, an empty limitations list, plus one fixed unavailable reason; canonical model may remain non-null for `no_rate_card` or `rate_boundary_crossed`. A `priced` sidecar's provider/canonical-model/source/effective/rates/limitations fields must exactly form the pinned material snapshot identified by its `rate_card_id`. `catalog_version` is always a non-empty string. All `usage_detail_trust` identities must exactly cover the owning report's started attempts.

### 8.2 Temporal failure precedence

`build_pricing_payload` validates the run window before model/rate resolution. A naive timestamp, non-convertible timestamp, or `run_started_at > run_finished_at` yields `invalid_capture_time` and wins over all other expected unavailability reasons. Valid aware timestamps are converted to UTC before catalog lookup. `resolve_rate_card` itself accepts only aware datetimes; a naive direct call is a programmer error and raises `ValueError`, while `build_pricing_payload` converts the same defect into `invalid_capture_time`. After successful model resolution, resolve both endpoints: if neither endpoint matches a rate card, return `no_rate_card`; if exactly one endpoint matches, return `rate_boundary_crossed`; if both match different rate-card IDs, return `rate_boundary_crossed`; only the same non-null ID at both endpoints is priceable. Model-resolution failures occur after time validation but before rate lookup and keep their own fixed reason.

## 9. Monetary calculation

All arithmetic uses `decimal.Decimal`. The calculation relies on Batch #40's normalized contract that `input_tokens` is the total input count and cache-read/cache-write counts are informational subsets of that total. No binary float may represent rates, per-attempt money, medians, suite sums, or display-rounding inputs.

For one attempt, required trustworthy totals are `usage_observed=True` plus non-negative integer `input_tokens` and `output_tokens`. Cache details additionally require the matching `AttemptUsageTrust` entry. `cached_input_tokens` and `cache_write_input_tokens` must be non-negative integers **and** their trust states must be either `observed` or `known_zero`; `unobserved` makes the whole execution monetary-unpriceable even when the normalized numeric detail is `0`. A `known_zero` state additionally requires the persisted numeric value to equal `0`. Let:

```text
uncached_input = input_tokens - cached_input_tokens - cache_write_input_tokens
```

The two trusted cache categories are required to be mutually exclusive input partitions by the provider contract. `uncached_input < 0`, a missing/duplicate trust binding, an `unobserved` cache category, or any missing/invalid required counter makes that execution monetary-unpriceable. `reasoning_output_tokens`, when present, must be non-negative and <= `output_tokens`; it is informational only and is never added to cost.
The exact lower/upper formula is:

```text
lower = (
    uncached_input * input_uncached_rate
  + cached_input_tokens * input_cached_rate
  + cache_write_input_tokens * cache_write_lower_rate
  + output_tokens * output_rate
) / 1_000_000

upper = same formula using cache_write_upper_rate
```

If a token category is non-zero but the rate card has no corresponding rate, the execution is unpriceable. A zero category does not require a rate. When lower == upper, the sample is exact.

**Invariant C0:** cached input is a subset of total input and is never charged again at the uncached rate.

**Invariant C1:** cache-write input is a subset of total input and is never double-counted.

**Invariant C2:** reasoning/thinking output is a subset of output and is never double-counted. Gemini's published output rate explicitly includes thinking tokens.

**Invariant C3:** monetary eligibility has the same shared usable-pairing prerequisite as Batch #41: non-empty baseline/candidate repetition sets, identical sets, no duplicate `(side, repetition)` identities. It does **not** depend on `success`, `valid`, or `duration_ms`; resources can be consumed by invalid/failed attempts.

**Invariant C4:** every attempt in a started execution must be priceable for that execution to become a `CostSample`. No partial-execution dollar estimate is fabricated.

## 10. Analysis and cohort selection

`qualock cost` loads the current project only to obtain current canary IDs/order plus current `agent.name`, `model.effective_model`, and `reasoning_effort`. It scans existing qualification reports through the #41 loader, then reads pricing sidecars independently.
A sidecar is a **current-config candidate** only when its pinned `agent`, `configured_model`, and `reasoning_effort` exactly equal the current config values. Candidate sidecars are partitioned by exact `(canonical_model, rate_card_id)`; these partitions are monetary cohorts and MUST NOT be mixed.

Before cohorting, all valid sidecars sharing a `rate_card_id` must carry structurally equal parsed material rate-card snapshots: provider, canonical model, source URL/check date, effective interval, all Decimal rates, and ordered limitations. Any disagreement marks **all** sidecars using that ID as pricing failures with fixed reason `rate-card snapshot mismatch`; none may enter a cohort. Historical readers do not consult the bundled catalog to repair or choose among them.

Select the **latest observed model/rate cohort** by the greatest valid `run_finished_at` among remaining sidecars. If multiple cohorts share that timestamp, the **lexicographically smallest** `(canonical_model, rate_card_id)` wins. The UI MUST say "Latest observed model/rate cohort" and MUST NOT claim that a convenience alias currently resolves to that canonical model.

Every successfully loaded #41 report enters one primary pricing classification in this precedence order: missing sidecar -> `older/unpinned`; malformed sidecar or rate-card snapshot conflict -> pricing failure; valid sidecar with current-config mismatch -> `excluded_config`; valid current-config `availability="unavailable"` -> `unavailable_pricing`; valid current-config priced sidecar in a non-selected cohort -> `excluded_cohort`; valid current-config priced sidecar in the selected cohort -> `selected_cohort`. These primary categories are disjoint and exhaustive. `priceable_qualification_runs` is intentionally a derived subset of `selected_cohort_runs`, counting distinct selected-cohort qualification IDs that contribute at least one current-canary monetary sample.

Historical monetary calculation MUST use the rates pinned inside each valid `pricing.json`; `qualock cost` MUST NOT re-resolve a historical `rate_card_id` against the bundled catalog. Only currently configured canary IDs are actionable. Historical canaries removed from the current config do not enter per-canary estimates or the suite sum.

For each current canary in the selected cohort:

```text
lower_samples = all exact execution lower Decimal values
upper_samples = all exact execution upper Decimal values
lower_median = statistics-style median over Decimal values
upper_median = statistics-style median over Decimal values
```

Even sample counts use the arithmetic mean of the two middle Decimal values. No cent rounding occurs before median or suite summation.

Suite lower/upper are sums of the unrounded per-canary lower/upper medians only when every currently configured canary has >=1 monetary sample. Otherwise both suite values are unavailable and `missing_cost_canaries` preserves current config order.

## 11. Internal interfaces

Pin these frozen dataclasses (field order is implementation-authoritative):

```python
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
```

```python
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
```

```python
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
```

```python
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
```

`selected_cohort_runs` counts valid priced sidecars in the selected current-config cohort. `priceable_qualification_runs` counts distinct selected-cohort qualification IDs that contribute at least one current-canary `CostSample`. `older_unpinned_runs` counts all successfully loaded #41 reports without a sidecar because their original config cannot be reconstructed safely. `unavailable_pricing_runs` counts valid `availability="unavailable"` sidecars matching current config. `excluded_config_runs` counts valid sidecars not matching current agent/configured-model/reasoning-effort. `excluded_cohort_runs` counts priced current-config sidecars in non-selected canonical/rate cohorts.

Required functions:

```python
provider_for_agent(agent: str) -> str | None
build_usage_detail_trust(agent: str, result: QualificationResult) -> tuple[AttemptUsageTrust, ...]
resolve_model_identity(agent: str, configured_model: str, result: QualificationResult) -> ModelIdentity
resolve_rate_card(provider: str, canonical_model: str, instant: datetime) -> RateCard | None
build_pricing_payload(
    config: QualockConfig,
    result: QualificationResult,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> dict[str, object]
write_pricing_sidecar(qualification_dir: Path, payload: dict[str, object]) -> Path
scan_pricing(summary: HistorySummary) -> PricingHistory
price_execution(
    execution: HistoricalExecution,
    trust_by_identity: Mapping[tuple[str, str, int], AttemptUsageTrust],
    rates: RateComponents,
) -> CostSample | None
analyze_cost(
    summary: HistorySummary,
    pricing: PricingHistory,
    current_canary_ids: Sequence[str],
    *, agent: str, configured_model: str, reasoning_effort: str,
) -> CostAnalysis
render_cost_text(analysis: CostAnalysis) -> str
execute_cost(root: Path) -> CostAnalysis
```

`build_pricing_payload` always builds complete per-attempt usage-detail trust provenance first, including for expected `availability="unavailable"` outcomes. It resolves the rate card at both ends of the inclusive run window under §8.2 precedence. Both matched instants must select the same immutable `rate_card_id`; otherwise the fixed expected reason applies. The historical reader never calls `resolve_rate_card` for a valid priced sidecar.

## 12. Pricing-sidecar tolerance

`scan_pricing()` iterates only successfully loaded qualification reports from `HistorySummary.loaded`; it returns `PricingHistory.records`, `older_unpinned_qualification_ids`, and `failures`. Therefore project-protection `{"kind","result"}` artifacts remain silently excluded by the already-reviewed #41 loader.

For each loaded qualification directory:

- no `pricing.json` -> increment older/unpinned, no failure row;
- unreadable/non-UTF-8 -> fixed reason `unreadable pricing sidecar`;
- invalid JSON -> `invalid pricing JSON`;
- non-object -> `pricing sidecar is not a JSON object`;
- unsupported schema -> `unsupported pricing schema`;
- qualification ID mismatch with the owning loaded report -> `pricing qualification_id mismatch`;
- malformed typed fields/rates or any §8.1 semantic-validation failure -> `malformed pricing sidecar`;
- valid `availability=unavailable` -> parsed normally and counted unavailable, not malformed;
- any cross-file material snapshot disagreement under one `rate_card_id` -> every involved sidecar is removed from `records` and surfaced as `rate-card snapshot mismatch`.

Raw exception text, absolute paths, provider responses, credentials, and tracebacks are never rendered. Duplicate qualification semantics remain owned by #41; pricing sees only first-successfully-loaded qualification reports. The parser validates `usage_detail_trust` against the owning `HistoricalExecution` attempt identities after report loading; missing, duplicate, or extra trust identities make the sidecar `malformed pricing sidecar` rather than silently dropping monetary categories.

## 13. `qualock cost` CLI contract

The command has no arguments or flags:

```python
@app.command("cost")
def cost_command() -> None:
    ...
```

`execute_cost(root)` calls existing `load_project(root)`, rejects an empty current canary suite with `CommandError("no canaries found")`, scans `project_dir(root) / "results"`, loads sidecars, analyzes only current config/canaries, and performs no writes.

CLI error mapping follows `history`:

- `(ConfigError, CanaryLoadError, CommandError, ValueError)` -> message with `markup=False`, exit 3;
- unexpected `Exception` -> safe message with `markup=False`, exit 1;
- valid project with no history, no selected cohort, no priceable samples, or an unknown/unavailable model -> explanatory output and exit 0.

**Invariant R0:** cold-start `qualock cost` does not create `.qualock/results`.

**Invariant R1:** a real `qualock cost` invocation preserves bytes and `st_mtime_ns` of every existing qualification/pricing artifact.

**Invariant R2:** `qualock history` rendering and exit behavior are unchanged by #42.

## 14. Rendering and monetary rounding

Output begins exactly:

```text
QuaLock Reference Cost
```

When a cohort is selected:

```text
Latest observed model/rate cohort
- Model: <canonical_model>
- Rate card: <rate_card_id>

Typical complete qualification
About $1.42
```

Quantize lower and upper independently at the final display boundary. If the quantized cents differ, render `About $1.42–$1.55`; if an underlying raw range collapses to the same displayed cent, render the single cent value rather than the misleading `$1.42–$1.42`. Per-canary rows always follow current config order.

For a selected cohort with a complete suite estimate, render the exact/range `Typical complete qualification` amount. For a selected cohort with some samples but missing current canaries, render `Typical complete qualification` followed by `Unavailable: missing monetary history for <N> current canary/canaries`, then show available per-canary estimates and a `Missing current canaries` list. For a selected cohort with zero monetary samples, still show the selected model/rate cohort, render `Typical complete qualification` as `Unavailable`, and state `No trustworthy monetary samples are available for the current canaries in this cohort.`; all current canaries are listed as missing.

Every output renders a deterministic `History` block with all counts, including zeros:

```text
History
- Selected cohort runs: N
- Priceable matching runs: N
- Older/unpinned runs: N
- Unavailable pricing runs: N
- Excluded config runs: N
- Excluded older cohorts: N
- Ignored pricing sidecars: N
```

`Priceable matching runs` is the derived selected-cohort subset defined in §11, so it may be lower than `Selected cohort runs` without violating the primary partition. If `pricing_failures` is non-empty, append `Ignored pricing sidecars` rows as `- <qualification_id>: <fixed reason>` in qualification-ID lexical order. Never render absolute paths or raw exception text.

Always include:

```text
Basis
Public standard API list rates, USD.
Reference estimate, not your actual bill.
```

When a selected cohort exists, render the ordered limitations pinned in its validated rate-card snapshot, deduplicating by first occurrence. When no cohort is selected, omit rate-card-specific limitations but still render the generic Basis and History blocks. Historical rendering MUST NOT consult today's bundled catalog for limitation order or content.

Money is calculated and aggregated unrounded in `Decimal`. Display converts to cents only at the final formatting boundary using `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)`. Thousands are not abbreviated. Negative monetary values are impossible by validation.

When no selected cohort exists, render guidance such as:

```text
Reference cost unavailable.

No priced model/rate cohort with trustworthy pricing provenance is available
for the current configured agent/model/effort.

Run a normal qualification after pricing provenance is available;
QuaLock will preserve it for future estimates.
```

Do not call this an error and exit 0.

## 15. Writer integration

`execute_check()` captures `run_started_at = datetime.now(UTC)` immediately before `QualificationExecutor.run(...)`. After the executor returns and canonical qualification artifacts are successfully written, it captures `run_finished_at = datetime.now(UTC)` and passes the returned qualification directory plus the completed result/config/window to one isolated best-effort pricing function. That function resolves model/rates and publishes `pricing.json` through the atomic no-replace procedure in Invariant S6, serializing with `sort_keys=True`, `indent=2`, UTF-8, and a trailing newline. Clock capture itself must not enter executor/policy code.

No sidecar is written by standalone baseline creation. A normal check report already contains both baseline and candidate attempts required for monetary sampling. `write_qualification_artifacts` has only one production call site, inside `execute_check`; therefore every production flow that delegates to `execute_check` receives the same best-effort sidecar attempt without modifying those callers.
The best-effort boundary MUST catch every exception raised by pricing provenance generation or sidecar writing and return without modifying the already-written qualification artifacts or CLI result. This isolation is intentional because monetary evidence is advisory. Tests must inject a writer failure and prove `execute_check` still returns the same `QualificationResult` and artifact set except for the absent sidecar.

A valid unknown-model case is not an exception: it writes `availability="unavailable"` with a fixed reason when sidecar I/O succeeds.

## 16. Source and honesty contract

The catalog represents standard public API list-rate equivalents. It does not claim the coding-agent CLI actually bills through that API path. This distinction is mandatory for Claude Code subscription automation, Codex account/subscription modes, and Antigravity authentication where the user's real commercial arrangement may differ.

The sidecar pins source URLs and rate data for reproducibility, but `qualock cost` never fetches those URLs. Updating catalog rates requires a normal QuaLock code/release change with tests and a new immutable rate-card ID when any rate/effective interval changes.

OpenAI GPT-5.6 Terra/Sol model pages were checked on 2026-09-07. The bundled cards pin the currently published standard token rates and 1.25x cache-write multiplier; because those pages do not publish a future rate transition, the cards have no invented expiry. A later documented price change requires a new immutable card.

Gemini 3.8 Flash is different: Google publishes exact rates through 2026-12-31 and exact new rates starting 2027-01-01, so capture-date lookup deterministically chooses the corresponding immutable card.

## 17. TDD obligations

At minimum, tests must pin all of the following:

1. Pre-#42 qualification report with no `pricing.json` is older/unpinned and monetary-unavailable, never retroactively inferred.
2. Missing `pricing.json` does not make `qualock history` ignore a report.
3. Malformed pricing JSON affects cost only; fixed non-sensitive failure reason.
4. Non-UTF-8 pricing sidecar is tolerated and siblings still analyze.
5. Claude configured alias `sonnet` resolves from consistent runtime-observed canonical model.
6. Claude alias with no observed model fails closed.
7. Conflicting Claude observed model IDs fail closed.
8. Claude exact configured canonical ID is accepted only if any observed ID agrees.
9. OpenAI exact canonical ID resolves.
10. Provider-documented `gpt-5.6` exact alias resolves only to `gpt-5.6-sol`.
11. Unknown OpenAI alias/model fails closed; no prefix/fuzzy matching.
12. Antigravity three explicit Gemini 3.8 Flash effort-suffixed IDs map exactly.
13. An unlisted Antigravity suffix/name fails closed; no generic suffix stripping.
14. Catalog rate-card IDs are unique and immutable in test fixtures.
15. Gemini capture date 2026-12-31 selects old card; 2027-01-01 selects new card.
16. OpenAI Terra/Sol cards pin the published 1.25x cache-write rate and have no fabricated future price-transition date.
17. Decimal-only exact arithmetic for an attempt whose cache-read/cache-write categories are both trust-proven zero.
18. Cached input is subtracted from uncached input and not double-counted.
19. Cache-write input is subtracted from uncached input and not double-counted.
20. Reasoning/thinking tokens are not double-counted.
21. Negative or inconsistent token subsets make the execution unpriceable.
22. `usage_observed=False` makes monetary sample unavailable.
23. Non-zero token category with missing required rate makes monetary sample unavailable.
24. Claude cache writes produce correct 5m/1h lower-upper range.
25. Claude sample with explicitly reported `cache_creation_input_tokens=0` is exact even though the card supports a range; an absent cache-creation field is unpriceable under #42 trust rules.
26. Failed/invalid success outcomes do not block an otherwise priceable monetary sample.
27. Malformed duration does not block an otherwise priceable monetary sample.
28. Skipped canary (`attempts=()`) is not a monetary sample.
29. Incomplete/mismatched repetition pairing is not a monetary sample.
30. Current config agent/model/effort exact matching is required before cohorting.
31. Different canonical models are never mixed.
32. Different rate-card IDs are never mixed.
33. Latest observed cohort selection is deterministic by `run_finished_at` + lexical tie-break.
34. Removed historical canaries do not enter current estimates.
35. Per-canary medians use Decimal and no pre-rounding.
36. Even-count median is arithmetic mean of middle Decimal values.
37. Suite range is sum of unrounded per-canary medians.
38. Missing one current canary makes the complete suite estimate unavailable.
39. Missing-canary list preserves current config order.
40. Display rounds only at cents boundary with `ROUND_HALF_EVEN`.
41. Exact sample renders one amount; a range with distinct rounded endpoints renders lower–upper; a sub-cent range whose rounded endpoints are equal renders one amount.
42. Required basis/not-actual-bill framing is always present.
43. Catalog limitations are rendered deterministically and deduplicated.
44. Zero history exits 0 and does not create `results/`.
45. No matching priced cohort exits 0 with guidance.
46. Empty current canary suite raises exact `CommandError("no canaries found")` and CLI exits 3.
47. Config/canary load errors follow history-style exit 3 mapping.
48. Real `qualock cost` preserves bytes and mtimes of all result artifacts.
49. Mixed project-protection `report.json` artifacts remain silently excluded by #41 scan logic.
50. Pricing writer failure cannot change check verdict/result/output/exit behavior.
51. Unknown-model check still writes an unavailable sidecar when I/O succeeds.
52. Sidecar rates serialize as decimal strings, never JSON floats.
53. Sidecar capture is immutable; updating catalog code does not rewrite old files.
54. `qualock history` golden/public output is unchanged.
55. Batch #41 effectiveness eligibility, runtime estimates, token samples, medians, suite availability, ordering, and rendered output remain value-identical. The existing structural assertion that `HistoricalAttempt` lacks cache/reasoning fields is intentionally re-baselined: new tests assert the optional fields exist with `None` defaults and that #41 analysis never reads them.
56. No pricing package runtime code performs HTTP/network/provider calls.
57. No `--max-cost`, cost budget, JSON mode, or pricing option appears in CLI help.
58. Protected policy/executor/monitor/bisect/PR/scheduler/source/config-schema/canary-schema files remain unchanged.
59. Windows path-neutral sidecar discovery and read-only invocation pass.
60. Linux Python 3.11/3.12/3.13 and Windows CI all pass.
61. Sidecar `qualification_id` mismatch is a fixed pricing failure and cannot rebind to another report.
62. Exact closed agent-to-provider mapping is pinned; unknown agent cannot infer a provider.
63. A run whose start/end instants resolve to different rate cards writes `rate_boundary_crossed` and no monetary rates.
64. Historical calculation uses the sidecar's pinned rates even if the bundled catalog later contains different rates for the same model.
65. Existing `pricing.json` is never overwritten; exclusive-create failure remains advisory and cannot change qualification behavior.
66. Malformed Claude runtime model evidence fails closed to fixed `malformed_model_evidence` without raw parser leakage.
67. Initial catalog cards do not resolve before their conservative 2026-09-07 applicability floor; effective-until dates are UTC-date inclusive.
68. Codex cache-read trust is `observed` only when every completed turn explicitly reports a valid detail; absent/malformed detail is `unobserved`, and Codex cache-write is always `unobserved` under the current protocol.
69. Claude cache-read is `observed` on a trustworthy terminal result; cache-write is `observed` only when `cache_creation_input_tokens` is explicitly present/valid and is `unobserved` when absent.
70. Antigravity cache-read is `observed` and cache-write is `known_zero`; a `known_zero` trust record with a nonzero persisted numeric value is rejected for monetary use.
71. Missing, duplicate, or extra `usage_detail_trust` attempt identities make the sidecar malformed; trust identity repetition is 1-based.
72. An `unobserved` cache category makes an execution monetary-unpriceable even when its normalized report value is numeric zero.
73. Sidecar semantic validation pins exact basis/currency, agent/provider agreement, model-identity-source/reason consistency, catalog-version shape, and complete mutually exclusive `priced` versus `unavailable` nullability rules.
74. Sidecar/catalog rates reject NaN, infinities, signed negatives, non-string values, and cache-write lower > upper.
75. Sidecar timestamps require aware datetimes, normalize offsets to UTC, reject naive values, and reject start > finish; effective intervals reject from > until.
76. Temporal precedence is exact: invalid window -> `invalid_capture_time`; both endpoints unmatched -> `no_rate_card`; exactly one unmatched -> `rate_boundary_crossed`; different endpoint card IDs -> `rate_boundary_crossed`; same ID -> priceable.
77. Sidecars sharing a rate-card ID but differing in any parsed material snapshot field all become fixed `rate-card snapshot mismatch` pricing failures and cannot enter cohorts.
78. Same latest `run_finished_at` across cohorts deterministically selects the lexicographically smallest `(canonical_model, rate_card_id)`.
79. Primary report classifications are disjoint/exhaustive and all counters are pinned; one selected run contributing samples to multiple canaries increments `priceable_qualification_runs` only once.
80. Renderer covers complete selected suite, partial selected suite, and selected cohort with zero samples, and always prints every History counter including zeros.
81. Pricing failures render only lexical `<qualification_id>: <fixed reason>` rows; absolute paths and raw exception text never appear.
82. Sidecar publication is atomic/no-replace: successful publish exposes complete bytes, an existing destination is unchanged, and in-process failures clean temporary files without exposing partial `pricing.json`.
83. No-selected-cohort guidance remains truthful when pinned `availability=unavailable` sidecars exist and exits 0.
84. Claude model extraction pins exact system-init and assistant-message paths, treats missing/empty values as absent, present non-string values as malformed, unknown consistent strings as `unknown_model`, and malformed non-empty JSONL as `malformed_model_evidence`.
85. Historical selected-cohort limitations and rates come solely from the validated pinned snapshot; changing today's bundled catalog cannot change old cost output.
86. `build_usage_detail_trust` emits exactly one trust record for every started report attempt, including invalid/failed attempts; skipped canaries with `attempts=()` emit none.
87. A valid unavailable sidecar still carries complete attempt trust provenance, but trust data cannot turn an unavailable rate/model sidecar into a priced cohort.

## 18. Verification gates

The implementation plan must preserve the repository's current strict static baselines:

- fresh full pytest on exact branch heads;
- compileall over `src` and tests;
- strict mypy may contain only the exact pre-existing three PyYAML `import-untyped` findings in `config/io.py`, `canary/loader.py`, and `project_setup/config.py` unless base changes before implementation;
- every Batch #42 changed Python file is Ruff-clean;
- full-tree Ruff diagnostics must introduce no debt relative to the exact Batch #42 base;
- `git diff --check` clean;
- protected-scope diff empty;
- independent task review after each implementation task;
- whole-implementation review before first push;
- Linux + Windows CI green before documentation is marked delivered;
- docs-inclusive exact-head gates + CI + final whole-branch review before merge.

Any base movement before implementation requires recomputing, not blindly reusing, these baseline waivers.

## 19. Documentation and roadmap gate

Only after implementation-head CI is green:

- README may document `qualock cost`, clearly labeled API-equivalent standard list-rate reference and not actual billing;
- ROADMAP moves #42 from pending to delivered without implying monetary gating or invoice reconciliation;
- this spec status may change from Proposed to Delivered.

No release/tag/package publish is part of Batch #42.