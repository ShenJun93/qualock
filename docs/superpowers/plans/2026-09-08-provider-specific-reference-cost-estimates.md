# Provider-Specific API-Equivalent Reference Cost Estimates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add offline, reproducible provider-specific API-equivalent reference-cost estimates through pinned per-qualification pricing provenance and a read-only `qualock cost` command, without changing qualification policy or execution admission.

**Architecture:** Extend provider-neutral history with optional normalized usage subsets, then add a one-way `qualock.pricing` package for immutable rate cards, exact model resolution, per-attempt trust provenance, sidecar writing/loading, Decimal cost calculation, cohort analysis, and deterministic rendering. `execute_check` best-effort emits `pricing.json` only after canonical artifacts; `execute_cost` composes existing project/history loading with pricing analysis and never writes. Historical pricing always uses pinned sidecar snapshots, never today's catalog.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, `decimal.Decimal`, `datetime`, `json`, `os`, `pathlib`, `statistics`, existing Typer/Rich CLI, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md`

## Global Constraints

- Exact feature base: `5800a814e295cf1126bca227bfb043438fd22c27`; approved canonical spec commit: `4000ab175ab15eff7ec7ae0f68fe76a4b3abf43b`.
- Worktree/branch: `/home/pacmap/qualock-provider-costs`, `feat/provider-cost-estimates`; all implementation tasks start after spec commit `4000ab1`.
- `qualock cost` is advisory API-equivalent public list-rate reference only, never an invoice, subscription allocation, billing reconciliation, pass/fail signal, or admission budget.
- Runtime must be fully offline: no provider/account/billing/pricing HTTP calls, no background refresh, no database/index, no artifact migration.
- Historical reports before #42 remain older/unpinned and must never be retroactively assigned provider/model/rates from current config.
- `pricing/` may depend on `history`; `history/` must never depend on `pricing`.
- Never change `src/qualock/qualification/policy.py`, `src/qualock/run/executor.py`, verdict semantics, canary order/admission, baseline stability, `max_attempts`, `max_tokens`, monitor, bisect, GitHub PR qualification, scheduler, source management, canary/config schemas, or provider adapter behavior.
- Do not modify `pyproject.toml`, install `types-PyYAML`, add `--max-cost`, JSON pricing output, cost filters, pricing flags, or monetary gating.
- All monetary arithmetic and persisted rates use `Decimal`/decimal strings only; no binary float for rates, samples, medians, suite sums, or display-rounding inputs.
- Historical price calculations use only validated pinned `pricing.json` snapshots; catalog updates never rewrite or reinterpret prior priced sidecars.
- Cache-component trust comes from per-attempt sidecar provenance, never from compatibility-zero numeric fields alone.
- Sidecar publication is immutable, atomic, same-filesystem, and no-replace; pricing failures remain advisory and cannot alter canonical qualification artifacts or command verdicts.
- `qualock history` public output and #41 effectiveness/runtime/token values must remain unchanged; only the structural no-cache-fields assertion is intentionally re-baselined.
- Strict mypy may report only the exact three inherited PyYAML `import-untyped` errors in `src/qualock/config/io.py`, `src/qualock/canary/loader.py`, and `src/qualock/project_setup/config.py`; any extra error blocks completion.
- Every #42 changed Python file must be Ruff-clean; full-tree Ruff must introduce no findings beyond exact feature base diagnostics.
- Fresh full pytest, compileall, `git diff --check`, protected-scope diff, Linux Python 3.11/3.12/3.13 CI, and Windows CI are mandatory gates.
- Model budget: Sonnet medium Tasks 1/4/6 and bounded task reviews; Sonnet high Tasks 2/3/5 and pre-push whole-implementation review; Opus high exactly once for final docs-inclusive whole-branch review; Codex is fallback only and every substitution is ledgered.
- One fresh implementer per implementation task, one independent task review after every task, no parallel branch writers, controller does not write production fixes.
- No tag, release, package publish, or pricing-catalog network refresh is part of Batch #42.

---

## File Structure

- Modify `src/qualock/history/models.py`: append optional cache/reasoning normalized fields only.
- Modify `src/qualock/history/loader.py`: normalize new persisted usage subsets without changing existing history semantics.
- Create `src/qualock/pricing/models.py`: frozen rate/provenance/history/cost dataclasses and fixed literals.
- Create `src/qualock/pricing/catalog.py`: immutable bundled rate cards, catalog validation, UTC effective-date lookup.
- Create `src/qualock/pricing/resolve.py`: exact agent/provider/model identity resolution and raw Claude model evidence extraction.
- Create `src/qualock/pricing/sidecar.py`: usage-detail trust extraction, payload construction, atomic writer, tolerant sidecar scanner/parser.
- Create `src/qualock/pricing/calculate.py`: pure per-execution Decimal pricing.
- Create `src/qualock/pricing/analysis.py`: cohort selection, report classification, medians, all-or-nothing suite estimate.
- Create `src/qualock/pricing/render.py`: deterministic low-tech monetary rendering and final-cent rounding.
- Create `src/qualock/pricing/__init__.py`: export only the small command-facing pricing surface.
- Modify `src/qualock/commands.py`: capture qualification run window, best-effort sidecar emission, and `execute_cost` composition.
- Modify `src/qualock/cli.py`: argument-free `cost` command with history-style exit mapping.
- Create tests: `tests/unit/test_pricing_catalog.py`, `test_pricing_resolve.py`, `test_pricing_sidecar.py`, `test_pricing_calculate.py`, `test_pricing_analysis.py`, `test_pricing_render.py`.
- Modify tests: `tests/unit/test_history_loader.py`, `test_history_analysis.py`, `test_commands.py`, `test_cli.py`.
- Modify `README.md`, `ROADMAP.md`, and the spec status only after implementation-head CI is green in Task 7.

### Task 1: Extend Historical Usage Without Changing #41 Semantics

**Files:**
- Modify: `src/qualock/history/models.py`
- Modify: `src/qualock/history/loader.py`
- Modify: `tests/unit/test_history_loader.py`
- Modify: `tests/unit/test_history_analysis.py`

**Interfaces:**
- Produces `HistoricalAttempt.cached_input_tokens: int | None = None`, `cache_write_input_tokens: int | None = None`, `reasoning_output_tokens: int | None = None`, appended after existing fields.
- Preserves `analyze_history(summary, current_canary_ids)` behavior and all #41 result dataclasses unchanged.
- Later pricing tasks consume these optional normalized details but never infer trust from them.

- [ ] **Step 1: Write RED loader compatibility tests**

Add tests proving persisted `usage` detail fields normalize independently, bool/non-int/missing values become `None`, pre-#42 reports remain loadable, and the three fields default to `None` when constructing `HistoricalAttempt` with the old argument set.

```python
attempt = scan_results(results).loaded[0].executions[0].attempts[0]
assert attempt.cached_input_tokens == 4
assert attempt.cache_write_input_tokens == 2
assert attempt.reasoning_output_tokens == 3
```
- [ ] **Step 2: Write RED #41 value-identity tests**

Re-baseline the old structural assertion so fields exist with `None` defaults, then construct attempts with wildly different cache/reasoning values and prove #41 effectiveness/runtime/token results are unchanged and token totals remain exactly `input_tokens + output_tokens`.

```python
analysis = analyze_history(summary, ["canary-a"])
assert analysis.per_canary_estimates[0].token_samples == (6000,)
```

- [ ] **Step 3: Run Task 1 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py
```

Expected: new tests fail because `HistoricalAttempt` has no optional detail fields and loader drops them.

- [ ] **Step 4: Implement the minimal history extension**

Append the three optional fields with `None` defaults. In `_normalize_attempt`, use the existing bool-rejecting integer normalizer for the three persisted `usage` keys; malformed/missing `usage` yields all three `None`. Do not modify `history/analysis.py` or `history/render.py`.

- [ ] **Step 5: Run Task 1 GREEN/static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history/models.py src/qualock/history/loader.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/history
git diff --check
```

- [ ] **Step 6: Commit Task 1**

```bash
git add src/qualock/history/models.py src/qualock/history/loader.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py
git commit -m "refactor: expose historical usage detail subsets"
```

Reviewer must verify #41 values/rendering remain provider-neutral and the new numeric zeros are never treated as monetary trust.
### Task 2: Add Pricing Models, Bundled Catalog, and Exact Model Resolution

**Files:**
- Create: `src/qualock/pricing/__init__.py`
- Create: `src/qualock/pricing/models.py`
- Create: `src/qualock/pricing/catalog.py`
- Create: `src/qualock/pricing/resolve.py`
- Create: `tests/unit/test_pricing_catalog.py`
- Create: `tests/unit/test_pricing_resolve.py`

**Interfaces:**
- Produces frozen `RateComponents`, `RateCard`, `ModelIdentity`, `AttemptUsageTrust`, `PricingSidecar`, `PricingLoadFailure`, `PricingHistory`, `CostSample`, `CanaryCostEstimate`, `SuiteCostEstimate`, `CostAnalysis` exactly in spec field order.
- Produces `provider_for_agent(agent: str) -> str | None`, `resolve_model_identity(agent, configured_model, result) -> ModelIdentity`, `resolve_rate_card(provider, canonical_model, instant) -> RateCard | None`.
- Defines `CATALOG_VERSION = "2026-09-07.1"` and the six initial immutable cards/rates/effective intervals from spec §8.

- [ ] **Step 1: Write RED catalog tests**

Pin exact IDs, Decimal rates, source URLs/check dates, limitations order, conservative `2026-09-07` applicability floors, Gemini `2026-12-31`/`2027-01-01` boundary, inclusive `effective_until`, unique IDs/material snapshots, naive-datetime rejection, and overlapping-card programmer-error behavior.

```python
card = resolve_rate_card("google", "gemini-3.8-flash", datetime(2027, 1, 1, tzinfo=UTC))
assert card is not None
assert card.rates.output == Decimal("7.50")
```

- [ ] **Step 2: Write RED semantic-validation tests**

Exercise finite/non-negative decimal strings only; reject bool/non-string, `NaN`, infinities, negatives, cache-write half-null/range inversion, and invalid effective intervals. Assert OpenAI Terra/Sol cache-write equals exactly 1.25x uncached input and no fabricated future expiry exists.

- [ ] **Step 3: Write RED exact-resolution tests**

Use direct `QualificationResult` attempts with raw `events_jsonl` to pin: exact closed agent/provider mapping; OpenAI canonical + only documented `gpt-5.6 -> gpt-5.6-sol`; explicit Antigravity three-entry mapping only; Claude exact system-init/assistant model paths; alias `sonnet` requires consistent runtime model; malformed non-empty JSON/non-object/present non-string model gives fixed `malformed_model_evidence`; disagreements fail closed; exact configured Claude ID must agree with observed runtime ID.
- [ ] **Step 4: Run Task 2 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
```

Expected: import/collection failure because `qualock.pricing` does not exist.

- [ ] **Step 5: Implement frozen models and immutable catalog**

Define exact spec dataclasses and constants. Build cards from literal decimal strings into validated `Decimal` objects at import/construction time. `resolve_rate_card` requires aware datetimes, normalizes to UTC date, returns exactly one card/`None`, and raises `ValueError` for naive input or overlapping matches.

- [ ] **Step 6: Implement exact fail-closed model resolution**

`provider_for_agent` is a literal closed map. Claude parser reads only spec paths and never leaks raw parse text. Runtime observation is authoritative over configured exact IDs. Codex and Antigravity use only literal tables; no generic suffix/prefix/fuzzy logic.

- [ ] **Step 7: Run Task 2 GREEN/static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/models.py src/qualock/pricing/catalog.py src/qualock/pricing/resolve.py tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing
git diff --check
```

- [ ] **Step 8: Commit Task 2**

```bash
git add src/qualock/pricing/__init__.py src/qualock/pricing/models.py src/qualock/pricing/catalog.py src/qualock/pricing/resolve.py tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py
git commit -m "feat: add offline provider pricing catalog"
```

Reviewer must verify rate data/effective windows exactly match the approved spec, all resolution is exact/fail-closed, and no runtime network code exists.

### Task 3: Pin Runtime Provenance and Write Atomic Best-Effort Sidecars
**Files:**
- Create: `src/qualock/pricing/sidecar.py`
- Modify: `src/qualock/commands.py`
- Create: `tests/unit/test_pricing_sidecar.py`
- Modify: `tests/unit/test_commands.py`

**Interfaces:**
- Produces `build_usage_detail_trust(agent: str, result: QualificationResult) -> tuple[AttemptUsageTrust, ...]`.
- Produces `build_pricing_payload(config, result, run_started_at, run_finished_at) -> dict[str, object]`.
- Produces `write_pricing_sidecar(qualification_dir: Path, payload: dict[str, object]) -> Path`.
- `execute_check` captures `run_started_at` immediately before `QualificationExecutor.run`, writes canonical artifacts, captures `run_finished_at`, then invokes one exception-swallowing pricing boundary.

- [ ] **Step 1: Write RED per-provider trust tests**

Pin one trust record for every started attempt, 1-based `(canary_id, side, repetition)` identity, failed/invalid attempts included, skipped executions excluded. Codex cache-read is observed only if every `turn.completed` has explicit valid `usage.cached_input_tokens`; cache-write always `unobserved`. Claude terminal cache-read is observed only on trustworthy usage and cache-write only when `cache_creation_input_tokens` is explicitly present/valid. Antigravity cache-read observed and cache-write `known_zero`.

```python
trust = build_usage_detail_trust("antigravity", result)
assert trust[0].cache_write_input_tokens_trust == "known_zero"
```

- [ ] **Step 2: Write RED payload/time-precedence tests**

Pin complete trust provenance even for unavailable outcomes. Assert aware timestamps normalize to UTC; invalid/naive/reversed window wins as `invalid_capture_time`; then model failures; then endpoint lookup: both missing -> `no_rate_card`, one missing/different IDs -> `rate_boundary_crossed`, same ID -> priced. Unavailable payload nullability/reason/source rules must match spec §8.1 exactly.

- [ ] **Step 3: Write RED atomic/no-replace writer tests**

Assert serialized rates are strings, JSON is `sort_keys=True`, `indent=2`, UTF-8 with trailing newline; successful publish exposes complete final bytes; existing destination remains byte-identical; injected temp-write/fsync/link failures leave no partial `pricing.json` and clean the temp file where possible.
- [ ] **Step 4: Write RED `execute_check` isolation tests**

Monkeypatch pricing build/write to fail and prove canonical result, verdict, stdout-facing result object, and existing artifacts are unchanged except absent sidecar. Pin unknown-model as a normal unavailable sidecar when I/O succeeds. Assert existing `pricing.json` is never overwritten and that baseline creation never emits a pricing sidecar.

- [ ] **Step 5: Run Task 3 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_sidecar.py tests/unit/test_commands.py -k "pricing or execute_check"
```

Expected: failures because trust/payload/writer/integration are absent.

- [ ] **Step 6: Implement trust, payload, atomic writer, and integration**

Keep raw-event parsing pricing-only and independent from qualification validity. Build payload from exact Task 2 catalog/model APIs. Atomic publication: create temp in qualification directory, write complete bytes, flush + `os.fsync`, `os.link(temp, final)` as same-filesystem no-replace, then unlink temp. Treat unsupported/no-replace failures as advisory. In `execute_check`, preserve existing canonical writer call/result return; the pricing boundary catches every exception.

- [ ] **Step 7: Run Task 3 GREEN/static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_sidecar.py tests/unit/test_commands.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/sidecar.py src/qualock/commands.py tests/unit/test_pricing_sidecar.py tests/unit/test_commands.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing src/qualock/commands.py
git diff --check
```

- [ ] **Step 8: Commit Task 3**

```bash
git add src/qualock/pricing/sidecar.py src/qualock/commands.py tests/unit/test_pricing_sidecar.py tests/unit/test_commands.py
git commit -m "feat: pin qualification pricing provenance"
```

Reviewer must verify sidecar failure cannot affect qualification semantics, cache trust never upgrades malformed/absent detail, and canonical artifacts are written before any sidecar attempt.

### Task 4: Load Pricing Sidecars Tolerantly and Enforce Snapshot Consistency

**Files:**
- Modify: `src/qualock/pricing/sidecar.py`
- Modify: `src/qualock/pricing/__init__.py`
- Create: `tests/unit/test_pricing_loader.py`

**Interfaces:**
- Consumes `HistorySummary.loaded` only; does not rescan arbitrary report directories.
- Produces `scan_pricing(summary: HistorySummary) -> PricingHistory` with parsed `PricingSidecar`, older/unpinned IDs, and fixed `PricingLoadFailure`s.
- [ ] **Step 1: Write RED tolerant-reader tests**

Create loaded-report fixtures whose qualification directories contain: no sidecar; unreadable/non-UTF-8; invalid JSON; non-object; unsupported schema; qualification ID mismatch; malformed typed/semantic fields; valid unavailable sidecar; valid priced sidecar. Assert exact fixed reasons from spec and prove malformed pricing never removes the owning report from normal `scan_results`/`qualock history` analysis.

- [ ] **Step 2: Write RED trust-binding validation tests**

Add `test_pricing_scan_is_path_neutral`: build qualification directories through `Path`, include names/order that would fail slash-splitting assumptions, and assert discovery/order uses `qualification_dir`/`Path.name` without OS-specific separators.

For the owning `HistoricalExecution`, require exact one-to-one started attempt identities. Missing, duplicate, extra, invalid side/repetition, or `known_zero` paired with nonzero persisted numeric value makes the whole pricing sidecar `malformed pricing sidecar`.

- [ ] **Step 3: Write RED material-snapshot consistency tests**

Two sidecars sharing one `rate_card_id` but differing in provider, canonical model, source URL/check date, effective interval, any parsed rate, or ordered limitation must both be removed from `records` and returned as `rate-card snapshot mismatch`. `catalog_version` differences alone do not trigger mismatch. Historical validation must not call today's catalog.

- [ ] **Step 4: Run Task 4 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_loader.py
```

Expected: import/function failures because `scan_pricing` does not exist.

- [ ] **Step 5: Implement strict parser + deterministic scanner**

Read `pricing.json` only for successfully loaded #41 reports. Catch only file/Unicode/JSON boundaries into fixed reasons; parse aware timestamps to UTC; parse Decimal strings under Task 2 rules; enforce priced/unavailable cross-field contracts; validate trust identities against owning report. After individual parsing, perform all-or-nothing material-snapshot conflict removal per rate-card ID.

- [ ] **Step 6: Run Task 4 GREEN/static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_loader.py tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py tests/unit/test_pricing_loader.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing
git diff --check
```

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/pricing/sidecar.py src/qualock/pricing/__init__.py tests/unit/test_pricing_loader.py
git commit -m "feat: load pinned pricing history"
```

Reviewer must verify pricing sees only #41-loaded reports, never leaks paths/raw exceptions, and conflicting snapshots fail closed for every affected sidecar.
### Task 5: Calculate Decimal Costs and Analyze Current Model/Rate Cohorts

**Files:**
- Create: `src/qualock/pricing/calculate.py`
- Create: `src/qualock/pricing/analysis.py`
- Modify: `src/qualock/pricing/__init__.py`
- Create: `tests/unit/test_pricing_calculate.py`
- Create: `tests/unit/test_pricing_analysis.py`

**Interfaces:**
- Produces `price_execution(execution, trust_by_identity, rates) -> CostSample | None`.
- Produces `analyze_cost(summary, pricing, current_canary_ids, *, agent, configured_model, reasoning_effort) -> CostAnalysis`.
- Uses only pinned `PricingSidecar.rates/limitations`; must not call `resolve_rate_card` during history analysis.

- [ ] **Step 1: Write RED per-execution pricing tests**

Pin shared pairing prerequisite; `usage_observed`; non-negative input/output; exact trust binding; `known_zero` requires numeric zero; unobserved cache category is unpriceable even when numeric zero; cached/cache-write subtraction from total input; nonzero category with missing rate unpriceable; reasoning <= output and never double-counted; failed/invalid outcomes and malformed duration do not affect cost eligibility.

```python
sample = price_execution(execution, trust, rates)
assert sample == CostSample(Decimal("0.0000225"), Decimal("0.0000225"))
```

Use explicit hand-calculated fixtures for exact zero-cache, cached-input, cache-write, Claude lower/upper range, and Gemini reasoning subset cases.

- [ ] **Step 2: Write RED cohort/classification tests**

Create reports spanning: older/unpinned, pricing failure, config mismatch, current-config unavailable, multiple priced cohorts, selected latest cohort, removed historical canary. Assert primary classifications are disjoint/exhaustive and counters exact; latest cohort uses greatest `run_finished_at`, lexical `(canonical_model, rate_card_id)` tie-break; no cross-model/card mixing.

- [ ] **Step 3: Write RED medians/suite tests**

Assert Decimal odd/even medians without cent rounding, current-canary order, removed historical canaries excluded, one selected qualification contributing multiple canaries increments `priceable_qualification_runs` once, complete suite sums unrounded medians, any missing current canary yields both suite bounds `None` and config-ordered missing list.

- [ ] **Step 4: Pin historical-snapshot independence**

Monkeypatch/change today's catalog and prove `analyze_cost` output is byte/value-identical because it consumes pinned sidecar rates/limitations only.
- [ ] **Step 5: Run Task 5 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
```

Expected: failures because calculator/analysis are not implemented.

- [ ] **Step 6: Implement pure Decimal calculator**

Reuse the same side/repetition pairing semantics as #41 without importing private history helpers. Compute `uncached_input = input - cached - cache_write`; reject negative/inconsistent values. Require rates only for nonzero categories. Sum all attempts in an execution into one lower/upper `CostSample`; no partial execution sample.

- [ ] **Step 7: Implement deterministic cohort analysis**

Classify every successfully loaded report once under spec precedence. Match current config exactly on agent/configured model/reasoning effort before cohorting. Partition by exact `(canonical_model, rate_card_id)`, select latest/tie-break, compute current-canary samples/medians/suite, carry pinned ordered limitations, and count priceable qualification IDs distinctly.

- [ ] **Step 8: Run Task 5 GREEN/static gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/calculate.py src/qualock/pricing/analysis.py src/qualock/pricing/__init__.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/pricing
git diff --check
```

- [ ] **Step 9: Commit Task 5**

```bash
git add src/qualock/pricing/calculate.py src/qualock/pricing/analysis.py src/qualock/pricing/__init__.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py
git commit -m "feat: analyze historical reference costs"
```

Reviewer must verify every monetary value stays Decimal, current-cohort selection is deterministic, classification counts are exact, and no catalog re-resolution occurs for historical priced runs.

### Task 6: Render and Expose Read-Only `qualock cost`

**Files:**
- Create: `src/qualock/pricing/render.py`
- Modify: `src/qualock/pricing/__init__.py`
- Modify: `src/qualock/commands.py`
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_pricing_render.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_cli.py`
**Interfaces:**
- Produces `render_cost_text(analysis: CostAnalysis) -> str`.
- Produces `execute_cost(root: Path) -> CostAnalysis` composing `load_project`, `scan_results`, `scan_pricing`, and `analyze_cost`.
- Adds exactly argument-free `@app.command("cost") def cost_command() -> None` with history-style exception mapping.

- [ ] **Step 1: Write RED renderer tests**

Pin first line `QuaLock Reference Cost`; selected cohort model/rate card block; complete exact suite, complete range, partial suite, selected cohort with zero samples, and no-selected-cohort guidance. Assert `ROUND_HALF_EVEN` cents only at final display, lower/upper independently, collapsed display range becomes one amount, per-canary order follows current config, all History counters render including zeros, pricing failures are lexical `<qualification_id>: <fixed reason>` rows only, and generic Basis/not-actual-bill text is always present.

- [ ] **Step 2: Pin limitations/output safety tests**

Render ordered pinned limitations deduplicated by first occurrence only when a cohort exists. Assert absolute paths, raw exceptions, provider responses, credentials, "actual bill", monetary gating, and current-catalog-derived limitation text cannot appear through renderer-owned diagnostics.

- [ ] **Step 3: Write RED `execute_cost` composition tests**

Assert current config values passed verbatim (`agent.name`, `model.effective_model`, `reasoning_effort`), current canary IDs preserve config order, results path is `.qualock/results`, empty suite raises exact `CommandError("no canaries found")`, and config/canary exceptions are not wrapped.

Add `test_pricing_package_has_no_network_clients_or_urlopen_calls`: parse every `src/qualock/pricing/*.py` AST and fail on imports/calls from `httpx`, `requests`, `urllib.request`, or socket/network-client construction. Also run a real local-artifact `qualock cost` invocation with outbound socket connection monkeypatched to raise, proving the read-only pricing path succeeds without network access.

- [ ] **Step 4: Write RED CLI/E2E safety tests**

Pin valid zero history/no cohort/unavailable model exit 0; `(ConfigError, CanaryLoadError, CommandError, ValueError)` exit 3 with `markup=False`; unexpected exception exit 1; extra positional argument rejected; help contains no `--max-cost`, pricing JSON, budget/filter flags. Real cold-start must not create results dir; real invocation snapshots bytes + `st_mtime_ns` of every artifact and proves exact preservation; mixed project-protection artifacts remain silently excluded; `qualock history` golden/public output remains unchanged.

Add `test_cost_real_invocation_is_path_neutral`: create the valid project/results tree under a nested `tmp_path` containing spaces, invoke the real `qualock cost` after `monkeypatch.chdir(project_root)`, and assert successful local discovery/output without any string separator assumptions. The same test must run unmodified on Linux and `windows-latest`.

- [ ] **Step 5: Run Task 6 RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py -k "cost or history"
```

Expected: failures because renderer/command/CLI do not exist.
- [ ] **Step 6: Implement renderer, composition, and CLI handler**

`render_cost_text` consumes `CostAnalysis` only and never reads files/catalog. `execute_cost` loads project, rejects empty current suite, calls `scan_results(project_dir(root) / "results")`, `scan_pricing(summary)`, then `analyze_cost(...)`. `cost_command` mirrors history's exact exit mapping and prints with `markup=False`; no flags/arguments are added.

- [ ] **Step 7: Run Task 6 GREEN + path-neutral regressions**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py -k "cost or history"
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/pricing/render.py src/qualock/commands.py src/qualock/cli.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py
git diff --check
```

Path-neutral tests must use `Path`/`Path.name` only and contain no production split-on-`/` or Windows-only branch; the same suite runs on `windows-latest`.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/qualock/pricing/render.py src/qualock/pricing/__init__.py src/qualock/commands.py src/qualock/cli.py tests/unit/test_pricing_render.py tests/unit/test_commands.py tests/unit/test_cli.py
git commit -m "feat: expose local reference cost estimates"
```

Reviewer must verify `cost` is argument-free/read-only, zero history is normal exit 0, empty suite/config errors map to 3, and history/check user-facing behavior is unchanged apart from best-effort sidecar evidence creation during checks.

### Task 7: Verify, Review, Integrate, Document, and Close Batch #42

**Files:**
- Modify only after implementation-head CI is green: `README.md`, `ROADMAP.md`, `docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md` status line.
- No production file changes unless a verified review/CI finding enters the formal fix loop.

**Interfaces:**
- Consumes complete Tasks 1–6 implementation.
- Produces reviewed CI-green PR merged to `main` by rebase merge.
- Opus high is reserved exactly once for the final docs-inclusive whole-branch review.

- [ ] **Step 1: Run fresh implementation-head functional gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_pricing_catalog.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_sidecar.py tests/unit/test_pricing_loader.py tests/unit/test_pricing_calculate.py tests/unit/test_pricing_analysis.py tests/unit/test_pricing_render.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py tests/unit/test_commands.py tests/unit/test_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
git diff --check
```

Record exact HEAD and fresh counts in the SDD ledger.
- [ ] **Step 2: Run strict static-baseline gates**

```bash
set +e
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock > /tmp/b42-mypy.txt 2>&1
mypy_rc=$?
set -e
test "$mypy_rc" -eq 1
test "$(grep -F -c '[import-untyped]' /tmp/b42-mypy.txt)" -eq 3
grep -F 'src/qualock/config/io.py:' /tmp/b42-mypy.txt
grep -F 'src/qualock/canary/loader.py:' /tmp/b42-mypy.txt
grep -F 'src/qualock/project_setup/config.py:' /tmp/b42-mypy.txt
```

Run Ruff on every #42 changed Python file and require clean. For full-tree no-new-debt, archive exact feature base `5800a814e295cf1126bca227bfb043438fd22c27`, run the same `/home/pacmap/qualock-easy/.venv/bin/ruff check .` in base archive and HEAD, and require exit code plus canonical diagnostics byte-equivalent after replacing only absolute temp-root prefixes if any.

- [ ] **Step 3: Prove protected scopes unchanged**

```bash
git diff --name-only 5800a814e295cf1126bca227bfb043438fd22c27..HEAD -- \
  src/qualock/qualification/policy.py src/qualock/run/executor.py \
  src/qualock/agents src/qualock/evidence src/qualock/github_pr src/qualock/release_monitor \
  src/qualock/version_bisect src/qualock/scheduler src/qualock/source \
  src/qualock/canary src/qualock/config pyproject.toml
```

Expected: no output except no files at all; Task 2 model resolution lives under `pricing/`, not provider adapters/config schema.

- [ ] **Step 4: Run whole-implementation review before first push**

Generate a review package for exact `5800a814..HEAD`. Use Sonnet high. Include every deferred per-task Minor from the ledger. Block on Critical/Important and use SDD fix loop; do not use Opus here.

- [ ] **Step 5: Push implementation head and open non-draft PR**

Only after Steps 1–4 are green/approved, push `feat/provider-cost-estimates` and create a PR whose summary states: pinned offline pricing provenance; read-only `qualock cost`; historical rates from sidecar snapshots; advisory list-rate reference not actual bill; no policy/budget/network changes. Do not claim docs delivered yet.
- [ ] **Step 6: Require implementation-head CI green**

```bash
gh pr checks --watch --fail-fast
```

Require Python 3.11/3.12/3.13 and `windows-test` all PASS. Any CI defect is reproduced locally, fixed by TDD through one worker, scoped-reviewed, then Steps 1–3 rerun on the new exact HEAD before a fresh CI run.

- [ ] **Step 7: Update docs only after implementation-head CI is green**

README: document `qualock cost` as offline API-equivalent standard public list-rate reference, not actual billing; explain exact/range/unavailable and pinned provenance. ROADMAP: move #42 to Delivered without implying gating/invoice support. Spec: change only status from Proposed to Delivered/Implemented wording consistent with actual merged feature scope.

```bash
git add README.md ROADMAP.md docs/superpowers/specs/2026-09-07-provider-specific-reference-cost-estimates-design.md
git commit -m "docs: document provider reference cost estimates"
```

- [ ] **Step 8: Re-run docs-inclusive exact-head gates and CI**

Repeat Steps 1–3 on the docs-inclusive HEAD, push, and require a fresh all-green Linux 3.11/3.12/3.13 + Windows run. Documentation cannot bypass functional/static gates.

- [ ] **Step 9: Run final whole-branch review with Opus high exactly once**

Generate exact base-to-HEAD review package including code/tests/docs and all deferred ledger minors/rulings. Use Claude Opus high once. If findings exist, SDD allows one final fix wave containing all findings, one scoped re-review, then fresh exact-head local gates + fresh 4/4 CI. Do not run a second Opus whole-branch review.

- [ ] **Step 10: Identity-check and rebase-merge**

Immediately before merge require local HEAD = remote branch head = PR `headRefOid`, latest PR checks green, PR mergeable/CLEAN. Rebase-merge without deleting the host-owned worktree branch, then verify PR `MERGED`, fetch `origin/main`, record resulting main SHA, and prove final branch tree equals merged main tree.

- [ ] **Step 11: Close Batch #42 bookkeeping**

Record task commits/reviews, exact test/static/CI evidence, final review verdict/fix wave, PR/merge SHA, and all `Ruling:` lines. Before deleting only this plan's `.superpowers/sdd/...` scratch directory, surface every ruling to the user under `Rulings I made`. No tag/release/package publish.

## TDD Obligation Coverage Matrix

Every numbered obligation in spec §17 is assigned below; implementers may add more tests but must not leave any listed row uncovered.
| # | Task | Named covering test |
|---:|---:|---|
| 1 | 4 | `test_missing_sidecar_is_older_unpinned` |
| 2 | 1/4 | `test_missing_sidecar_does_not_change_history_loading` |
| 3 | 4 | `test_invalid_pricing_json_is_cost_only_failure` |
| 4 | 4 | `test_non_utf8_pricing_sidecar_is_tolerated` |
| 5 | 2 | `test_claude_sonnet_alias_uses_consistent_runtime_model` |
| 6 | 2 | `test_claude_alias_without_runtime_model_fails_closed` |
| 7 | 2 | `test_claude_conflicting_runtime_models_fail_closed` |
| 8 | 2 | `test_claude_exact_configured_model_requires_observed_agreement` |
| 9 | 2 | `test_openai_exact_canonical_model_resolves` |
| 10 | 2 | `test_openai_documented_gpt56_alias_resolves_only_to_sol` |
| 11 | 2 | `test_openai_unknown_model_has_no_fuzzy_resolution` |
| 12 | 2 | `test_antigravity_three_explicit_flash_aliases_resolve` |
| 13 | 2 | `test_antigravity_unlisted_suffix_fails_closed` |
| 14 | 2 | `test_catalog_rate_card_ids_and_snapshots_are_unique` |
| 15 | 2 | `test_gemini_rate_boundary_selects_published_cards` |
| 16 | 2 | `test_openai_cache_write_multiplier_and_no_fake_expiry` |
| 17 | 5 | `test_price_execution_decimal_exact_with_trusted_zero_cache` |
| 18 | 5 | `test_cached_input_is_not_double_counted` |
| 19 | 5 | `test_cache_write_input_is_not_double_counted` |
| 20 | 5 | `test_reasoning_output_is_not_double_counted` |
| 21 | 5 | `test_negative_or_inconsistent_subsets_are_unpriceable` |
| 22 | 5 | `test_unobserved_usage_is_unpriceable` |
| 23 | 5 | `test_nonzero_category_without_rate_is_unpriceable` |
| 24 | 5 | `test_claude_cache_write_produces_lower_upper_range` |
| 25 | 3/5 | `test_claude_explicit_zero_cache_write_is_exact_but_absent_is_unpriceable` |
| 26 | 5 | `test_failed_or_invalid_outcome_does_not_block_cost_sample` |
| 27 | 5 | `test_malformed_duration_does_not_block_cost_sample` |
| 28 | 5 | `test_skipped_execution_has_no_cost_sample` |
| 29 | 5 | `test_incomplete_or_mismatched_pairing_is_unpriceable` |
| 30 | 5 | `test_current_config_agent_model_effort_match_is_exact` |
| 31 | 5 | `test_different_canonical_models_never_mix` |
| 32 | 5 | `test_different_rate_card_ids_never_mix` |
| 33 | 5 | `test_latest_cohort_uses_finished_at_then_lexical_tiebreak` |
| 34 | 5 | `test_removed_historical_canaries_are_ignored` |
| 35 | 5 | `test_per_canary_medians_are_decimal_and_unrounded` |
| 36 | 5 | `test_even_decimal_median_uses_middle_mean` |
| 37 | 5 | `test_suite_sums_unrounded_per_canary_medians` |
| 38 | 5 | `test_missing_current_canary_makes_suite_unavailable` |
| 39 | 5 | `test_missing_cost_canaries_preserve_config_order` |
| 40 | 6 | `test_money_display_uses_half_even_cents_only_at_boundary` |
| 41 | 6 | `test_exact_range_and_collapsed_range_rendering` |
| 42 | 6 | `test_basis_and_not_actual_bill_text_always_render` |
| 43 | 6 | `test_pinned_limitations_render_deterministically_deduplicated` |
| 44 | 6 | `test_cost_zero_history_exits_zero_without_creating_results` |
| 45 | 6 | `test_cost_no_matching_priced_cohort_exits_zero_with_guidance` |
| 46 | 6 | `test_execute_cost_empty_suite_raises_exact_command_error` |
| 47 | 6 | `test_cost_configuration_failures_exit_3` |
| 48 | 6 | `test_cost_real_invocation_preserves_bytes_and_mtimes` |
| 49 | 4/6 | `test_cost_mixed_project_protection_reports_are_silently_excluded` |
| 50 | 3 | `test_pricing_writer_failure_preserves_check_result_and_artifacts` |
| 51 | 3 | `test_unknown_model_check_writes_unavailable_sidecar` |
| 52 | 3 | `test_sidecar_serializes_rates_as_decimal_strings` |
| 53 | 3 | `test_existing_sidecar_is_immutable_across_catalog_change` |
| 54 | 1/6 | `test_qualock_history_public_output_is_unchanged` |
| 55 | 1 | `test_batch41_analysis_values_ignore_new_usage_details` |
| 56 | 6/7 | `test_pricing_package_has_no_network_clients_or_urlopen_calls` + protected review gate |
| 57 | 6 | `test_cost_help_has_no_budget_json_or_pricing_options` |
| 58 | 7 | `test/protected-scope git diff gate` |
| 59 | 4/6 | `test_pricing_scan_is_path_neutral` + `test_cost_real_invocation_is_path_neutral` |
| 60 | 7 | GitHub CI Python 3.11/3.12/3.13 + `windows-test` |
| 61 | 4 | `test_pricing_qualification_id_mismatch_cannot_rebind` |
| 62 | 2 | `test_provider_for_agent_is_exact_closed_map` |
| 63 | 3 | `test_payload_crossing_rate_boundary_is_unavailable` |
| 64 | 5 | `test_historical_analysis_uses_pinned_rates_not_catalog` |
| 65 | 3 | `test_existing_pricing_sidecar_is_never_overwritten` |
| 66 | 2 | `test_malformed_claude_model_evidence_has_fixed_reason` |
| 67 | 2 | `test_catalog_floor_and_inclusive_effective_until` |
| 68 | 3 | `test_codex_cache_read_requires_every_completed_turn_detail` |
| 69 | 3 | `test_claude_cache_trust_requires_terminal_explicit_fields` |
| 70 | 3/5 | `test_antigravity_cache_write_known_zero_requires_numeric_zero` |
| 71 | 4 | `test_usage_detail_trust_identity_set_must_match_report_exactly` |
| 72 | 5 | `test_unobserved_zero_cache_category_is_still_unpriceable` |
| 73 | 4 | `test_sidecar_basis_currency_agent_provider_and_nullability_validation` |
| 74 | 2/4 | `test_rates_reject_nonfinite_negative_nonstring_and_reversed_range` |
| 75 | 2/4 | `test_sidecar_timestamps_and_effective_intervals_are_validated` |
| 76 | 3 | `test_build_payload_temporal_failure_precedence` |
| 77 | 4 | `test_same_rate_card_id_snapshot_mismatch_invalidates_all_records` |
| 78 | 5 | `test_same_latest_timestamp_uses_lexicographically_smallest_cohort` |
| 79 | 5 | `test_primary_classifications_and_priceable_run_count_are_exact` |
| 80 | 6 | `test_renderer_complete_partial_and_zero_sample_selected_cohort` |
| 81 | 6 | `test_pricing_failure_rows_are_lexical_and_non_sensitive` |
| 82 | 3 | `test_atomic_no_replace_publication_and_temp_cleanup` |
| 83 | 6 | `test_unavailable_sidecars_without_cohort_render_truthful_exit_zero` |
| 84 | 2 | `test_claude_model_extraction_exact_paths_and_malformed_cases` |
| 85 | 5/6 | `test_historical_rates_and_limitations_ignore_current_catalog` |
| 86 | 3 | `test_usage_trust_emits_one_record_per_started_attempt_only` |
| 87 | 3/5 | `test_unavailable_sidecar_keeps_trust_but_cannot_enter_priced_cohort` |

## Plan Self-Review

- **Spec coverage:** Tasks 1–6 cover history compatibility, exact catalog/model provenance, per-component trust, immutable sidecars, tolerant parsing, Decimal pricing/cohorts, rendering/CLI/read-only behavior. Task 7 covers static/CI/review/docs/merge gates. The matrix maps every spec §17 obligation 1–87.
- **Dependency direction:** Task 1 remains pricing-free; Tasks 2–6 depend from pricing toward history/config/qualification data only. No task introduces a history→pricing import.
- **Type consistency:** Every required function/dataclass from spec §11 is produced once before downstream consumption; names and signatures are identical across task Interfaces blocks.
- **Failure isolation:** Expected model/rate unavailability becomes a sidecar payload; pricing generation/write exceptions are swallowed only at the advisory `execute_check` boundary; sidecar reader failures affect cost only.
- **No placeholders:** Every task names exact files, RED/GREEN commands, implementation rules, commit boundary, and reviewer rejection criteria; every implementation decision needed by a worker is explicit.
- **Model budget:** Opus is unused until the one final docs-inclusive review; whole-implementation pre-push review uses Sonnet high.

## Execution Handoff

Execute with **Subagent-Driven Development**. The user has already delegated routine workflow decisions, so proceed continuously: create this plan's SDD workspace/ledger, run preflight interface-conflict table, dispatch one fresh implementer per task, independent review each task, formal fix loops for Critical/Important findings, then Task 7 integration gates. Stop only for the four SDD stop classes or an explicit user instruction.
