# Provider-Specific API-Equivalent Reference Cost Estimates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline, advisory `qualock cost` command and immutable per-check pricing provenance that estimate current-suite token-processing cost at pinned public API-equivalent USD list rates without changing qualification behavior.

**Architecture:** Extend Batch #41's normalized history attempts with informational usage subsets, then add a one-way `qualock.pricing` package containing frozen models, a bundled catalog, fail-closed model/rate resolution, immutable sidecar I/O, Decimal calculation, cohort analysis, and deterministic rendering. `execute_check` invokes sidecar capture only after canonical artifacts succeed and inside a catch-all advisory boundary; `execute_cost` reads history and pinned sidecars without writing or consulting current catalog data for historical prices. Qualification policy/execution and provider-neutral `qualock history` remain unchanged.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, `datetime`, `decimal`, `json`, `os.link`, `pathlib`, `statistics`, existing Pydantic config models, Typer/Rich CLI, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md` at canonical commit `4000ab175ab15eff7ec7ae0f68fe76a4b3abf43b`

## Global Constraints

- Exact Batch #42 merge/base remains `5800a814e295cf1126bca227bfb043438fd22c27`; the approved spec commit is `4000ab175ab15eff7ec7ae0f68fe76a4b3abf43b` on branch `feat/provider-cost-estimates`. Here "base" means the locked feature merge-base, not the advancing branch HEAD. If that merge-base is intentionally changed, stop, record the new base, recompute every baseline waiver, and rerun the exact-base gates instead of reusing these results.
- Resume checkpoint: plan commit `4074cac53f670a24cca3f111493423f72e8ee66a` and Task 1 implementation commit `9886108ecff1f5f27d37d5786bb284f02266d347` already exist on top of the locked base. Task 1 has a genuine RED/GREEN report but still needs its independent review; do not replay its implementation.
- The estimate is an **API-equivalent public standard list-rate reference in USD**, never an invoice, account charge, subscription/seat allocation, credit/discount/tax calculation, cost gate, pass/fail signal, or actual-bill claim.
- All runtime behavior is offline and deterministic. Pricing code must not import or call HTTP clients, provider APIs, billing APIs, pricing websites, background refreshes, databases, or analytics indexes.
- `pricing.json` is separate immutable evidence. It is created only after `report.md`, `report.json`, and `qualification.json` succeed, is atomically published with no replacement, and is never migrated, refreshed, or retroactively created for old reports.
- Sidecar generation/writing is best effort: every exception is swallowed at one isolated boundary and cannot change `qualock check` stdout, exit code, `QualificationResult`, verdict, canonical artifacts, budgets, or canary ordering.
- `qualock cost` is argument-free, read-only, and zero-write. Missing history, no matching cohort, no priceable samples, and valid unavailable sidecars exit `0`; an empty current suite is exactly `CommandError("no canaries found")` and CLI exit `3`.
- `qualock history` remains provider-neutral and value-identical. Its effectiveness/runtime logic never reads the new fields, and token totals remain exactly `input_tokens + output_tokens`.
- `history` must not depend on `pricing`; dependency direction is `pricing -> history`. Do not change `src/qualock/qualification/policy.py`, `src/qualock/run/executor.py`, baseline semantics, monitor/watch, bisect, GitHub PR qualification, scheduler, source management, agent resolution, canary schema, or config schema.
- Do not add `--max-cost`, a monetary budget, JSON mode, pricing flags, automatic canary reordering, artifact migration, or background refresh.
- `CATALOG_VERSION` is exactly `"2026-09-07.1"`; all rates are USD per 1M tokens stored and serialized as decimal strings, and all money remains `Decimal` until final `ROUND_HALF_EVEN` cent rendering.
- Rate-card IDs identify the complete immutable material snapshot: provider, canonical model, source URL/check date, inclusive UTC effective interval, all rates, and ordered limitations. Historical analysis uses only that validated pinned snapshot and never today's catalog.
- The agent/provider map is closed and exact: `codex -> openai`, `claude -> anthropic`, `antigravity -> google`. No fuzzy matching, edit distance, generic prefix/suffix stripping, or closest-model inference is allowed.
- A trust record exists for every started attempt, including invalid/failed attempts, and no skipped attempt. Cache-read/cache-write numeric zeros are not evidence: monetary use also requires matching `observed` or protocol-established `known_zero` trust.
- Do not modify `pyproject.toml` and do not add `types-PyYAML`. Strict mypy may contain only the exact three pre-existing PyYAML `import-untyped` findings at `config/io.py:3`, `canary/loader.py:4`, and `project_setup/config.py:6` unless the implementation base moves.
- Every Batch #42 changed Python file must be Ruff-clean. Full-tree Ruff may introduce no diagnostic relative to exact base `5800a814e295cf1126bca227bfb043438fd22c27`; do not spend this batch fixing unrelated debt.
- Fresh full pytest, `compileall -q src tests`, `git diff --check`, protected-scope diff, exact-head identity, and Linux 3.11/3.12/3.13 plus Windows CI gates are mandatory.
- Use Sonnet medium for fresh implementers and independent reviewers on Tasks 1, 4, and 6; Sonnet high for fresh implementers and independent reviewers on Tasks 2, 3, and 5; Sonnet high for the pre-push whole-implementation review. Use Opus high exactly once, only for the final docs-inclusive whole-branch review.
- Codex is fallback only when the required Claude model is hard-limited. Record one nearest-effort substitution in the SDD ledger and do not launch a duplicate worker/reviewer. Opus fallback is one Codex high review, not an Opus retry plus Codex.
- Task 1 already has its original implementation commit and must not be re-dispatched; it still requires one fresh independent Sonnet medium reviewer. Tasks 2-6 use one fresh implementer and a different independent reviewer each. Never run branch-writing implementers in parallel; reviewers are read-only, and Critical/Important findings return to that task's implementer/fixer for a scoped fix and re-review.
- Run every command below from `/home/pacmap/qualock-provider-costs`; use `/home/pacmap/qualock-easy/.venv/bin/python`, `/home/pacmap/qualock-easy/.venv/bin/ruff`, and `/home/pacmap/qualock-easy/.venv/bin/mypy` exactly.

---

## File Structure

- Modify `src/qualock/history/models.py` and `src/qualock/history/loader.py`: optional normalized usage details only; preserve Batch #41 semantics.
- Create `src/qualock/pricing/__init__.py`: re-export the pricing interfaces consumed by commands and tests.
- Create `src/qualock/pricing/models.py`: frozen catalog, provenance, scanner, sample, estimate, and analysis dataclasses plus shared monetary validation.
- Create `src/qualock/pricing/catalog.py`: immutable bundled rate cards, catalog version, and inclusive temporal lookup.
- Create `src/qualock/pricing/resolve.py`: closed provider mapping, exact model evidence resolution, and protocol-specific attempt trust.
- Create `src/qualock/pricing/sidecar.py`: payload construction, atomic publication, tolerant parsing, report binding, and snapshot consistency.
- Create `src/qualock/pricing/calculate.py`: pure per-execution Decimal pricing with trust and pairing gates.
- Create `src/qualock/pricing/analysis.py`: current-config classification, exact cohort selection, Decimal medians, counters, and suite aggregation.
- Create `src/qualock/pricing/render.py`: deterministic text, final-cent rounding only, history/basis/limitations, and fixed safe failures.
- Modify `src/qualock/commands.py`: post-artifact best-effort capture and read-only `execute_cost` composition.
- Modify `src/qualock/cli.py`: exact argument-free `cost` command and history-style exit mapping.
- Test through `tests/unit/test_history_{loader,analysis,render}.py`, eight new `tests/unit/test_pricing_*.py` modules named in Tasks 2-6, and existing `tests/unit/test_commands.py` / `tests/unit/test_cli.py`.
- Modify `README.md`, `ROADMAP.md`, and the canonical spec status only after implementation-head CI is green in Task 7.

### Task 1: Reconcile and Independently Review the Implemented Historical Usage Extension

**Resume state:** Task 1 implementation already exists at exact commit `9886108ecff1f5f27d37d5786bb284f02266d347`, whose parent is plan commit `4074cac53f670a24cca3f111493423f72e8ee66a`. The original Task 1 report at `.superpowers/sdd/2026-09-08-provider-specific-reference-cost-estimates/task-1-report.md` records genuine RED (`2 failed, 58 passed`) before the loader implementation and GREEN (`73 passed`) after it. Do not replay RED or dispatch a duplicate implementer; the remaining gate is an independent review plus the normal fix loop if that review finds a blocking gap.

**Files in commit `9886108`:**
- Modified: `src/qualock/history/models.py`
- Modified: `src/qualock/history/loader.py`
- Modified: `tests/unit/test_history_loader.py`
- Modified: `tests/unit/test_history_analysis.py`
- Regression-only: `tests/unit/test_history_render.py` remains unchanged by the implementation and is rerun to prove public history rendering stays provider-neutral.

**Interfaces:**
- Consumes: persisted `report.json` attempt `usage` objects already loaded by `scan_results(results_dir: Path) -> HistorySummary`.
- Produces: `HistoricalAttempt.cached_input_tokens: int | None = None`, `cache_write_input_tokens: int | None = None`, and `reasoning_output_tokens: int | None = None`, appended after `usage_observed`.
- Preserves: `analyze_history(summary: HistorySummary, current_canary_ids: Sequence[str]) -> HistoryAnalysis` and `render_history_text(analysis: HistoryAnalysis) -> str` behavior.
- Later pricing tasks may consume the normalized numeric fields, but monetary trust must come only from `pricing.json` provenance.

**Worker budget:** The original Task 1 implementer commit is the implementation provenance. Use one fresh independent Sonnet medium reviewer now. If the review finds Critical/Important issues, resume that implementer when possible; if the original worker is unavailable, dispatch one fresh Sonnet medium fixer under the same Task 1 brief/report and use a scoped re-review.

- [ ] **Step 1: Verify the exact Task 1 commit, ancestry, and scope**

```bash
test "$(git rev-parse 9886108ecff1f5f27d37d5786bb284f02266d347^)" = "4074cac53f670a24cca3f111493423f72e8ee66a"
git diff --name-only 4074cac53f670a24cca3f111493423f72e8ee66a..9886108ecff1f5f27d37d5786bb284f02266d347
git show --check --oneline --stat 9886108ecff1f5f27d37d5786bb284f02266d347
```
Expected: the parent is exactly the plan commit; the diff lists only `src/qualock/history/models.py`, `src/qualock/history/loader.py`, `tests/unit/test_history_analysis.py`, and `tests/unit/test_history_loader.py`; `git show --check` is clean.

- [ ] **Step 2: Verify the recorded TDD evidence and rerun Task 1 GREEN**

Read the existing report before review. It records loader RED failures because the new fields normalized to `None`, followed by the minimal loader change. Then rerun:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history/models.py src/qualock/history/loader.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/history
git diff --check 4074cac53f670a24cca3f111493423f72e8ee66a..9886108ecff1f5f27d37d5786bb284f02266d347
```

Expected: the focused history suites PASS, Ruff/compileall/diff-check exit `0`, and the following existing tests remain the direct behavioral evidence:
- `test_historical_attempt_new_fields_default_to_none_with_old_argument_set`
- `test_new_usage_detail_fields_normalize_from_usage_object`
- `test_new_usage_detail_fields_normalize_independently_and_reject_bools`
- `test_missing_usage_detail_keys_and_malformed_usage_yield_none_for_all_three`
- `test_cache_and_reasoning_fields_default_to_none_and_cannot_affect_token_totals`
- `test_wildly_different_cache_and_reasoning_values_do_not_change_41_results`

- [ ] **Step 3: Run the independent Task 1 review**

Generate the review package over exact range `4074cac53f670a24cca3f111493423f72e8ee66a..9886108ecff1f5f27d37d5786bb284f02266d347`. Give the fresh read-only Sonnet medium reviewer the original Task 1 brief, Task 1 report, review package, Global Constraints, and Spec §5 plus §17 obligations 54-55. Require both spec-compliance and task-quality verdicts.
Reviewer must verify: the three fields are additive `None`-defaulted data only; malformed/bool usage details normalize to `None`; old constructors remain compatible; `history/analysis.py` and `history/render.py` do not read the new fields; Batch #41 effectiveness/runtime/token values remain unchanged; token totals remain exactly `input_tokens + output_tokens`; numeric zero is not treated as monetary trust.

- [ ] **Step 4: Resolve Task 1 review findings through the SDD fix loop**

If the reviewer returns spec ❌ or any Critical/Important finding, hand the exact findings to the Task 1 implementer/fixer, require a focused failing regression when applicable, rerun Step 2 after the fix, commit only the scoped Task 1 fix, generate a fix-range review package, and obtain one scoped re-review. Record Minor findings in the ledger for final triage. The controller does not write production fixes.

- [ ] **Step 5: Mark Task 1 complete in the ledger**

When the independent review is clean, append:

```text
Task 1: complete (commits 4074cac..9886108, review clean)
```

If a reviewed fix commit was required, replace the ending SHA with the reviewed Task 1 fix head and record the fix-round line first. No duplicate Task 1 implementation commit is created merely to match the refined plan wording.

### Task 2: Add Pricing Models, Catalog, Exact Model Resolution, and Temporal Validation

**Files:**
- Create: `src/qualock/pricing/__init__.py`
- Create: `src/qualock/pricing/models.py`
- Create: `src/qualock/pricing/catalog.py`
- Create: `src/qualock/pricing/resolve.py`
- Create: `tests/unit/test_pricing_catalog.py`
- Create: `tests/unit/test_pricing_resolve.py`

**Interfaces:**
- Consumes: `QualificationResult`, each `AttemptResult.events_jsonl`, and aware capture instants.
- Produces: all frozen dataclasses in Spec §11 with exact field order.
- Produces: `parse_rate_components(raw: object) -> RateComponents`, `validate_effective_interval(effective_from: date | None, effective_until: date | None) -> None`, `provider_for_agent(agent: str) -> str | None`, `resolve_model_identity(agent: str, configured_model: str, result: QualificationResult) -> ModelIdentity`, and `resolve_rate_card(provider: str, canonical_model: str, instant: datetime) -> RateCard | None`.
- Produces: `CATALOG_VERSION = "2026-09-07.1"`, immutable `RATE_CARDS: tuple[RateCard, ...]`, exact alias `gpt-5.6 -> gpt-5.6-sol`, and only the three specified Antigravity mappings.

**Worker budget:** Fresh Sonnet high implementer; different Sonnet high reviewer; sequential branch writing only.

- [ ] **Step 1: Write RED catalog and shared validation tests**

Add to `test_pricing_catalog.py`: `test_catalog_version_and_rate_card_ids_are_unique`, `test_rate_cards_are_frozen_material_snapshots`, `test_openai_cards_pin_standard_rates_cache_write_multiplier_and_no_expiry`, `test_anthropic_cards_pin_global_rates_and_cache_write_range`, `test_gemini_boundary_selects_old_then_new_card`, `test_initial_cards_do_not_resolve_before_applicability_floor`, `test_effective_until_is_utc_date_inclusive`, `test_overlapping_rate_cards_are_rejected_by_catalog_validation`, `test_resolve_rate_card_rejects_naive_datetime`, `test_rate_parser_accepts_only_finite_nonnegative_decimal_strings`, `test_cache_write_rates_are_both_null_or_ordered`, `test_effective_interval_rejects_from_after_until`, and `test_pricing_runtime_has_no_network_client_imports`.

```python
def test_gemini_boundary_selects_old_then_new_card() -> None:
    old = resolve_rate_card(
        "google",
        "gemini-3.8-flash",
        datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
    )
    new = resolve_rate_card("google", "gemini-3.8-flash", datetime(2027, 1, 1, tzinfo=UTC))
    assert old is not None
    assert old.rate_card_id == "google:gemini-3.8-flash:standard:through-2026-12-31"
    assert new is not None
    assert new.rate_card_id == "google:gemini-3.8-flash:standard:from-2027-01-01"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-0.01", -1, True])
def test_rate_parser_accepts_only_finite_nonnegative_decimal_strings(value: object) -> None:
    raw = {
        "input_uncached": value,
        "input_cached": "0.20",
        "cache_write_lower": "2.50",
        "cache_write_upper": "2.50",
        "output": "12.00",
    }
    with pytest.raises(ValueError):
        parse_rate_components(raw)
```

The network test parses every `src/qualock/pricing/*.py` with `ast` and rejects import roots in `{"httpx", "requests", "urllib", "socket", "aiohttp"}`.

- [ ] **Step 2: Write RED exact model-resolution tests**

Define this local factory in `test_pricing_resolve.py`:

```python
def qualification_with_events(*events_jsonl: str) -> QualificationResult:
    attempts = tuple(
        AttemptResult(
            side="baseline",
            repetition=index,
            success=True,
            valid=True,
            duration_ms=1,
            events_jsonl=events,
        )
        for index, events in enumerate(events_jsonl, 1)
    )
    execution = CanaryExecution(
        "canary-a",
        True,
        "sha256:test",
        attempts,
        len(attempts),
        0,
        len(attempts),
        0,
        Verdict.PASS,
        "test",
    )
    return QualificationResult("q-1", "1.0.0", "1.0.1", Verdict.PASS, (execution,), (), ())
```

Add `test_provider_for_agent_is_exact_and_closed`, `test_claude_alias_resolves_consistent_runtime_model`, `test_claude_alias_without_observation_fails_closed`, `test_claude_conflicting_observations_fail_closed`, `test_claude_exact_config_requires_observed_agreement`, `test_claude_model_paths_missing_and_empty_are_absent`, `test_claude_non_string_model_is_malformed`, `test_claude_malformed_or_non_object_jsonl_is_malformed`, `test_claude_unrelated_events_are_ignored`, `test_claude_unknown_consistent_runtime_model_is_unknown`, `test_openai_exact_canonical_models_resolve`, `test_openai_documented_alias_resolves_only_to_sol`, `test_openai_unknown_and_convenience_names_do_not_fuzzy_match`, `test_antigravity_three_explicit_effort_ids_map`, and `test_antigravity_unlisted_suffix_does_not_resolve`.

```python
assert resolve_model_identity(
    "claude",
    "sonnet",
    qualification_with_events('{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'),
) == ModelIdentity("claude-sonnet-5", "runtime_observed", None)
```

- [ ] **Step 3: Run Task 2 tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
```

Expected: collection FAIL with `ModuleNotFoundError: No module named 'qualock.pricing'`.

- [ ] **Step 4: Implement all frozen dataclasses and shared validators**

Define `RateComponents`, `RateCard`, `ModelIdentity`, `AttemptUsageTrust`, `PricingSidecar`, `PricingLoadFailure`, `PricingHistory`, `CostSample`, `CanaryCostEstimate`, `SuiteCostEstimate`, and `CostAnalysis` exactly in Spec §11 field order. The first declarations are:

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
```

`parse_rate_components` requires exactly five keys, rejects non-string present values before `Decimal`, requires finite/non-negative input/output, and enforces paired ordered cache-write bounds. Validate date order.

- [ ] **Step 5: Implement exact catalog and model resolution**

Define all six cards from Spec §8 with exact IDs, intervals, values, URLs, checked date, and ordered limitations. Validate unique IDs and no overlapping model/provider intervals at module construction. Lookup normalizes aware offsets to UTC and uses inclusive UTC dates; naive direct calls raise `ValueError`.

| ID | UTC dates | Input | Cached | Write lower/upper | Output |
| --- | --- | ---: | ---: | ---: | ---: |
| `openai:gpt-5.6-terra:standard:2026-09-07` | `2026-09-07` onward | `2.00` | `0.20` | `2.50/2.50` | `12.00` |
| `openai:gpt-5.6-sol:standard:2026-09-07` | `2026-09-07` onward | `4.00` | `0.40` | `5.00/5.00` | `20.00` |
| `anthropic:claude-sonnet-5:standard-global:2026-09-07` | `2026-09-07` onward | `2.00` | `0.20` | `2.50/4.00` | `10.00` |
| `anthropic:claude-sonnet-4-6:standard-global:2026-09-07` | `2026-09-07` onward | `3.00` | `0.30` | `3.75/6.00` | `15.00` |
| `google:gemini-3.8-flash:standard:through-2026-12-31` | `2026-09-07..2026-12-31` | `0.75` | `0.075` | `null/null` | `3.75` |
| `google:gemini-3.8-flash:standard:from-2027-01-01` | `2027-01-01` onward | `1.50` | `0.15` | `null/null` | `7.50` |

Pin sources exactly to `https://developers.openai.com/api/docs/models/gpt-5.6-terra`, `https://developers.openai.com/api/docs/models/gpt-5.6-sol`, `https://platform.claude.com/docs/en/about-claude/pricing`, `https://platform.claude.com/docs/en/models/sonnet-4-6/overview`, and `https://ai.google.dev/gemini-api/docs/pricing`; use source-check date `2026-09-07` and the ordered limitation text from Spec §8 without paraphrasing.

Pin these limitation strings verbatim:
- OpenAI: `the pinned standard card includes the published 1.25x cache-write token rate. Long-context, tool/search, Batch/Priority/Flex/other serving modifiers remain excluded because current attempt telemetry does not preserve correct per-request modifier data.`
- Anthropic: `Batch, data residency, fast mode, server-side tool fees and subscription/seat economics are excluded. Cache-write TTL is not observed, so cache-write tokens create a bounded range.`
- Gemini: `explicit cache-storage token-hour fees, tools/search, tier discounts and regional/enterprise terms are excluded. Output rate already includes thinking tokens.`

Use only these maps:

```python
_PROVIDERS = {"codex": "openai", "claude": "anthropic", "antigravity": "google"}
_OPENAI_ALIASES = {"gpt-5.6": "gpt-5.6-sol"}
_ANTIGRAVITY_MODELS = {
    "gemini-3.8-flash-low": "gemini-3.8-flash",
    "gemini-3.8-flash-medium": "gemini-3.8-flash",
    "gemini-3.8-flash-high": "gemini-3.8-flash",
}
```

Claude observes only `system/init -> event["model"]` and `assistant -> event["message"]["model"]`. Empty values are absent; present non-strings, invalid JSON, and non-object non-empty lines are malformed. Runtime strings must all agree and override/agrees-with exact configuration. Never persist parser text.

- [ ] **Step 6: Run Task 2 GREEN and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/__init__.py src/qualock/pricing/models.py src/qualock/pricing/catalog.py src/qualock/pricing/resolve.py tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
git diff --check
```

Expected: all tests PASS; Ruff, compileall, and diff-check exit `0`.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/qualock/pricing/__init__.py src/qualock/pricing/models.py src/qualock/pricing/catalog.py src/qualock/pricing/resolve.py tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
git commit -m "feat: bundle validated provider rate cards"
```

Expected: one commit containing only the six named files.

- [ ] **Step 8: Run independent Task 2 review**

Give the exact commit and Spec §§6-8.2, 11, 16, and mapped obligations to the fresh read-only reviewer. Require checks of every card field, snapshot IDs, exact Claude paths, closed maps, absence of fuzzy/network logic, Decimal rejection, UTC inclusivity, and dataclass field order. Critical/Important findings require a scoped fix, Step 6 rerun, and exact-head re-review.

### Task 3: Capture Runtime Provenance, Usage Trust, Immutable Sidecars, and Best-Effort Checks

**Files:**
- Modify: `src/qualock/pricing/resolve.py`
- Create: `src/qualock/pricing/sidecar.py`
- Modify: `src/qualock/pricing/__init__.py`
- Modify: `src/qualock/commands.py`
- Create: `tests/unit/test_pricing_provenance.py`
- Create: `tests/unit/test_pricing_sidecar_writer.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Task 2 catalog/resolution, `QualockConfig`, completed `QualificationResult`, and aware start/finish instants.
- Produces: `build_usage_detail_trust(agent: str, result: QualificationResult) -> tuple[AttemptUsageTrust, ...]`.
- Produces: `build_pricing_payload(config: QualockConfig, result: QualificationResult, run_started_at: datetime, run_finished_at: datetime) -> dict[str, object]`.
- Produces: `write_pricing_sidecar(qualification_dir: Path, payload: dict[str, object]) -> Path`.
- Produces in `commands.py`: `_write_pricing_sidecar_best_effort(qualification_dir: Path, config: QualockConfig, result: QualificationResult, run_started_at: datetime, run_finished_at: datetime) -> None`.

**Worker budget:** Fresh Sonnet high implementer; different Sonnet high reviewer; sequential branch writing only.

- [ ] **Step 1: Write RED protocol-specific trust tests**

In `test_pricing_provenance.py`, define local factories for `AttemptResult`, `CanaryExecution`, and `QualificationResult`; arguments include canary, side, repetition, valid, success, usage, and `events_jsonl`. Add `test_codex_cache_read_requires_every_completed_turn_detail`, `test_codex_missing_or_malformed_completed_turn_detail_is_unobserved`, `test_codex_cache_write_is_always_unobserved`, `test_claude_trust_uses_unique_terminal_result_usage`, `test_claude_cache_creation_absence_is_unobserved`, `test_claude_explicit_zero_cache_creation_is_observed`, `test_antigravity_cache_read_observed_and_write_known_zero`, `test_usage_trust_is_local_to_attempt_and_never_changes_usage`, `test_usage_trust_covers_every_started_failed_or_invalid_attempt_once`, and `test_usage_trust_omits_skipped_executions`.

```python
assert build_usage_detail_trust("antigravity", result) == (
    AttemptUsageTrust(
        canary_id="canary-a",
        side="candidate",
        repetition=1,
        cached_input_tokens_trust="observed",
        cache_write_input_tokens_trust="known_zero",
    ),
)
```

- [ ] **Step 2: Write RED payload, temporal-precedence, and atomic-writer tests**

In `test_pricing_sidecar_writer.py`, define `configured(agent: str, model: str, effort: str) -> QualockConfig` from `AgentConfig`, `ModelConfig`, and default remaining config, plus a local one-attempt result factory. Add `test_priced_payload_has_exact_schema_and_decimal_strings`, `test_unknown_model_payload_is_unavailable_with_complete_trust`, `test_malformed_claude_model_payload_has_fixed_reason_without_raw_text`, `test_invalid_capture_time_precedes_model_and_rate_failures`, `test_temporal_lookup_precedence_for_no_card_boundary_and_same_card`, `test_overlapping_catalog_runtime_lookup_fails_closed_to_no_rate_card`, `test_rate_boundary_crossed_keeps_resolved_model_source`, `test_payload_normalizes_aware_offsets_to_utc`, `test_successful_sidecar_publish_exposes_complete_sorted_bytes`, `test_existing_pricing_sidecar_is_never_overwritten`, `test_publish_failure_leaves_no_final_or_temp_file`, and `test_sidecar_capture_does_not_rewrite_existing_artifacts`.

```python
assert set(payload) == {
    "schema_version", "availability", "basis", "currency", "qualification_id",
    "run_started_at", "run_finished_at", "agent", "provider", "configured_model",
    "reasoning_effort", "canonical_model", "model_identity_source", "catalog_version",
    "rate_card_id", "source_url", "source_checked_at", "effective_from",
    "effective_until", "rates_per_million", "usage_detail_trust", "limitations",
    "unavailable_reason",
}
assert set(payload["rates_per_million"]) == {
    "input_uncached", "input_cached", "cache_write_lower", "cache_write_upper", "output",
}
assert all(
    value is None or isinstance(value, str)
    for value in payload["rates_per_million"].values()
)
```

```python
path = write_pricing_sidecar(qualification_dir, payload)
assert path == qualification_dir / "pricing.json"
assert path.read_bytes() == (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
assert not tuple(qualification_dir.glob(".pricing.*.tmp"))
```

- [ ] **Step 3: Write RED `execute_check` advisory-boundary tests**

Reuse `setup_project`, `FakeResolver`, and `FakeBackend` in `test_commands.py`. Add `test_execute_check_captures_window_around_run_and_after_canonical_write`, `test_check_pricing_writer_failure_is_advisory`, `test_unknown_model_check_writes_unavailable_sidecar`, `test_existing_sidecar_failure_cannot_change_check_result`, and `test_standalone_baseline_does_not_write_pricing_sidecar`. In `test_cli.py`, add `test_check_pricing_writer_failure_preserves_cli_output_and_exit`: build equivalent fixed-ID checks in two temporary project roots, inject a writer failure into only one real `execute_check`, return each result through `cli.execute_check`, and assert the two `runner.invoke(app, ["check", "codex@0.151.0"])` calls have identical stdout and exit code.

```python
def fail_write(_directory: Path, _payload: dict[str, object]) -> Path:
    raise OSError("sensitive writer detail")

monkeypatch.setattr(commands_module, "write_pricing_sidecar", fail_write)
result = execute_check(
    tmp_path,
    "codex@0.151.0",
    resolver=resolver,
    backend=backend,
    qualification_id="check-pricing-failure",
)
artifact_root = tmp_path / ".qualock/results/check-pricing-failure"
assert result.verdict is Verdict.BLOCK
assert {path.name for path in artifact_root.iterdir()} == {
    "report.md",
    "report.json",
    "qualification.json",
}
```

- [ ] **Step 4: Run Task 3 tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_commands.py tests/unit/test_cli.py -k "pricing or usage_trust or standalone_baseline"
```

Expected: FAIL because trust extraction, payload construction, atomic publication, and check integration do not exist.

- [ ] **Step 5: Implement exact protocol trust**

Iterate executions/attempts in result order and emit one record per actual attempt. Codex requires at least one `turn.completed` and valid explicit `usage.cached_input_tokens` on every completed turn; write trust is always unobserved. Claude uses the unique terminal top-level `type="result"` usage, observed read only with trustworthy canonical usage, and observed write only with explicit valid `cache_creation_input_tokens`. Antigravity uses terminal `event="result" -> result.usage`, observed read and protocol-known-zero write. Never mark a component trusted unless the protocol establishes read/write as mutually exclusive input partitions. Parser defects downgrade only that attempt/component and never mutate `Usage`.

- [ ] **Step 6: Implement complete payload construction**

Always build trust first. Validate and UTC-normalize time before model resolution. Apply endpoint precedence exactly: neither card or an overlap/programming lookup defect `no_rate_card`; one card or differing IDs `rate_boundary_crossed`; identical non-null ID `priced`. Emit exactly the Spec §6 keys, fixed basis/currency/provider, UTC ISO timestamps, catalog version, decimal strings, full material snapshot, ordered trust, limitations, and correct reason/null fields. Expected failures return complete unavailable dicts; unavailable records retain trust, have empty limitations, and no rate/source/effective/card fields. The only generated reasons are `unknown_model`, `missing_observed_model`, `inconsistent_observed_model`, `malformed_model_evidence`, `no_rate_card`, `invalid_capture_time`, and `rate_boundary_crossed`.

- [ ] **Step 7: Implement atomic no-replace publication**

Use `tempfile.mkstemp(prefix=".pricing.", suffix=".tmp", dir=qualification_dir)`, `os.fdopen(fd, "wb")`, write complete sorted/indented/trailing-newline bytes, `flush()`, `os.fsync()`, and `os.link(temp_path, qualification_dir / "pricing.json")`. In `finally`, unlink the temp path with `missing_ok=True`. Do not use final-path writes, `rename`, `replace`, or existence pre-checks.

- [ ] **Step 8: Integrate one isolated best-effort boundary after canonical writes**

```python
def _write_pricing_sidecar_best_effort(
    qualification_dir: Path,
    config: QualockConfig,
    result: QualificationResult,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> None:
    try:
        payload = build_pricing_payload(config, result, run_started_at, run_finished_at)
        write_pricing_sidecar(qualification_dir, payload)
    except Exception:
        return
```

Capture start immediately before `QualificationExecutor.run`. Assign the successful canonical writer's returned directory, then capture finish and call this helper. Do not touch baseline, policy, executor, or callers already delegating to `execute_check`.

- [ ] **Step 9: Run Task 3 GREEN and regressions**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_storage.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_cli.py -k "check or monitor or bisect or github"
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/resolve.py src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py src/qualock/commands.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_commands.py tests/unit/test_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing src/qualock/commands.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_commands.py tests/unit/test_cli.py
git diff --check
```

Expected: tests PASS; injected pricing failures preserve result/verdict/canonical artifacts; static commands exit `0`.

- [ ] **Step 10: Commit Task 3**

```bash
git add src/qualock/pricing/resolve.py src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py src/qualock/commands.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_commands.py tests/unit/test_cli.py
git commit -m "feat: capture immutable pricing provenance"
```

Expected: one commit containing only the eight named files.

- [ ] **Step 11: Run independent Task 3 review**

Give the exact commit and Spec §§6-8.2, 11, 15-16, and mapped obligations to the fresh read-only reviewer. Require checks of protocol trust, every-started-attempt coverage, temporal precedence, unavailable completeness, decimal-string JSON, post-canonical ordering, atomic/no-replace cleanup, and catch-all advisory isolation. Critical/Important findings require a scoped fix, Step 9 rerun, and exact-head re-review.

### Task 4: Tolerantly Scan Sidecars and Enforce Cross-File Snapshot Consistency

**Files:**
- Modify: `src/qualock/pricing/sidecar.py`
- Modify: `src/qualock/pricing/__init__.py`
- Create: `tests/unit/test_pricing_sidecar_loader.py`
- Modify: `tests/unit/test_history_loader.py`

**Interfaces:**
- Consumes: `HistorySummary.loaded` and exact owning historical attempt identities; never scans directories independently.
- Produces: `scan_pricing(summary: HistorySummary) -> PricingHistory`.
- Produces only fixed reasons: `unreadable pricing sidecar`, `invalid pricing JSON`, `pricing sidecar is not a JSON object`, `unsupported pricing schema`, `pricing qualification_id mismatch`, `malformed pricing sidecar`, and `rate-card snapshot mismatch`.
- Preserves: missing sidecars as older/unpinned; valid unavailable sidecars as records; Batch #41 report loading/rendering unaffected.

**Worker budget:** Fresh Sonnet medium implementer; different Sonnet medium reviewer; sequential branch writing only.

- [ ] **Step 1: Write RED tolerance and owner-binding tests**

Define local `priced_payload(qualification_id: str = "q-1") -> dict[str, object]` with every Spec §6 field and valid Terra snapshot, plus `loaded_summary(tmp_path: Path, qualification_id: str = "q-1") -> HistorySummary` with one matching baseline repetition. Add `test_missing_pricing_sidecar_is_older_unpinned_not_failure`, `test_invalid_pricing_json_is_fixed_failure_and_sibling_loads`, `test_non_utf8_sidecar_is_unreadable_and_sibling_loads`, `test_non_object_and_unsupported_schema_have_fixed_reasons`, `test_qualification_id_mismatch_cannot_rebind_report`, `test_valid_unavailable_sidecar_loads_normally`, `test_malformed_typed_fields_collapse_to_fixed_reason`, `test_raw_exception_path_and_payload_never_enter_failure_reason`, and `test_scanner_uses_only_successfully_loaded_reports`.

```python
assert scan_pricing(summary) == PricingHistory(
    records=(),
    older_unpinned_qualification_ids=("q-1",),
    failures=(),
)
```

- [ ] **Step 2: Write RED semantic, identity, and snapshot tests**

Add `test_sidecar_requires_exact_basis_currency_and_agent_provider_map`, `test_sidecar_model_source_reason_combinations_are_closed`, `test_priced_and_unavailable_nullability_are_mutually_exclusive`, `test_catalog_version_must_be_nonempty_string`, `test_sidecar_rates_reject_non_strings_nan_infinity_negative_and_reversed_range`, `test_sidecar_timestamps_require_aware_ordered_instants_and_normalize_utc`, `test_sidecar_effective_interval_must_be_ordered`, `test_missing_duplicate_or_extra_trust_identity_is_malformed`, `test_trust_repetition_is_positive_and_matches_one_based_attempt`, `test_same_rate_card_id_equal_snapshots_all_remain_records`, and `test_same_rate_card_id_different_snapshot_invalidates_every_owner`.

The conflict fixture has two reports using the same ID and changes only the second ordered limitations tuple; assert both records disappear and both owners receive `rate-card snapshot mismatch`.

- [ ] **Step 3: Pin history isolation**

Add `test_missing_pricing_sidecar_does_not_hide_history_report` and `test_malformed_pricing_sidecar_does_not_hide_history_report` to `test_history_loader.py`. Each writes valid `report.json`, optionally malformed `pricing.json`, calls only `scan_results`, and expects the same loaded report with no ignored entry. Production history must not import pricing.

- [ ] **Step 4: Run Task 4 tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_loader.py -k "pricing or sidecar or history_report"
```

Expected: FAIL because `scan_pricing` and strict parsing/snapshot checks do not exist; history isolation characterizations PASS.

- [ ] **Step 5: Implement report-bound tolerant parsing**

For each loaded report, inspect only `qualification_dir / "pricing.json"`. Translate read/decode/JSON/object/schema defects to exact reasons. Parse exact types, enums, dates, shared Decimal rules, aware timestamps normalized to UTC, model-source/reason combinations, and priced/unavailable nullability. Require sidecar/report ID equality before construction; never expose raw exceptions or paths.

- [ ] **Step 6: Enforce exact trust coverage and snapshot consistency**

Build expected identities from every owning started historical attempt; reject malformed, missing, duplicate, extra, or non-positive identities. Group priced records by non-null card ID and compare exactly:

```python
(
    sidecar.provider,
    sidecar.canonical_model,
    sidecar.source_url,
    sidecar.source_checked_at,
    sidecar.effective_from,
    sidecar.effective_until,
    sidecar.rates,
    sidecar.limitations,
)
```

Any difference removes every group member and creates one fixed failure per owner. Never call `resolve_rate_card` from the reader.

- [ ] **Step 7: Run Task 4 GREEN and static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing/sidecar.py tests/unit/test_pricing_sidecar_loader.py
git diff --check
```

Expected: all scanner, trust, snapshot, sibling-tolerance, and history-isolation tests PASS; static commands exit `0`.

- [ ] **Step 8: Commit Task 4**

```bash
git add src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_loader.py
git commit -m "feat: load pinned pricing sidecars safely"
```

Expected: one commit containing only the four named files.

- [ ] **Step 9: Run independent Task 4 review**

Give the exact commit and Spec §§6, 8.1, 12, and mapped obligations to the fresh read-only reviewer. Require checks of fixed reasons, sibling tolerance, owner binding, trust coverage, schema states, UTC normalization, all-member snapshot invalidation, no catalog lookup, and no reverse history dependency. Critical/Important findings require a scoped fix, Step 7 rerun, and exact-head re-review.

### Task 5: Calculate Decimal Samples and Analyze Exact Model/Rate Cohorts

**Files:**
- Create: `src/qualock/pricing/calculate.py`
- Create: `src/qualock/pricing/analysis.py`
- Modify: `src/qualock/pricing/__init__.py`
- Create: `tests/unit/test_pricing_calculate.py`
- Create: `tests/unit/test_pricing_analysis.py`

**Interfaces:**
- Consumes: `HistoricalExecution`, `Mapping[tuple[str, str, int], AttemptUsageTrust]`, pinned `RateComponents`, `HistorySummary`, `PricingHistory`, current canary IDs, and exact current agent/model/effort.
- Produces: `price_execution(execution: HistoricalExecution, trust_by_identity: Mapping[tuple[str, str, int], AttemptUsageTrust], rates: RateComponents) -> CostSample | None`.
- Produces: `analyze_cost(summary: HistorySummary, pricing: PricingHistory, current_canary_ids: Sequence[str], *, agent: str, configured_model: str, reasoning_effort: str) -> CostAnalysis`.
- Preserves: unrounded `Decimal` arithmetic/medians/sums and distinct qualification counting.

**Worker budget:** Fresh Sonnet high implementer; different Sonnet high reviewer; sequential branch writing only.

- [ ] **Step 1: Write RED Decimal calculator tests**

Define local typed/defaulted `hist_attempt`, `execution`, `trust`, and `rates` factories in `test_pricing_calculate.py`. Add `test_decimal_exact_cost_with_trusted_zero_cache_categories`, `test_cached_input_is_subtracted_from_uncached`, `test_cache_write_input_is_subtracted_from_uncached`, `test_reasoning_tokens_are_validated_but_not_double_counted`, `test_negative_or_inconsistent_subsets_are_unpriceable`, `test_unobserved_usage_is_unpriceable`, `test_nonzero_category_requires_corresponding_rate`, `test_claude_cache_write_rates_produce_lower_upper_range`, `test_explicit_zero_cache_write_is_exact_but_unobserved_zero_is_unpriceable`, `test_failed_or_invalid_outcomes_still_price`, `test_malformed_duration_does_not_block_price`, `test_skipped_execution_is_not_sample`, `test_mismatched_or_duplicate_pairing_is_not_sample`, `test_known_zero_requires_persisted_numeric_zero`, `test_missing_or_duplicate_trust_binding_is_unpriceable`, and `test_every_attempt_in_execution_must_be_priceable`.

```python
rate = RateComponents(
    Decimal("2.00"),
    Decimal("0.20"),
    Decimal("2.50"),
    Decimal("2.50"),
    Decimal("12.00"),
)
sample = price_execution(execution(
    hist_attempt("baseline", 1, input_tokens=1_000_000, output_tokens=100_000),
    hist_attempt("candidate", 1, input_tokens=500_000, output_tokens=50_000),
), trust_by_identity, rate)
assert sample == CostSample(Decimal("4.8"), Decimal("4.8"))
```

The local `execution` factory fixes `canary_id="canary-a"`; `trust_by_identity` is a literal baseline/candidate repetition-1 mapping with both states `known_zero` and persisted cache counters `0`.

- [ ] **Step 2: Write RED cohort, counter, and median tests**

Define local `report`, `sidecar`, `priced_history`, and paired-execution factories in `test_pricing_analysis.py`; the sidecar factory accepts ID, finish instant, configured triple, canonical model, card ID, rates, and limitations. Add `test_current_agent_model_effort_must_match_exactly`, `test_canonical_models_never_mix`, `test_rate_card_ids_never_mix`, `test_latest_finished_cohort_wins`, `test_latest_tie_uses_lexicographically_smallest_cohort`, `test_removed_historical_canaries_do_not_enter_estimates`, `test_per_canary_medians_are_decimal_without_prerounding`, `test_even_decimal_median_averages_middle_values`, `test_suite_sums_unrounded_per_canary_medians`, `test_missing_current_canary_makes_suite_unavailable`, `test_missing_canaries_preserve_current_config_order`, `test_primary_classifications_are_disjoint_and_exhaustive`, `test_one_multicanary_run_counts_priceable_qualification_once`, `test_selected_run_without_samples_is_not_priceable_run`, `test_historical_samples_use_pinned_rates_not_current_catalog`, `test_selected_limitations_come_only_from_pinned_snapshot`, and `test_unavailable_sidecar_never_enters_priced_cohort`.

```python
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
```

- [ ] **Step 3: Run Task 5 tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
```

Expected: collection/import FAIL because calculator, analysis, and exports do not exist.

- [ ] **Step 4: Implement strict per-execution pricing**

Require non-empty identical baseline/candidate repetition sets and no duplicate slots. Every attempt requires observed non-negative total input/output, non-negative cached/write subsets, matching `observed`/`known_zero` trust, `known_zero -> numeric zero`, optional reasoning within output, and non-negative `uncached = input - cached - write`. A nonzero category requires its rate; zero does not. Sum every attempt with the Spec §9 formula and divide by `Decimal(1_000_000)`; any attempt defect returns `None` for the execution. Never inspect success, valid, or duration.

- [ ] **Step 5: Implement primary classification and exact cohort selection**

Join by qualification ID and apply precedence: older/unpinned; pricing failure; excluded config; current-config unavailable; non-selected priced cohort; selected priced cohort. Select current-config priced `(canonical_model, rate_card_id)` by greatest normalized finish time, then lexicographically smallest tuple. Use no current catalog data for historical resolution, rates, or limitations.

- [ ] **Step 6: Implement current-canary Decimal medians and counters**

```python
def _decimal_median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)
```

Price only current canary IDs, preserve config order, and sum unrounded medians only if all current canaries have a sample. Count a selected qualification once if at least one current execution prices. Carry selected pinned limitations unchanged.

- [ ] **Step 7: Run Task 5 GREEN and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py tests/unit/test_pricing_sidecar_loader.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/calculate.py src/qualock/pricing/analysis.py src/qualock/pricing/__init__.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
git diff --check
```

Expected: all tests PASS; no float represents pricing data; static commands exit `0`.

- [ ] **Step 8: Commit Task 5**

```bash
git add src/qualock/pricing/calculate.py src/qualock/pricing/analysis.py src/qualock/pricing/__init__.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
git commit -m "feat: analyze historical reference costs"
```

Expected: one commit containing only the five named files.

- [ ] **Step 9: Run independent Task 5 review**

Give the exact commit and Spec §§9-11 and mapped obligations to the fresh read-only reviewer. Require checks of trust/pairing all-or-nothing gates, no outcome/duration dependency, no float/pre-rounding, exact config/cohort logic, exhaustive classification, tie-break, unique run counter, removed-canary exclusion, and pinned-only history. Critical/Important findings require a scoped fix, Step 7 rerun, and exact-head re-review.

### Task 6: Render Reference Costs and Expose a Read-Only Argument-Free CLI

**Files:**
- Create: `src/qualock/pricing/render.py`
- Modify: `src/qualock/pricing/__init__.py`
- Modify: `src/qualock/commands.py`
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_pricing_render.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_history_render.py`

**Interfaces:**
- Consumes: `CostAnalysis`, `load_project`, `project_dir`, `scan_results`, `scan_pricing`, and `analyze_cost`.
- Produces: `render_cost_text(analysis: CostAnalysis) -> str`.
- Produces: `execute_cost(root: Path) -> CostAnalysis` using current agent, effective model, effort, and config-order canaries.
- Produces: exact `@app.command("cost") def cost_command() -> None` with no arguments/options.

**Worker budget:** Fresh Sonnet medium implementer; different Sonnet medium reviewer; sequential branch writing only.

- [ ] **Step 1: Write RED renderer tests**

Define a local `cost_analysis` factory returning `CostAnalysis` with all counters/estimates explicit. Add `test_output_starts_with_exact_title`, `test_complete_suite_renders_latest_cohort_and_one_amount`, `test_distinct_range_renders_en_dash_endpoints`, `test_subcent_range_collapses_to_one_display_amount`, `test_money_rounds_only_at_final_boundary_half_even`, `test_money_never_abbreviates_thousands`, `test_partial_suite_renders_missing_count_and_config_order`, `test_selected_cohort_with_zero_samples_has_specific_guidance`, `test_no_selected_cohort_is_unavailable_not_error`, `test_basis_and_not_actual_bill_are_always_present`, `test_limitations_are_deduplicated_in_first_seen_order`, `test_every_history_counter_renders_even_when_zero`, and `test_pricing_failures_are_lexical_fixed_rows_without_paths_or_exceptions`. The last test injects absolute paths, traceback text, provider response text, and credential-shaped text into `PricingLoadFailure.qualification_dir`/unreachable fixture data and asserts none appears.

```python
def cost_analysis(*, suite: SuiteCostEstimate) -> CostAnalysis:
    return CostAnalysis(
        current_agent="codex",
        configured_model="gpt-5.6",
        reasoning_effort="high",
        selected_canonical_model="gpt-5.6-sol",
        selected_rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        per_canary=(),
        suite=suite,
        selected_cohort_runs=1,
        priceable_qualification_runs=1,
        older_unpinned_runs=0,
        unavailable_pricing_runs=0,
        excluded_config_runs=0,
        excluded_cohort_runs=0,
        pricing_failures=(),
        limitations=(),
    )


text = render_cost_text(
    cost_analysis(
        suite=SuiteCostEstimate(Decimal("1.424"), Decimal("1.425"), ())
    )
)
assert "About $1.42" in text
assert "$1.42–$1.42" not in text
assert "Public standard API list rates, USD." in text
assert "Reference estimate, not your actual bill." in text
```

- [ ] **Step 2: Write RED command composition tests**

Add `test_execute_cost_composes_current_config_and_canary_order`, `test_execute_cost_rejects_empty_current_suite`, `test_execute_cost_missing_results_is_normal_and_does_not_create_directory`, and `test_execute_cost_uses_effective_snapshot_model` to `test_commands.py`. Monkeypatch the three pure layers and assert exact arguments.

```python
def execute_cost(root: Path) -> CostAnalysis:
    config, canaries = load_project(root)
    if not canaries:
        raise CommandError("no canaries found")
    summary = scan_results(project_dir(root) / "results")
    pricing = scan_pricing(summary)
    return analyze_cost(
        summary,
        pricing,
        [canary.id for canary in canaries],
        agent=config.agent.name,
        configured_model=config.model.effective_model,
        reasoning_effort=config.model.reasoning_effort,
    )
```

- [ ] **Step 3: Write RED CLI, no-argument, zero-write, and Windows tests**

Add `test_cost_zero_history_exits_zero`, `test_cost_no_matching_cohort_exits_zero_with_guidance`, `test_cost_unavailable_sidecars_exit_zero_with_truthful_guidance`, `test_cost_empty_suite_exits_3`, `test_cost_config_and_canary_errors_exit_3_without_markup`, `test_cost_unexpected_error_exits_1_with_safe_fixed_message`, `test_cost_rejects_extra_arguments_and_pricing_flags`, `test_help_has_cost_but_no_max_cost_budget_json_or_pricing_options`, `test_cost_real_cold_start_does_not_create_results`, `test_cost_real_invocation_preserves_all_artifact_bytes_and_mtimes`, `test_cost_real_invocation_succeeds_with_network_blocked`, and `test_cost_sidecar_discovery_is_path_neutral_on_windows` to `test_cli.py`. The network test monkeypatches outbound socket connection to raise and still expects exit `0`; the path-neutral test creates the complete project under a nested `tmp_path / "project with spaces"` and runs unchanged on Linux and Windows.

```python
before = {
    p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
    for p in results.rglob("*")
    if p.is_file()
}
result = runner.invoke(app, ["cost"])
after = {
    p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
    for p in results.rglob("*")
    if p.is_file()
}
assert result.exit_code == 0
assert after == before
```

Add `test_qualock_history_public_output_is_unchanged` to `test_history_render.py` with the current exact zero-history rendered string, and add `test_history_renderer_does_not_consult_pricing_package` using source/import inspection. Keep all existing Batch #41 renderer tests unchanged.

- [ ] **Step 4: Run Task 6 tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_history_render.py -k "cost or pricing or history_public or history_renderer"
```

Expected: FAIL because rendering, composition, CLI registration, and imports do not exist; history golden stays green.

- [ ] **Step 5: Implement deterministic rendering**

Start exactly `QuaLock Reference Cost`. Render selected cohort/model/card; complete, partial, zero-sample, or no-cohort state; available per-canary rows in config order; missing IDs; all seven counters; lexical fixed failure rows; deduplicated first-seen pinned limitations only when a cohort exists; and exact Basis copy. With no cohort, omit card limitations and render the spec's non-error guidance. `_format_money(lower: Decimal, upper: Decimal) -> str` quantizes both only here with `Decimal("0.01")` and `ROUND_HALF_EVEN`, emits one value for equal displayed cents, otherwise an en dash, and never abbreviates thousands.

```text
Latest observed model/rate cohort
- Model: gpt-5.6-sol
- Rate card: openai:gpt-5.6-sol:standard:2026-09-07

Typical complete qualification
About $1.42
```

For partial suites emit `f"Unavailable: missing monetary history for {count} current canary/canaries"`; for a selected zero-sample cohort emit `No trustworthy monetary samples are available for the current canaries in this cohort.` For no selected cohort emit `Reference cost unavailable.` plus the two spec guidance paragraphs and still exit `0`.

```text
Reference cost unavailable.

No priced model/rate cohort with trustworthy pricing provenance is available
for the current configured agent/model/effort.

Run a normal qualification after pricing provenance is available;
QuaLock will preserve it for future estimates.
```

```text
History
- Selected cohort runs: 0
- Priceable matching runs: 0
- Older/unpinned runs: 0
- Unavailable pricing runs: 0
- Excluded config runs: 0
- Excluded older cohorts: 0
- Ignored pricing sidecars: 0

Basis
Public standard API list rates, USD.
Reference estimate, not your actual bill.
```

- [ ] **Step 6: Implement composition and exact safe CLI mapping**

Use the Step 2 function unchanged, then register:

```python
@app.command("cost")
def cost_command() -> None:
    try:
        analysis = execute_cost(Path.cwd())
    except (ConfigError, CanaryLoadError, CommandError, ValueError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except Exception as exc:
        console.print("unable to analyze reference cost", markup=False)
        raise typer.Exit(1) from exc
    console.print(render_cost_text(analysis), end="", markup=False)
```

Do not render unexpected exception text, create results, mutate artifacts, resolve historical aliases/cards, or add parameters.

- [ ] **Step 7: Run Task 6 GREEN and regression gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_cli.py -k "check or history or cost or monitor or bisect"
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/render.py src/qualock/pricing/__init__.py src/qualock/commands.py src/qualock/cli.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing src/qualock/commands.py src/qualock/cli.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py
git diff --check
```

Expected: tests PASS; cold start remains absent; bytes/mtimes are unchanged; history/check/monitor/bisect regressions PASS; static commands exit `0`.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/qualock/pricing/render.py src/qualock/pricing/__init__.py src/qualock/commands.py src/qualock/cli.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_history_render.py
git commit -m "feat: expose reference cost estimates"
```

Expected: one commit containing only the eight named files.

- [ ] **Step 9: Run independent Task 6 review**

Give the exact commit and Spec §§13-14 and mapped obligations to the fresh read-only reviewer. Require checks of title/copy, all render states/counters, pinned limitations, final-only half-even cents, safe failures, argument-free help, exit codes, zero-write/cold-start, Windows paths, and unchanged history/check output. Critical/Important findings require a scoped fix, Step 7 rerun, and exact-head re-review.

### Task 7: Gate the Exact Head, Review, Push/PR/CI, Document, and Rebase-Merge

**Files:**
- Modify only after implementation-head CI is green: `README.md`
- Modify only after implementation-head CI is green: `ROADMAP.md`
- Modify only after implementation-head CI is green: `docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md`
- Do not modify: `pyproject.toml`
- Do not create: release files, tags, package-publish configuration, or `types-PyYAML` dependency changes

**Interfaces:**
- Consumes: reviewed Tasks 1-6 and exact base `5800a814e295cf1126bca227bfb043438fd22c27`, with spec commit `4000ab175ab15eff7ec7ae0f68fe76a4b3abf43b` in ancestry.
- Produces: exact-head local gates, one Sonnet high whole-implementation approval, green implementation CI, post-CI docs, docs-inclusive gates/CI, exactly one Opus high final whole-branch approval, and verified rebase merge.
- Produces no release, tag, package publish, catalog refresh, or host-owned branch/worktree cleanup.

**Worker budget:** Controller owns gates/git/docs bookkeeping; Sonnet high owns the pre-push whole-implementation review; Opus high is invoked exactly once for final docs-inclusive review; Codex is only the recorded hard-limit fallback.

- [ ] **Step 1: Freeze the exact implementation head**

```bash
test "$(git merge-base 5800a814e295cf1126bca227bfb043438fd22c27 HEAD)" = "5800a814e295cf1126bca227bfb043438fd22c27"
git merge-base --is-ancestor 4000ab175ab15eff7ec7ae0f68fe76a4b3abf43b HEAD
test -z "$(git status --short)"
git rev-parse HEAD | tee /tmp/b42-implementation-head.txt
```

Expected: ancestry checks and clean-worktree assertion exit `0`; the file contains one 40-character SHA. If the base intentionally moved, replace it later only after recomputing mypy/Ruff baselines and recording the decision.

- [ ] **Step 2: Run fresh implementation-head GREEN tests, compileall, mypy, changed Ruff, and diff-check**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_writer.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
set +e
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock > /tmp/b42-mypy.txt 2>&1
b42_mypy_rc=$?
set -e
cat /tmp/b42-mypy.txt
test "$b42_mypy_rc" -eq 1
test "$(grep -c ': error:' /tmp/b42-mypy.txt)" -eq 3
test "$(grep -F -c '[import-untyped]' /tmp/b42-mypy.txt)" -eq 3
grep -Fx 'src/qualock/config/io.py:3: error: Library stubs not installed for "yaml"  [import-untyped]' /tmp/b42-mypy.txt
grep -Fx 'src/qualock/canary/loader.py:4: error: Library stubs not installed for "yaml"  [import-untyped]' /tmp/b42-mypy.txt
grep -Fx 'src/qualock/project_setup/config.py:6: error: Library stubs not installed for "yaml"  [import-untyped]' /tmp/b42-mypy.txt
mapfile -t b42_changed_python < <(git diff --name-only --diff-filter=ACMR 5800a814e295cf1126bca227bfb043438fd22c27..HEAD -- '*.py')
test "${#b42_changed_python[@]}" -gt 0
/home/pacmap/qualock-easy/.venv/bin/ruff check "${b42_changed_python[@]}"
git diff --check 5800a814e295cf1126bca227bfb043438fd22c27..HEAD
```

Expected: focused/full pytest and compileall exit `0`; mypy exits `1` with exactly the three listed errors and no other error; changed Python Ruff exits `0`; diff-check emits nothing.

- [ ] **Step 3: Prove no new full-tree Ruff debt and no protected-scope change**

```bash
git diff --name-only --diff-filter=ACMR 5800a814e295cf1126bca227bfb043438fd22c27..HEAD -- '*.py' > /tmp/b42-changed-python.txt
b42_base_tree=$(mktemp -d)
git archive 5800a814e295cf1126bca227bfb043438fd22c27 | tar -x -C "$b42_base_tree"
set +e
(cd "$b42_base_tree" && /home/pacmap/qualock-easy/.venv/bin/ruff check . --output-format=json > /tmp/b42-base-ruff.json)
b42_base_ruff_rc=$?
/home/pacmap/qualock-easy/.venv/bin/ruff check . --output-format=json > /tmp/b42-head-ruff.json
b42_head_ruff_rc=$?
set -e
test "$b42_base_ruff_rc" -eq 1
test "$b42_head_ruff_rc" -eq 1
printf '%s\n' "$b42_base_tree" > /tmp/b42-base-tree.txt
/home/pacmap/qualock-easy/.venv/bin/python - <<'PY'
import json
from pathlib import Path

changed = set(Path("/tmp/b42-changed-python.txt").read_text().splitlines())
base_root = Path(Path("/tmp/b42-base-tree.txt").read_text().strip())
head_root = Path.cwd()

def diagnostics(path: str, root: Path) -> set[tuple[str, int, int, str, str]]:
    payload = json.loads(Path(path).read_text())
    return {
        (Path(item["filename"]).relative_to(root).as_posix(), item["location"]["row"], item["location"]["column"], item["code"], item["message"])
        for item in payload
        if Path(item["filename"]).relative_to(root).as_posix() not in changed
    }

new = diagnostics("/tmp/b42-head-ruff.json", head_root) - diagnostics("/tmp/b42-base-ruff.json", base_root)
assert not new, sorted(new)
PY
rm -rf "$b42_base_tree"
b42_protected_changes=$(git diff --name-only 5800a814e295cf1126bca227bfb043438fd22c27..HEAD -- \
  .github/workflows/ci.yml \
  pyproject.toml \
  src/qualock/agents \
  src/qualock/baseline \
  src/qualock/canary/models.py \
  src/qualock/config/models.py \
  src/qualock/evidence \
  src/qualock/github_pr \
  src/qualock/project_watch \
  src/qualock/qualification/policy.py \
  src/qualock/release_monitor \
  src/qualock/run/executor.py \
  src/qualock/scheduler \
  src/qualock/source \
  src/qualock/version_bisect)
test -z "$b42_protected_changes"
! git diff 5800a814e295cf1126bca227bfb043438fd22c27..HEAD -- pyproject.toml | grep -Fq 'types-PyYAML'
```

Expected: inline comparison finds no new diagnostic on unchanged base files; protected command emits no path; dependency guard exits `0`. Record both Ruff return codes; base debt need not make them `0`.

- [ ] **Step 4: Run pre-push whole-implementation review**

Use one fresh read-only Sonnet high reviewer on exact diff `5800a814e295cf1126bca227bfb043438fd22c27..$(cat /tmp/b42-implementation-head.txt)` against the spec and 87-row matrix. Require `APPROVED` with no Critical/Important finding and explicit review of advisory isolation, catalog/provenance, trust, validation, Decimal/cohorts, read-only CLI, Windows, and protected scopes. A finding returns to a fresh correctly budgeted owning-task implementer, requires that task's GREEN/review loop plus Steps 1-3 on a new head, and invalidates the old whole review.

- [ ] **Step 5: Push implementation head and create a non-draft PR**

```bash
git push -u origin feat/provider-cost-estimates
gh pr create --base main --head feat/provider-cost-estimates --title "feat: add provider-specific reference cost estimates" --body '## Summary
- capture immutable provider/model/rate provenance beside new qualification artifacts
- add read-only `qualock cost` with Decimal canary medians and latest exact model/rate cohorts
- preserve provider-neutral history and qualification policy while failing closed on incomplete pricing telemetry

## Honesty and boundaries
- API-equivalent public standard list-rate reference in USD, not an invoice or actual bill
- no monetary gating, network pricing lookup, retroactive pricing, database/index, JSON mode, or artifact rewrite
- no policy/executor/config-schema/canary-schema changes

## Verification
- exact-head local pytest, compileall, strict mypy baseline, changed-file Ruff, no-new full-tree Ruff, diff-check, and protected-scope gates passed
- independent task reviews and Sonnet high whole-implementation review passed
- Linux Python 3.11/3.12/3.13 and Windows CI must be green before documentation is marked delivered'
gh pr view --json number,state,isDraft,headRefOid,mergeable,mergeStateStatus,url
```

Expected: push succeeds; PR is open/non-draft; `headRefOid` equals `/tmp/b42-implementation-head.txt`.

- [ ] **Step 6: Require implementation-head CI green before docs**

```bash
gh pr checks --watch --fail-fast
gh pr checks
test "$(gh pr view --json headRefOid --jq .headRefOid)" = "$(cat /tmp/b42-implementation-head.txt)"
```

Expected: Linux Python 3.11/3.12/3.13 and `windows-test` all PASS on the implementation SHA. CI defects require TDD root-cause fix by a fresh owning-task implementer, scoped review, Steps 1-4, new push, and fresh CI.

- [ ] **Step 7: Run documentation RED assertions only after Step 6**

```bash
! grep -nFx '### Provider-specific reference cost estimates' README.md
grep -nFx 'Provider-specific monetary cost estimates (#42), pending, advisory only, outside qualification pass/fail policy.' ROADMAP.md
grep -nFx -- '- **Status:** Proposed design; implementation is not authorized until this spec is reviewed' docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md
```

Expected: all commands exit `0`, proving docs did not claim delivery before green implementation CI.

- [ ] **Step 8: Update only the approved post-CI docs**

Insert this README section immediately after Historical qualification insights:

````markdown
### Provider-specific reference cost estimates

After new qualifications have captured pricing provenance, inspect the current suite's historical reference estimate:

```bash
qualock cost
```

`qualock cost` is read-only and offline. It reports an API-equivalent public standard list-rate reference in USD from the exact provider/model/rate snapshots preserved with qualification artifacts. It is not your actual bill, an invoice, a subscription or seat allocation, or a qualification budget, and it never affects PASS/WARN/BLOCK/INCOMPLETE behavior. Older runs without `pricing.json` remain visible as unpinned history and are never retroactively priced.
````

Move #42 from Roadmap `Next` to `Delivered` as exactly `Provider-specific API-equivalent reference cost estimates (#42), advisory only and outside qualification pass/fail policy.` Change only the canonical spec status line to `- **Status:** Delivered`.

- [ ] **Step 9: Commit docs and run docs-inclusive GREEN local gates**

```bash
git add README.md ROADMAP.md docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md
git commit -m "docs: document reference cost estimates"
grep -nFx '### Provider-specific reference cost estimates' README.md
grep -nFx 'Provider-specific API-equivalent reference cost estimates (#42), advisory only and outside qualification pass/fail policy.' ROADMAP.md
grep -nFx -- '- **Status:** Delivered' docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
git diff --check 5800a814e295cf1126bca227bfb043438fd22c27..HEAD
```

Repeat Steps 2-3 in full on the docs-inclusive head. Expected: exact docs copy is found; full/local/static/protected gates pass; only Tasks 1-6 product/test files plus the three approved docs differ.

- [ ] **Step 10: Push docs-inclusive head and require fresh CI**

```bash
git rev-parse HEAD | tee /tmp/b42-docs-head.txt
git push origin feat/provider-cost-estimates
gh pr checks --watch --fail-fast
gh pr checks
test "$(gh pr view --json headRefOid --jq .headRefOid)" = "$(cat /tmp/b42-docs-head.txt)"
```

Expected: all Linux and Windows checks PASS on exactly `/tmp/b42-docs-head.txt`.

- [ ] **Step 11: Run exactly one final Opus high review**

Create exactly one Opus high reviewer for exact diff `5800a814e295cf1126bca227bfb043438fd22c27..$(cat /tmp/b42-docs-head.txt)`. Require `APPROVED` with no Critical/Important finding and explicit confirmation of all 87 obligations, docs, gates/CI, and no release scope. If Opus is hard-limited, create one Codex GPT-5.6 Sol high reviewer as the only recorded fallback; never run both. If the single whole-branch review finds issues, make one consolidated owning-task fix wave, obtain independent scoped Sonnet review of every fix, and rerun fresh local gates plus CI; do not create or invoke a second Opus reviewer.

- [ ] **Step 12: Verify exact identity and rebase-merge**

```bash
b42_local_head=$(git rev-parse HEAD)
b42_remote_head=$(git ls-remote origin refs/heads/feat/provider-cost-estimates | cut -f1)
b42_pr_head=$(gh pr view --json headRefOid --jq .headRefOid)
test "$b42_local_head" = "$b42_remote_head"
test "$b42_local_head" = "$b42_pr_head"
test "$b42_local_head" = "$(cat /tmp/b42-docs-head.txt)"
gh pr view --json state,isDraft,headRefOid,mergeable,mergeStateStatus,url
gh pr checks
gh pr merge --rebase
gh pr view --json state,mergedAt,mergeCommit,url
git fetch origin main
git rev-parse origin/main
git branch -r --contains "$(gh pr view --json mergeCommit --jq .mergeCommit.oid)"
test "$(git rev-parse HEAD^{tree})" = "$(git rev-parse origin/main^{tree})"
```

Expected: three heads match; latest checks are green; PR reports `MERGED` with non-null merge commit; fetched `origin/main` contains it. Preserve branch/worktree.

- [ ] **Step 13: Close bookkeeping without a release**

Record exact commits, worker/reviewer models and fallbacks, review verdicts, implementation/docs heads, local outcomes, CI identities, single final-review verdict, PR URL, and merged main SHA. Remove only Batch #42 SDD scratch state if finishing workflow explicitly authorizes it. Do not create a tag, release, package build/upload, catalog refresh, or publish artifact.

## §17 TDD Coverage Matrix

| §17 | Task | Named test or exact gate |
| ---: | ---: | --- |
| 1 | 4 | `test_missing_pricing_sidecar_is_older_unpinned_not_failure` |
| 2 | 4 | `test_missing_pricing_sidecar_does_not_hide_history_report` |
| 3 | 4 | `test_invalid_pricing_json_is_fixed_failure_and_sibling_loads`; `test_malformed_pricing_sidecar_does_not_hide_history_report` |
| 4 | 4 | `test_non_utf8_sidecar_is_unreadable_and_sibling_loads` |
| 5 | 2 | `test_claude_alias_resolves_consistent_runtime_model` |
| 6 | 2 | `test_claude_alias_without_observation_fails_closed` |
| 7 | 2 | `test_claude_conflicting_observations_fail_closed` |
| 8 | 2 | `test_claude_exact_config_requires_observed_agreement` |
| 9 | 2 | `test_openai_exact_canonical_models_resolve` |
| 10 | 2 | `test_openai_documented_alias_resolves_only_to_sol` |
| 11 | 2 | `test_openai_unknown_and_convenience_names_do_not_fuzzy_match` |
| 12 | 2 | `test_antigravity_three_explicit_effort_ids_map` |
| 13 | 2 | `test_antigravity_unlisted_suffix_does_not_resolve` |
| 14 | 2 | `test_catalog_version_and_rate_card_ids_are_unique`; `test_rate_cards_are_frozen_material_snapshots` |
| 15 | 2 | `test_gemini_boundary_selects_old_then_new_card` |
| 16 | 2 | `test_openai_cards_pin_standard_rates_cache_write_multiplier_and_no_expiry` |
| 17 | 5 | `test_decimal_exact_cost_with_trusted_zero_cache_categories` |
| 18 | 5 | `test_cached_input_is_subtracted_from_uncached` |
| 19 | 5 | `test_cache_write_input_is_subtracted_from_uncached` |
| 20 | 5 | `test_reasoning_tokens_are_validated_but_not_double_counted` |
| 21 | 5 | `test_negative_or_inconsistent_subsets_are_unpriceable` |
| 22 | 5 | `test_unobserved_usage_is_unpriceable` |
| 23 | 5 | `test_nonzero_category_requires_corresponding_rate` |
| 24 | 5 | `test_claude_cache_write_rates_produce_lower_upper_range` |
| 25 | 5 | `test_explicit_zero_cache_write_is_exact_but_unobserved_zero_is_unpriceable` |
| 26 | 5 | `test_failed_or_invalid_outcomes_still_price` |
| 27 | 5 | `test_malformed_duration_does_not_block_price` |
| 28 | 5 | `test_skipped_execution_is_not_sample` |
| 29 | 5 | `test_mismatched_or_duplicate_pairing_is_not_sample` |
| 30 | 5 | `test_current_agent_model_effort_must_match_exactly` |
| 31 | 5 | `test_canonical_models_never_mix` |
| 32 | 5 | `test_rate_card_ids_never_mix` |
| 33 | 5 | `test_latest_finished_cohort_wins`; `test_latest_tie_uses_lexicographically_smallest_cohort` |
| 34 | 5 | `test_removed_historical_canaries_do_not_enter_estimates` |
| 35 | 5 | `test_per_canary_medians_are_decimal_without_prerounding` |
| 36 | 5 | `test_even_decimal_median_averages_middle_values` |
| 37 | 5 | `test_suite_sums_unrounded_per_canary_medians` |
| 38 | 5 | `test_missing_current_canary_makes_suite_unavailable` |
| 39 | 5 | `test_missing_canaries_preserve_current_config_order` |
| 40 | 6 | `test_money_rounds_only_at_final_boundary_half_even` |
| 41 | 6 | `test_complete_suite_renders_latest_cohort_and_one_amount`; `test_distinct_range_renders_en_dash_endpoints`; `test_subcent_range_collapses_to_one_display_amount` |
| 42 | 6 | `test_basis_and_not_actual_bill_are_always_present` |
| 43 | 6 | `test_limitations_are_deduplicated_in_first_seen_order` |
| 44 | 6 | `test_cost_zero_history_exits_zero`; `test_cost_real_cold_start_does_not_create_results` |
| 45 | 6 | `test_cost_no_matching_cohort_exits_zero_with_guidance` |
| 46 | 6 | `test_execute_cost_rejects_empty_current_suite`; `test_cost_empty_suite_exits_3` |
| 47 | 6 | `test_cost_config_and_canary_errors_exit_3_without_markup` |
| 48 | 6 | `test_cost_real_invocation_preserves_all_artifact_bytes_and_mtimes` |
| 49 | 4 | `test_scanner_uses_only_successfully_loaded_reports` with the existing project-protection mixed-artifact loader fixture |
| 50 | 3 | `test_check_pricing_writer_failure_is_advisory`; `test_check_pricing_writer_failure_preserves_cli_output_and_exit`; existing `test_check_easy_output_is_exactly_preserved` regression |
| 51 | 3 | `test_unknown_model_check_writes_unavailable_sidecar` |
| 52 | 3 | `test_priced_payload_has_exact_schema_and_decimal_strings` |
| 53 | 3 | `test_sidecar_capture_does_not_rewrite_existing_artifacts` |
| 54 | 6 | `test_qualock_history_public_output_is_unchanged`; `test_history_renderer_does_not_consult_pricing_package` |
| 55 | 1 | `test_cache_and_reasoning_fields_default_to_none_and_cannot_affect_token_totals`; `test_wildly_different_cache_and_reasoning_values_do_not_change_41_results`; full Task 1 history GREEN suite |
| 56 | 2, 6 | `test_pricing_runtime_has_no_network_client_imports`; `test_cost_real_invocation_succeeds_with_network_blocked` |
| 57 | 6 | `test_help_has_cost_but_no_max_cost_budget_json_or_pricing_options`; `test_cost_rejects_extra_arguments_and_pricing_flags` |
| 58 | 7 | Step 3 protected-scope exact-base diff gate |
| 59 | 6 | `test_cost_sidecar_discovery_is_path_neutral_on_windows`; `test_cost_real_invocation_preserves_all_artifact_bytes_and_mtimes` |
| 60 | 7 | Steps 6 and 10 Linux 3.11/3.12/3.13 plus `windows-test` CI gates |
| 61 | 4 | `test_qualification_id_mismatch_cannot_rebind_report` |
| 62 | 2 | `test_provider_for_agent_is_exact_and_closed` |
| 63 | 3 | `test_rate_boundary_crossed_keeps_resolved_model_source`; `test_temporal_lookup_precedence_for_no_card_boundary_and_same_card` |
| 64 | 5 | `test_historical_samples_use_pinned_rates_not_current_catalog` |
| 65 | 3 | `test_existing_pricing_sidecar_is_never_overwritten`; `test_existing_sidecar_failure_cannot_change_check_result` |
| 66 | 2, 3 | `test_claude_malformed_or_non_object_jsonl_is_malformed`; `test_malformed_claude_model_payload_has_fixed_reason_without_raw_text` |
| 67 | 2 | `test_initial_cards_do_not_resolve_before_applicability_floor`; `test_effective_until_is_utc_date_inclusive` |
| 68 | 3 | `test_codex_cache_read_requires_every_completed_turn_detail`; `test_codex_missing_or_malformed_completed_turn_detail_is_unobserved`; `test_codex_cache_write_is_always_unobserved` |
| 69 | 3 | `test_claude_trust_uses_unique_terminal_result_usage`; `test_claude_cache_creation_absence_is_unobserved`; `test_claude_explicit_zero_cache_creation_is_observed` |
| 70 | 3, 5 | `test_antigravity_cache_read_observed_and_write_known_zero`; `test_known_zero_requires_persisted_numeric_zero` |
| 71 | 4 | `test_missing_duplicate_or_extra_trust_identity_is_malformed`; `test_trust_repetition_is_positive_and_matches_one_based_attempt` |
| 72 | 5 | `test_explicit_zero_cache_write_is_exact_but_unobserved_zero_is_unpriceable` |
| 73 | 4 | `test_sidecar_requires_exact_basis_currency_and_agent_provider_map`; `test_sidecar_model_source_reason_combinations_are_closed`; `test_priced_and_unavailable_nullability_are_mutually_exclusive`; `test_catalog_version_must_be_nonempty_string` |
| 74 | 2, 4 | `test_rate_parser_accepts_only_finite_nonnegative_decimal_strings`; `test_cache_write_rates_are_both_null_or_ordered`; `test_sidecar_rates_reject_non_strings_nan_infinity_negative_and_reversed_range` |
| 75 | 2, 4 | `test_effective_interval_rejects_from_after_until`; `test_sidecar_timestamps_require_aware_ordered_instants_and_normalize_utc`; `test_sidecar_effective_interval_must_be_ordered` |
| 76 | 3 | `test_invalid_capture_time_precedes_model_and_rate_failures`; `test_temporal_lookup_precedence_for_no_card_boundary_and_same_card` |
| 77 | 4 | `test_same_rate_card_id_different_snapshot_invalidates_every_owner`; `test_same_rate_card_id_equal_snapshots_all_remain_records` |
| 78 | 5 | `test_latest_tie_uses_lexicographically_smallest_cohort` |
| 79 | 5 | `test_primary_classifications_are_disjoint_and_exhaustive`; `test_one_multicanary_run_counts_priceable_qualification_once`; `test_selected_run_without_samples_is_not_priceable_run` |
| 80 | 6 | `test_complete_suite_renders_latest_cohort_and_one_amount`; `test_partial_suite_renders_missing_count_and_config_order`; `test_selected_cohort_with_zero_samples_has_specific_guidance`; `test_every_history_counter_renders_even_when_zero` |
| 81 | 6 | `test_pricing_failures_are_lexical_fixed_rows_without_paths_or_exceptions` |
| 82 | 3 | `test_successful_sidecar_publish_exposes_complete_sorted_bytes`; `test_existing_pricing_sidecar_is_never_overwritten`; `test_publish_failure_leaves_no_final_or_temp_file` |
| 83 | 6 | `test_cost_unavailable_sidecars_exit_zero_with_truthful_guidance` |
| 84 | 2 | `test_claude_model_paths_missing_and_empty_are_absent`; `test_claude_non_string_model_is_malformed`; `test_claude_malformed_or_non_object_jsonl_is_malformed`; `test_claude_unknown_consistent_runtime_model_is_unknown` |
| 85 | 5 | `test_selected_limitations_come_only_from_pinned_snapshot`; `test_historical_samples_use_pinned_rates_not_current_catalog` |
| 86 | 3 | `test_usage_trust_covers_every_started_failed_or_invalid_attempt_once`; `test_usage_trust_omits_skipped_executions` |
| 87 | 3, 5 | `test_unknown_model_payload_is_unavailable_with_complete_trust`; `test_unavailable_sidecar_never_enters_priced_cohort` |

## Plan Self-Review

- **Spec coverage:** Tasks 1-6 cover every architecture file, schema field, fixed reason, provider rule, trust rule, catalog card, temporal rule, calculation/cohort rule, counter, renderer state, CLI exit, writer boundary, and zero-write constraint in §§1-16. Task 7 covers §§18-19 exact-head, review, CI, docs, merge, and no-release gates.
- **87-obligation coverage:** The matrix has one explicit row for every integer 1 through 87; each row names its task and concrete test or unavoidable CI/protected-scope gate.
- **Type consistency:** Every required §11 dataclass and function signature is produced before use under the identical name/type. Current config consistently uses `agent.name`, `model.effective_model`, and `model.reasoning_effort`.
- **Dependency consistency:** Pricing imports normalized history; no task adds the reverse dependency. Historical readers use pinned rates/limitations and never catalog lookup; runtime payload construction alone uses current bundled resolution.
- **Artifact consistency:** Canonical artifacts precede the best-effort boundary; sidecars use complete temp bytes, fsync, same-filesystem no-replace link, and cleanup. Missing/malformed pricing never changes report loading.
- **Static baseline consistency:** Exact three PyYAML mypy findings, changed-file Ruff, full-tree no-new Ruff, compileall, diff-check, protected scope, and unchanged `pyproject.toml`/no-`types-PyYAML` gates are explicit.
- **Review/model consistency:** Tasks 1/4/6 use Sonnet medium, Tasks 2/3/5 use Sonnet high, each with fresh implementer plus different reviewer; whole implementation uses Sonnet high; exactly one Opus high reviewer is created after docs-inclusive CI; Codex GPT-5.6 Sol is hard-limit fallback only.
- **Placeholder scan:** No deferred markers, generic error-handling steps, unnamed test requests, code ellipses, or undefined cross-task helper interfaces remain.
- **Code-fence audit:** All Markdown fences are paired, including the four-backtick outer README block.

## Execution Handoff

Use **Subagent-Driven Development** for Tasks 1-6 with one sequential fresh implementer and one independent read-only reviewer per task under the exact budgets above. Task 7 is controller-owned: freeze heads, run gates, obtain the pre-push Sonnet high review, wait for implementation CI before docs, obtain the single final Opus high review, and rebase-merge only after identity and fresh docs-inclusive CI verification.
