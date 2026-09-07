# Historical Canary Effectiveness + Runtime/Token Estimates (Read-Only, Local Artifacts Only)

- **Batch:** #41
- **Status:** Delivered (implemented; see README.md § "Historical qualification insights")
- **Branch:** `feat/history-estimates`
- **Base:** `f6410c610642b4e271a9932172326006f37cfaa6`
- **Related:** `docs/superpowers/specs/2026-09-07-token-aware-qualification-budget-design.md` (Batch #40, source of `Usage.observed`/`total_tokens`/`attempts_used`/`observed_tokens` this batch reads), `docs/superpowers/specs/2026-09-04-budgeted-qualification-design.md` (Batch #32, source of `max_attempts`/skipped-canary semantics this batch must recognize and exclude)
- **Author context:** design decisions below are pre-approved; this document formalizes them into binding spec language, invariants, and review gates. No open questions are left for the reader to resolve.

## 1. Summary

This batch adds **read-only historical analytics** over the local qualification artifacts QuaLock already writes under `.qualock/results/<qualification_id>/report.json`. It answers two questions from data already on disk:

1. **Effectiveness:** for each currently-configured canary, how often has a completed historical execution actually detected a candidate regression (a "detection"), versus not (a "non-detection")?
2. **Estimation:** for each currently-configured canary, and for the current full suite, what is the median observed model-attempt runtime and median observed model token usage, based on historical executions?

Both are exposed through a new, low-tech CLI surface that reads existing artifacts and renders a plain-text summary. **No new database, index file, or `history.jsonl` is introduced.** **No network or provider calls, and no pricing/cost/billing computation of any kind, are introduced.** Historical analytics are strictly advisory: they MUST NOT alter qualification policy, executor admission or execution order, `max_attempts`/`max_tokens` semantics, baseline creation, `monitor`, `bisect`, GitHub PR qualification, the scheduler, or the canary/config schema.

## 2. Goals

- Let operators see, from data already produced by `qualock check`, how often each currently configured canary has actually caught a regression historically, without inventing new tracking infrastructure.
- Let operators see a plain, honestly-scoped estimate of model-attempt runtime and model token consumption per canary and for the current full suite, built strictly from already-observed artifact data.
- Tolerate a heterogeneous, partially malformed, and partially pre-Batch-#40 artifact corpus without either crashing or silently fabricating numbers.
- Keep the new surface strictly read-only: scanning artifacts must never rewrite, migrate, or delete them.

## 3. Non-Goals (Batch #41 Boundary)

The following are explicitly out of scope for this batch and must not be introduced as side effects:

- Any new persisted index, database, cache, or `history.jsonl`-style aggregate file. Every invocation re-scans `report.json` artifacts directly.
- Any network call, provider API call, or pricing/billing/cost computation of any kind, for any provider. Batch #42 owns provider-specific monetary estimates, and even then only as advisory output outside qualification pass/fail policy.
- Any change to qualification policy (`qualification/policy.py`), executor admission/order (`run/executor.py`), `max_attempts`/`max_tokens` semantics (Batch #32/#40), baseline creation, `monitor`, `bisect`, GitHub PR qualification, the scheduler, or canary/config schema.
- A JSON output flag or a stable public API for `#42` or other external consumers. Internal dataclasses/functions are written to be reusable by Batch #42, but no such flag/API is required or added in this batch.
- Rewriting, migrating, deleting, or otherwise mutating any `report.json`, `qualification.json`, or `baseline.json` artifact. The loader is scan-only.
- Reason-string parsing of `CanaryExecution.reason` / `QualificationVerdict.reasons`, or any dependency on `qualification/policy.py`, to determine effectiveness or eligibility. All eligibility determinations in this batch are computed structurally from `attempts`, never from prose reason strings.
- Historical inclusion of obsolete canary IDs (IDs that appear in historical artifacts but are absent from the currently configured suite) in ranking or current-suite estimates.

### 3.1 Protected Surfaces

Unless a change is *directly* necessitated by this batch's goals, the following remain untouched:

- `agents/*`
- `github_pr/*`
- `release_monitor/*`
- `version_bisect/*`
- `scheduler/*`
- `source/*`
- `qualification/policy.py`
- `run/executor.py`
- Canary schema (`canary/models.py`) and config schema (`config/models.py`)
- `pyproject.toml`

### 3.2 Expected Surfaces

- New package `src/qualock/history/` containing `__init__.py`, `models.py`, `loader.py`, `analysis.py`, `render.py`.
- Bounded CLI/`commands.py` wiring to expose the new low-tech command (new function(s) in `commands.py` and a new `typer` command in `cli.py`; no changes to existing commands' behavior).
- `README.md` / `ROADMAP.md`, updated only after implementation, exact-head verification, CI, and independent review land (§13).

Nothing in `evidence/storage.py`, `qualification/models.py`, `run/schedule.py`, or `run/backend.py` needs to change for this batch; the loader in §5 reads the existing `report.json` shape as produced by those modules today (§4).

## 4. Artifact Source of Truth

### 4.1 Location and Scope

The loader scans `.qualock/results/*/report.json` under the current project root (`project_dir(root) / "results"`, matching the existing `write_qualification_artifacts` location in `evidence/storage.py`). Only directories that are **qualification directories** — i.e., contain a `report.json` file — are considered; any other file or subdirectory under `results/` (including `baseline.json`-only directories written by `execute_baseline`, which never contain `report.json`) is silently skipped, not treated as malformed. The loader never opens `qualification.json` or `report.md`; `report.json` alone is authoritative for this batch.

**Invariant L0 (cold-start results absence):** If `results_dir` does not exist, `scan_results(results_dir)` MUST return `HistorySummary(loaded=(), ignored=())`, exactly as for an existing empty directory. It MUST NOT create the directory and MUST NOT raise `FileNotFoundError`. This is the normal first-run path for a valid configured project that has not produced a qualification report yet.

Directory scanning uses only `pathlib.Path` traversal (`Path.iterdir()` / `Path.glob()`), performs no shell invocation, and makes no assumption about path separators, drive letters, or case sensitivity beyond what `pathlib` already normalizes — this keeps scanning Windows/path-neutral per this repo's existing cross-platform expectations (see the Batch #37 Windows-compatibility spec).

**Invariant L1 (zero writes):** The loader (`history/loader.py`) MUST NOT create, modify, rename, or delete any file or directory under `.qualock/results/`, or anywhere else. It opens files strictly for reading (`Path.read_text` / equivalent read-only I/O). This is proven by an explicit test (§11).

**Invariant L2 (deterministic scan order):** `scan_results` iterates qualification directories under `results/` in ascending sort order by directory name (`sorted(Path.iterdir(...))` or equivalent), so `HistorySummary.loaded`/`ignored` order — and therefore every downstream computation and rendered listing that is not itself independently sorted — is deterministic across runs and platforms.

### 4.2 `report.json` Shape Consumed

`report.json` is the `render_json(result)` output for a `QualificationResult` (see `report/render.py`, `qualification/models.py`), i.e. an `asdict()`-shaped JSON object with (at minimum, fields the loader depends on):

```
{
  "qualification_id": str,
  "baseline_version": str,
  "candidate_version": str,
  "verdict": "pass" | "warn" | "block" | "incomplete",
  "executions": [
    {
      "canary_id": str,
      "critical": bool,
      "prepared_image_digest": str,
      "attempts": [
        {
          "side": "baseline" | "candidate",
          "repetition": int,
          "success": bool,
          "valid": bool,
          "duration_ms": int,
          "usage": {
            "input_tokens": int,
            "cached_input_tokens": int,
            "cache_write_input_tokens": int,
            "output_tokens": int,
            "reasoning_output_tokens": int,
            "observed": bool
          },
          "invalid_reason": str | null,
          "events_jsonl": str,
          "protected_path_violations": [str, ...]
        },
        ...
      ],
      "baseline_successes": int,
      "candidate_successes": int,
      "baseline_valid": int,
      "candidate_valid": int,
      "verdict": "pass" | "warn" | "block" | "incomplete",
      "reason": str
    },
    ...
  ],
  "reasons": [str, ...],
  "run_order": [[str, str, int], ...],
  "max_attempts": int | null,
  "max_tokens": int | null,
  "attempts_used": int,
  "observed_tokens": int | null
}
```

Pre-Batch-#40 artifacts lack `usage.observed`, `usage.cache_write_input_tokens`, `max_attempts`, `max_tokens`, `attempts_used`, and `observed_tokens` entirely (those keys are simply absent from the JSON, since they did not exist in the model at the time they were written). §6.4 defines exactly how the loader tolerates this.

The loader depends on `executions[].canary_id`, `executions[].attempts[]` (with `side`, `repetition`, `success`, `valid`, `duration_ms`, and optionally `usage`), and top-level `qualification_id`. It does **not** depend on `verdict`, `reason`, `reasons`, `run_order`, `max_attempts`, `max_tokens`, `attempts_used`, or `observed_tokens` for any effectiveness/cost computation — those fields are policy/executor-owned narrative, not structural fact, and reading them would violate the reason-string-independence non-goal in §3.

## 5. Package Layout

### 5.1 Files

```
src/qualock/history/
  __init__.py     # re-exports the small public surface used by commands.py
  models.py       # frozen dataclasses: HistoricalAttempt, HistoricalExecution, LoadedReport,
                   # ReportLoadFailure, CanaryEffectiveness, CanaryEstimate, SuiteEstimate,
                   # HistorySummary, HistoryAnalysis
  loader.py       # scan_results(results_dir: Path) -> HistorySummary  (read-only; see Invariant L1)
  analysis.py      # analyze_history(summary, current_canary_ids) -> HistoryAnalysis:
                   # effectiveness ranking, per-canary and suite estimates
  render.py        # render_history_text(analysis) -> str  (low-tech output, §10)
```

`analysis.py` takes the currently configured canary list (from `load_project(root)` / `canary.loader.load_suite`) as an explicit parameter; it never re-derives "current" canary IDs from historical data. This is what makes "current configured canary IDs are the actionable universe" (§7) enforceable and testable in isolation from the loader.

### 5.2 Model and Function Interface Sketch

This section pins concrete field-level shapes; implementation must match these exactly (or record and justify a minor correction here before merging).

```python
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class HistoricalAttempt:
    side: str | None
    repetition: int | None
    success: bool | None
    valid: bool | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    usage_observed: bool = False
```

`loader.py` populates every field above by normalizing the raw JSON attempt object: a field that is absent, of the wrong JSON type, or (for `repetition`/`duration_ms`/`input_tokens`/`output_tokens`) a JSON boolean becomes `None` (or `False` for `usage_observed`) rather than raising or invalidating anything. A `bool` value is never accepted where an `int` is expected — Python's `bool` is a subclass of `int`, so the loader explicitly rejects `isinstance(value, bool)` before accepting an `int`-typed field. This single normalized shape is exactly what §6's independent per-metric prerequisites are evaluated against; there is no separate "invalid attempt" flag stored on the model — validity is a per-metric property computed by `analysis.py`, never a property recorded on the attempt itself.

```python
@dataclass(frozen=True)
class HistoricalExecution:
    canary_id: str
    attempts: tuple[HistoricalAttempt, ...]

@dataclass(frozen=True)
class LoadedReport:
    qualification_id: str
    qualification_dir: Path
    executions: tuple[HistoricalExecution, ...]

@dataclass(frozen=True)
class ReportLoadFailure:
    qualification_dir: Path
    reason: str

@dataclass(frozen=True)
class HistorySummary:
    loaded: tuple[LoadedReport, ...]
    ignored: tuple[ReportLoadFailure, ...]

@dataclass(frozen=True)
class CanaryEffectiveness:
    canary_id: str
    eligible_samples: int
    detections: int
    detection_rate: float | None

@dataclass(frozen=True)
class CanaryEstimate:
    canary_id: str
    runtime_samples_ms: tuple[int, ...]
    token_samples: tuple[int, ...]
    runtime_median_ms: float | None
    token_median: float | None

@dataclass(frozen=True)
class SuiteEstimate:
    runtime_ms: float | None
    tokens: float | None
    missing_runtime_canaries: tuple[str, ...]
    missing_token_canaries: tuple[str, ...]

@dataclass(frozen=True)
class HistoryAnalysis:
    loaded_reports: int
    ignored_reports: tuple[ReportLoadFailure, ...]
    ranked: tuple[CanaryEffectiveness, ...]
    not_enough_history: tuple[CanaryEffectiveness, ...]
    per_canary_estimates: tuple[CanaryEstimate, ...]
    suite_estimate: SuiteEstimate
```

### 5.3 Function Signatures and CLI Wiring

```python
def scan_results(results_dir: Path) -> HistorySummary: ...

def analyze_history(
    summary: HistorySummary,
    current_canary_ids: Sequence[str],
) -> HistoryAnalysis: ...

def render_history_text(analysis: HistoryAnalysis) -> str: ...

# commands.py
def execute_history(root: Path) -> HistoryAnalysis: ...
```

`commands.execute_history` calls the existing `load_project(root)` to obtain the config and configured canary suite. It MUST then mirror `execute_check`/`execute_baseline` by raising `CommandError("no canaries found")` when that suite is empty; analysis never runs over an empty current-canary universe, so suite estimates can never become vacuous fabricated zeroes. For a non-empty suite it calls `scan_results(project_dir(root) / "results")`, then `analyze_history(summary, current_canary_ids)` passing configured canary IDs **in config order** (the order `load_project` returns them, not sorted or otherwise re-derived).

`commands.py` does **not** wrap `load_project` failures into `CommandError`. The `history_command` CLI handler MUST catch exactly `(ConfigError, CanaryLoadError, CommandError, ValueError)` for its configuration/input failures and raise `typer.Exit(3)`. This is the Batch #41 history contract and follows the same exit-3 family used by existing commands; notably, `baseline_command` catches this exact union, while `check_command` uses the related `(ConfigError, CanaryLoadError, CommandError, FileNotFoundError)` union because check has additional baseline-file paths. History has no such baseline-file path. This preserves exit `3` for malformed config, malformed/missing canary files, an empty configured suite (`CommandError("no canaries found")`), and analysis/input validation failures. Individual malformed historical reports remain data-quality observations rather than command failures: `scan_results` converts them to `ReportLoadFailure` entries and never aborts the command.

The Batch #41 CLI `history` command is **argument-free**: no flags, no JSON output mode, no filtering options. It calls `commands.execute_history(root)` and prints `render_history_text(result)` to stdout. For a valid project with at least one configured canary, zero qualification history (`HistorySummary` with zero loaded reports) exits `0` (Invariant D1). A configured suite with zero canaries is instead the exit-3 `CommandError("no canaries found")` path above.

## 6. Artifact Parsing Contract

### 6.1 Report-Level Outcomes

Loading a single `report.json` file produces exactly one of two outcomes, recorded in `HistorySummary`:

- **Loaded** (`LoadedReport`): the file was readable, was valid JSON, and had a structurally valid top-level shape (§6.2). It carries zero or more `HistoricalExecution` entries, each holding zero or more normalized `HistoricalAttempt` entries (§6.3); individual executions or individual attempts within it may still be excluded from specific metrics per §6.4–§6.7, but the report as a whole counts toward the "successfully loaded" count.
- **Ignored** (`ReportLoadFailure`): the file was unreadable (I/O error, permission error), not valid JSON, or failed structural validation of its top-level identity (§6.2). It carries the qualification directory name and a short, non-sensitive reason string drawn from a **fixed category set** — never raw exception text or file content — for surfacing in output (§10.6). The fixed reasons are exactly: `"unreadable file"`, `"invalid JSON"`, `"not a JSON object"`, `"missing or invalid qualification_id"`, `"missing or invalid executions list"`, `"malformed execution entry"`, `"duplicate qualification_id"`. No content from an ignored report contributes to any computation.

**Invariant P1 (whole-report vs. partial-field tolerance):** Malformed **whole-report structural identity** (§6.2) invalidates the **whole report** (Ignored). Once that identity is established, nothing about the content of an individual `attempts[]` element — whether it is missing, is not itself a JSON object, or has missing/mistyped fields — ever invalidates the whole report or, by itself, the whole execution it belongs to. A malformed attempt element normalizes to a `HistoricalAttempt` with unusable (`None`/`False`) fields (§6.3); it is then excluded from whichever specific metric(s) its unusable fields would have supported, per §6.4–§6.7, while its execution and report survive for every other metric that remains computable. This is the field-tolerant parsing contract required for pre-#40 artifact salvage (§8) and for the independent-metric contract this batch requires (§6.4's design note).

### 6.2 Structural Validation (Whole-Report Level)

A `report.json` payload is structurally valid only if all of the following hold; any failure makes the report Ignored:

1. The file parses as JSON (`json.loads` succeeds).
2. The parsed value is a JSON object (dict).
3. `qualification_id` is present and is a non-empty string.
4. `executions` is present and is a list.
5. Every element of `executions` is a JSON object containing a non-empty string `canary_id` and a list `attempts`.

Individual elements of an execution's `attempts` list are **not** validated at this stage. An `attempts[]` element that is not a JSON object, or is a JSON object with missing/mistyped fields, does not fail whole-report structural validation — it is tolerated here and normalized into a `HistoricalAttempt` per §6.3.

An execution whose `canary_id` is present but whose own `attempts` list is empty is structurally valid (an empty `HistoricalExecution`) but contributes to no metric (§6.4 requires a non-empty paired schedule).

**Invariant P0 (duplicate qualification identity):** After a report passes structural validation, its non-empty `qualification_id` is checked against IDs already accepted in the deterministic directory-name scan order (Invariant L2). The **first successfully loaded** report for a given `qualification_id` wins. Any later structurally valid report with the same `qualification_id` is Ignored with fixed reason `"duplicate qualification_id"` and contributes no execution or sample. A malformed/ignored earlier file does not reserve an ID because it was never successfully loaded. This protects historical counts and medians from copied/restored duplicate result directories without requiring any artifact rewrite or global index.

### 6.3 Attempt Normalization (Loader Level)

Every element of an execution's `attempts` list — regardless of shape — is converted by the loader into exactly one `HistoricalAttempt` (§5.2). If the element itself is not a JSON object, every field on the resulting `HistoricalAttempt` is `None`/`False` (an **unusable attempt**); no report, execution, or other attempt is affected by this. If the element is a JSON object, each field is normalized independently:

- `side`: the raw value if it is a JSON string, else `None`. (Whether it equals `"baseline"`/`"candidate"` is checked later, as a pairing-identity concern, §6.4 — normalization itself does not reject other strings.)
- `repetition`, `duration_ms`, `input_tokens`, `output_tokens`: the raw value if it is a JSON integer and **not** a JSON boolean, else `None`. A JSON `true`/`false` is explicitly rejected here even though it is technically `int`-compatible in some JSON decoders' Python representation — `bool` must never pass as `int` anywhere in this contract.
- `success`, `valid`: the raw value if it is a JSON boolean, else `None`.
- `usage_observed`: `True` if and only if the attempt has a `usage` key whose value is a JSON object with an `observed` key whose value is exactly `true`; otherwise `False`.

This normalization is the **only** place raw JSON is inspected for attempt-level content; every downstream section (§6.4–§6.7, §7, §9) reasons only about the normalized `HistoricalAttempt` fields, never about the raw JSON again.

### 6.4 Pairing Identity Prerequisite (Shared by All Metrics)

**Design note:** effectiveness, runtime, and token metrics each need to know "was there a complete, unambiguous baseline/candidate schedule here?" before anything metric-specific can be computed. That question is answered exactly once, here, and is the *only* prerequisite shared across all three metrics — everything past this point is metric-specific and evaluated independently (§6.5–§6.7), so a defect in one metric's own fields never leaks into another metric's eligibility.

An execution has **usable pairing identity** only if, using its normalized attempts (§6.3):

1. **Every** attempt in the execution has `side` exactly `"baseline"` or `"candidate"` (not `None`, not any other string), and `repetition` a non-`None` integer `>= 1`. If even one attempt fails this, the execution's pairing identity is unusable.
2. Given (1) holds for every attempt, the set of repetitions with a `"baseline"` attempt is non-empty, the set of repetitions with a `"candidate"` attempt is non-empty, and the two sets are **identical**.
3. There is **no duplicate** `(side, repetition)` pair — each pair appears at most once across the execution's attempts.

(Note: pairing identity is inferred structurally from the attempts actually present; it does not read or depend on the configured `repetitions` count from `config.yaml`, because historical artifacts may have been produced under a different repetitions setting than the current config.)

If pairing identity is unusable or incomplete, the execution contributes **no metric at all** — not effectiveness (§7), not a runtime sample (§9.2), not a token sample (§9.3).

**Invariant E2 (budget-skipped executions are not samples):** An execution produced by the executor's `max_attempts`/`max_tokens` budget-skip path (Batch #32/#40) has `attempts == ()` (see `_skipped_canary` in `run/executor.py`). An empty attempt list fails the non-empty-repetition-set requirement in (2) above and is therefore never eligible for any metric — skipped canaries contribute zero samples of any kind, with no special-case code required beyond the general non-empty-set rule.

### 6.5 Effectiveness-Only Requirements

Given usable pairing identity (§6.4), an execution is eligible for effectiveness only if, additionally:

- Every attempt's `success` and `valid` are non-`None` (i.e., the raw JSON value was an actual boolean per §6.3).
- Every attempt has `valid == True`.
- Every attempt with `side == "baseline"` has `success == True` (the "stable baseline" requirement).

`duration_ms`, `input_tokens`, `output_tokens`, and `usage_observed` are **never** read for effectiveness eligibility or for detection/non-detection classification (§7.3). See §7 for the full effectiveness contract, built on this section plus §6.4.

### 6.6 Runtime-Only Requirements

Given usable pairing identity (§6.4), an execution is a **runtime sample** only if, additionally, every attempt's `duration_ms` is a non-`None` integer `>= 0`. `success`, `valid`, `input_tokens`, `output_tokens`, and `usage_observed` are **never** read for runtime eligibility — an attempt with `success == False` or `valid == False` still consumed model-attempt time, and its `duration_ms` is summed exactly like any other attempt's. See §9.2.

### 6.7 Token-Only Requirements

Given usable pairing identity (§6.4), an execution is a **token sample** only if, additionally, every attempt has trustworthy usage: `usage_observed == True` and both `input_tokens` and `output_tokens` are non-`None` integers `>= 0`. `duration_ms`, `success`, and `valid` are **never** read for token eligibility. See §9.3.

**Invariant P2 (no cache/reasoning double count in history):** `history/analysis.py` MUST NOT read or sum `cached_input_tokens`, `cache_write_input_tokens`, or `reasoning_output_tokens` under any circumstance — the loader does not even normalize those keys onto `HistoricalAttempt` (§5.2 has no fields for them). Only `input_tokens + output_tokens` per trustworthy attempt is ever summed for a token sample.

**Invariant P3 (metric independence):** No metric's eligibility depends on a field outside its own gate (§6.4's shared pairing prerequisite, plus its own §6.5/§6.6/§6.7 requirements). Concretely: `duration_ms` MUST NOT affect effectiveness or token eligibility; `success`/`valid` outcomes MUST NOT gate runtime or token eligibility; `usage`/`input_tokens`/`output_tokens` MUST NOT affect effectiveness or runtime eligibility. Consequences that follow directly from this invariant: a malformed `duration_ms` can cost an execution its runtime sample while its effectiveness classification and token sample (if usage is trustworthy) are unaffected; a malformed or failed `success`/`valid` outcome can cost an execution its effectiveness eligibility while its runtime and token samples (if their own fields are trustworthy) are unaffected. There is no scenario in this contract where `duration_ms` is required for effectiveness or for a token sample, and no scenario where a `success`/`valid` outcome gates a runtime or token sample.

### 6.8 Error Semantics Summary

| Condition | Effect |
|---|---|
| File unreadable / not valid JSON / not a JSON object | Whole report Ignored (§6.2), reason `"unreadable file"` / `"invalid JSON"` / `"not a JSON object"` |
| `qualification_id` missing/mistyped | Whole report Ignored (§6.2), reason `"missing or invalid qualification_id"` |
| `executions` missing/not-a-list | Whole report Ignored (§6.2), reason `"missing or invalid executions list"` |
| One `executions[]` entry missing/mistyped `canary_id` or `attempts` | Whole report Ignored (§6.2), reason `"malformed execution entry"` — a top-level structural defect, not a per-execution one |
| One `attempts[]` element is not a JSON object, or has missing/mistyped fields | That element normalizes to an unusable/partial `HistoricalAttempt` (§6.3); it does not invalidate the report by itself — the execution's fate per metric is then decided by §6.4–§6.7 |
| An execution's attempts fail pairing identity (§6.4): bad `side`/`repetition`, empty/mismatched/duplicated schedule | Execution contributes to **no** metric: not effectiveness, not runtime, not tokens |
| Pairing identity usable, but one attempt has non-boolean `success`/`valid`, a `valid == False`, or a baseline `success == False` | Execution excluded from **effectiveness only** (§6.5); runtime and token samples are unaffected if their own field requirements independently hold |
| Pairing identity usable, but one attempt's `duration_ms` is missing/mistyped/negative | Execution excluded from **runtime only** (§6.6); effectiveness and token samples are unaffected if their own field requirements independently hold |
| Pairing identity usable, but one attempt lacks trustworthy usage (`usage` missing/non-object, `observed` absent/false, or `input_tokens`/`output_tokens` missing/mistyped/negative) | Execution excluded from **tokens only** (§6.7); effectiveness and runtime samples are unaffected if their own field requirements independently hold |
| `results_dir` does not exist, or exists but contains zero qualification directories with `report.json` | Not an error; `scan_results` returns zero loaded and zero ignored without creating anything (Invariant L0, §10.1) |
| Later structurally valid report repeats a `qualification_id` already successfully loaded earlier in deterministic scan order | Later report Ignored with reason `"duplicate qualification_id"`; first successfully loaded report remains authoritative (Invariant P0) |

## 7. Effectiveness Contract

### 7.1 Actionable Universe

Effectiveness ranking and current-suite estimates are computed **only** for canary IDs present in the **currently configured** canary suite, as loaded by `load_project(root)` (identical config/canary loading path used by `check`/`baseline`). A historical canary ID that does not appear in the current suite (an **obsolete** canary ID) is never ranked and never contributes to current-suite estimates (§9.4), even if it has abundant, perfectly eligible historical data. This is enforced by filtering on the canary ID set **after** the loader has aggregated all historical data, so obsolete-ID data is computed and then discarded, not skipped during scanning — keeping the loader itself agnostic to "current" configuration and reusable by future batches that may want full historical detail.

### 7.2 Eligible Historical Execution (Effectiveness Denominator)

A `HistoricalExecution` (one canary's `executions[]` entry within one loaded report) is **eligible for effectiveness** only if it has **usable pairing identity** (§6.4 — complete, non-duplicated baseline/candidate schedule inferred structurally from attempts alone) **and** additionally satisfies the effectiveness-only requirements (§6.5): every attempt's `success`/`valid` are actual booleans, every attempt has `valid == True`, and every baseline attempt has `success == True` (the "stable baseline" requirement). An execution with any baseline failure, any non-boolean outcome field, or any `valid == False` attempt is excluded from effectiveness entirely; it is never counted as a detection or a non-detection. Per Invariant P3 (§6.7), this exclusion never depends on `duration_ms` or `usage`.

**Invariant E1 (structural pairing, no policy dependency):** Eligibility determination reads only `side`, `repetition`, `success`, and `valid` from each attempt. It never reads `CanaryExecution.verdict`, `CanaryExecution.reason`, `QualificationResult.verdict`, or `QualificationResult.reasons`. This guarantees effectiveness accounting is independent of `qualification/policy.py` and stays correct even if policy wording changes in a future batch.

Invariant E2 (budget-skipped executions are not samples) is stated once, in §6.4, and applies identically here: an empty attempt list fails the pairing-identity prerequisite shared by all metrics, so a skipped canary contributes zero effectiveness samples with no special-case code.

### 7.3 Detection vs. Non-Detection

For an eligible execution (§7.2):

- If **every** candidate attempt succeeded (`success == True` for all `side == "candidate"` attempts), the execution is a **non-detection** (the candidate behaved indistinguishably from a stable baseline for this check).
- If **any** candidate attempt failed (`success == False` for at least one `side == "candidate"` attempt), the execution is a **detection** (the canary caught a candidate regression).

There is no third outcome for an eligible execution — eligibility (§7.2) already guarantees a complete, valid, baseline-stable comparison exists, so every eligible execution is classified as exactly one of detection or non-detection.

### 7.4 Per-Canary Effectiveness Aggregation

For each currently configured canary ID:

- `eligible_samples` = count of eligible historical executions (across all successfully loaded reports) for that canary ID.
- `detections` = count of those that are detections (§7.3).
- `detection_rate` = `detections / eligible_samples` if `eligible_samples > 0`, else undefined (not ranked; see §7.5).

### 7.5 Ranking and Minimum-History Threshold

**Invariant E3 (minimum history):** A currently configured canary is **ranked** only if `eligible_samples >= 3`. A canary with `0 <= eligible_samples < 3` is reported separately as **not-enough-history** (§10.4), never assigned a rank, and never silently omitted — every currently configured canary appears in exactly one of "ranked" or "not-enough-history" in the rendered output (§10).

**Invariant E4 (deterministic tie-break):** Ranked canaries are ordered by, in strict priority order:

1. `detection_rate` descending
2. `detections` descending
3. `eligible_samples` descending
4. `canary_id` ascending

This exact four-key order is the only sort used; it is fully deterministic given identical input data, with `canary_id` ascending as the final tie-break guaranteeing a total order (no two distinct canary IDs can tie on all four keys).

**Invariant E5 (list ordering):** `ranked` is ordered exclusively per Invariant E4. `not_enough_history` (§10.5), `missing_runtime_canaries`, and `missing_token_canaries` (§9.4, §10.2, §10.3) all preserve the **current configured canary order** — the order `load_project` returns and `commands.execute_history` passes into `analyze_history` (§5.3) — never alphabetical order, discovery order, or any other re-derived ordering. This keeps every operator-facing "what's missing" or "not enough history" list aligned with the operator's own `config.yaml` ordering.

## 8. Backward Compatibility (Pre-Batch-#40 Artifacts)

Pre-#40 `report.json` artifacts lack `usage.observed` and `usage.cache_write_input_tokens` (the keys are simply absent — Batch #40 added them to the `Usage` dataclass, and `asdict` only ever emitted keys that existed on the dataclass at write time). Per §6.7, an attempt with a missing `usage` key, or a `usage` object missing `observed`, has **no trustworthy usage**, but remains fully usable for:

- Effectiveness (§7): eligibility and detection/non-detection depend only on `side`/`repetition`/`success`/`valid`, never on `usage`.
- Runtime sampling (§9.2): depends only on `duration_ms`, never on `usage`.

It is simply excluded from token sampling (§9.3) for that specific attempt (which, per §9.3's all-attempts-in-execution requirement, excludes the whole execution's token sample — but never its runtime or effectiveness contribution, per Invariant P3).

**Invariant C1 (no schema migration):** No artifact is rewritten, versioned, or migrated by this batch. `report.json` files remain exactly as originally written. A future reader (this batch, or a later one) is solely responsible for tolerating the shape it finds; the shape itself is never changed retroactively. This mirrors Invariant A1 from the Batch #40 spec, applied here to the reader side instead of the writer side.

## 9. Cost-Estimation Sample Contract (Intentionally Different from §7)

Cost estimation (runtime and tokens) uses a **broader** eligibility rule than effectiveness, by design: a canary execution that consumed real model-attempt resources should count toward a runtime/token estimate even if the run failed validity or success checks, because the resource was spent regardless of outcome. This is the key asymmetry versus §7, and it is intentional, not an oversight. Runtime and token sampling are also **independent of each other**, not just of effectiveness (Invariant P3, §6.7) — after the shared pairing prerequisite, runtime depends only on duration fields while token sampling depends only on trustworthy usage fields, so an execution may contribute either metric without the other.

### 9.1 Pairing Prerequisite (Shared)

Both runtime and token sampling require **usable pairing identity** exactly as defined in §6.4 — the same non-empty, identical, no-duplicate baseline/candidate repetition-set test effectiveness uses (§7.2's first requirement). Unlike effectiveness, cost sampling has **no** requirement on `success`/`valid` outcomes and **no** baseline-stability requirement: started attempts consumed resources regardless of grading outcome. A budget-skipped execution (`attempts == ()`, Invariant E2, §6.4) fails the non-empty-set test and is therefore never a runtime or token sample, for the same reason it is never an effectiveness sample.

### 9.2 Runtime Sample

Given usable pairing identity (§9.1), an execution is a **runtime sample** if, additionally, every attempt's `duration_ms` is a non-`None` integer `>= 0` (§6.6). The runtime sample value is `sum(duration_ms for attempt in attempts)` across **all** of its attempts (baseline and candidate, every repetition). Per Invariant P3, this determination never reads `success`, `valid`, or `usage` — an execution with a failed or invalid attempt is still a runtime sample as long as its durations are trustworthy and its pairing identity holds.

**Invariant R1 (attempt-runtime framing, not wall-clock):** This estimates **model-attempt runtime only** — the sum of durations the executor recorded for individual baseline/candidate model attempts. It is explicitly **not** an end-to-end or wall-clock qualification runtime. Rendering (§10) MUST label this "estimated model-attempt runtime" (or equivalent unambiguous phrasing) and MUST include a plain note that setup, preparation, and CLI overhead are excluded. Rendering MUST NOT use "full check runtime," "total runtime," "wall-clock," or similar language that implies an end-to-end measurement.

### 9.3 Token Sample

Given usable pairing identity (§9.1), an execution is a **token sample** if, additionally, every attempt has trustworthy usage per §6.7 (`usage_observed == True`, non-`None` non-negative integer `input_tokens`/`output_tokens`). If even one attempt in an otherwise-pairing-valid execution lacks trustworthy usage, that execution contributes **no token sample** (it may still contribute a runtime sample, per §9.2, since runtime and token trustworthiness are evaluated independently). When every attempt has trustworthy usage, the token sample value is `sum(input_tokens + output_tokens for attempt in attempts)` across all attempts — never adding cache-read, cache-write, or reasoning subsets (Invariant P2). Per Invariant P3, this determination never reads `duration_ms`, `success`, or `valid`.

### 9.4 Per-Canary and Suite Estimation

For each currently configured canary ID (§7.1):

- Collect all **eligible runtime samples** (§9.2) and all **eligible token samples** (§9.3) across every successfully loaded report, for executions of that canary ID.
- **Per-canary runtime estimate** = `statistics.median(runtime_samples)` if `runtime_samples` is non-empty, else unavailable for that canary.
- **Per-canary token estimate** = `statistics.median(token_samples)` if `token_samples` is non-empty, else unavailable for that canary.

**Invariant M1 (median semantics):** `statistics.median` is used exactly as-is, including its documented behavior of returning the arithmetic mean of the two middle values for an even-sized sample (which may therefore be a `float`, e.g. `1500.5`). Internal estimate values MAY be `float`. The renderer (§10) rounds to whole milliseconds or whole tokens **only for display**; rounding MUST NOT be applied before or during the `analysis.py` computation, and MUST NOT feed back into any stored/raw sample accounting.

**Invariant M2 (current full-suite estimate is all-or-nothing, independently per metric):**

- **Current full-suite runtime estimate** = the sum of every currently configured canary's per-canary runtime median, **if and only if every currently configured canary has at least one runtime sample**. If any currently configured canary has zero runtime samples, the full-suite runtime estimate is **unavailable**, and rendering lists exactly which currently configured canary ID(s) are missing a runtime sample.
- **Current full-suite token estimate** is computed identically and independently for tokens: sum of per-canary token medians iff every currently configured canary has at least one token sample; otherwise unavailable, with the specific missing canary IDs listed.
- These two all-or-nothing determinations are **independent of each other** — it is valid and expected for the runtime suite estimate to be available while the token suite estimate is unavailable (for example, because history predates Batch #40), or vice versa when duration fields are unusable but trustworthy normalized usage remains available. Neither metric is a prerequisite for the other beyond the shared pairing-identity requirement (§9.1–§9.3).

**Rationale:** this all-or-nothing design deliberately lets partial or budgeted historical runs (Batch #32/#40 `max_attempts`/`max_tokens`, which produce runs where only some canaries actually executed) contribute valid **per-canary** samples without ever letting a partial run masquerade as an estimate of the **whole current suite**. A suite estimate is only ever claimed when every currently configured canary has contributed real data.

## 10. Low-Tech Output Contract

### 10.1 History Summary (Always Present)

Every invocation reports, regardless of outcome:

- The count of successfully loaded qualification reports (`LoadedReport` count, §6.1).
- The count of ignored reports (`ReportLoadFailure` count, §6.1).

**Invariant D1 (zero history is normal for a valid non-empty current suite):** After project/config loading has succeeded and at least one current canary is configured (§5.3), zero qualification directories under `results/` (or zero successfully loaded reports) is a normal, **exit-0** result. It is rendered as plain low-tech guidance (e.g., "No qualification history found yet. Run `qualock check` to start building history."), never as an error, never as a non-zero exit code on its own. The empty-current-suite case is not zero history; it is the exit-3 `CommandError("no canaries found")` path from §5.3.

### 10.2 Estimated Model-Attempt Runtime

A labeled section titled exactly **"Estimated model-attempt runtime"** (never "full check runtime," "total runtime," or similar; see Invariant R1), containing:

- The current full-suite estimate (§9.4), if available, rendered with the **Invariant D3 runtime display rule** below, always accompanied by the plain-language note that setup, preparation, and CLI overhead are excluded — this note MUST always be present in this section, never conditionally omitted.
- If unavailable, an explicit statement that it is unavailable and the specific list of currently configured canary IDs (in config order, Invariant E5) with zero runtime samples — the same required exclusion note still applies once an estimate does become available.
- Per-canary runtime medians, where shown, use the identical display rule.

**Invariant D3 (runtime display rounding):** Internal `runtime_median_ms` / suite `runtime_ms` values stay exactly as returned by `statistics.median`/summation (§ Invariant M1) — `float` or `int`, unrounded — through all of `analysis.py`. Only `render.py` rounds, and only for display: `total_seconds = round(milliseconds / 1000)` using Python's built-in `round` (banker's rounding on an exact `.5` tie). If `total_seconds < 60`, render as `f"{total_seconds}s"`. Otherwise render as `f"{total_seconds // 60}m {total_seconds % 60}s"`. This is the only runtime rounding rule used anywhere in `render.py`; no millisecond value is ever shown directly.

### 10.3 Estimated Model Tokens

A labeled section titled exactly **"Estimated model tokens"** (never "cost," "price," "billing," or similar; see §10.7), containing:

- The current full-suite token estimate (§9.4), if available, rendered with the **Invariant D4 token display rule** below.
- If unavailable, an explicit statement that it is unavailable and the specific list of currently configured canary IDs (in config order, Invariant E5) with zero token samples.
- Per-canary token medians, where shown, use the identical display rule.

**Invariant D4 (token display rounding):** Internal `token_median` / suite `tokens` values stay exactly as returned by `statistics.median`/summation, unrounded, through all of `analysis.py`. Only `render.py` rounds, and only for display: `round(value)` using Python's built-in `round`, then formatted with thousands separators (e.g. Python's `f"{round(value):,}"`, e.g. `12,345`). This is the only token rounding rule used anywhere in `render.py`.

### 10.4 Ranked Current Canaries

A labeled section listing every currently configured canary with `eligible_samples >= 3` (Invariant E3), in the deterministic order from Invariant E4, each row showing at minimum: `canary_id`, `detection_rate` (as a plain percentage), `detections`, `eligible_samples`.

**Invariant D5 (detection-rate display):** Internal `detection_rate` stays the exact unrounded float from `detections / eligible_samples`. Only `render.py` formats it: `percent = round(detection_rate * 100)` using Python's built-in `round`, then renders exactly `f"{percent}%"`. No decimal percentage form is used in #41.

### 10.5 Not-Enough-History Current Canaries

A labeled section listing every currently configured canary with `0 <= eligible_samples < 3`, each row showing `canary_id` and its actual `eligible_samples` count (which may be `0`), with an explicit "not enough history" framing — never silently omitted, and never assigned a fabricated rank.

### 10.6 Missing-Estimate History and Ignored Malformed Reports

- If either the runtime or token full-suite estimate is unavailable (§9.4, Invariant M2), a section lists exactly which currently configured canary IDs are missing which sample type.
- A section lists the count of ignored reports (§10.1) and, when non-zero, MAY list the ignored qualification directory names with their short reason strings for operator troubleshooting. Reason strings are always drawn from the fixed, non-sensitive category set defined in §6.1 (`"unreadable file"`, `"invalid JSON"`, `"not a JSON object"`, `"missing or invalid qualification_id"`, `"missing or invalid executions list"`, `"malformed execution entry"`, `"duplicate qualification_id"`) — never raw exception messages or raw file content, which could leak local paths or artifact contents into shared operator output. This listing is diagnostic detail, not required for the zero-history-is-normal contract in §10.1/Invariant D1.

### 10.7 Wording Constraints

**Invariant D2 (no pricing/cost/cap/billing wording):** Output produced by `history/render.py` MUST NOT use the words "pricing," "cost," "price," "cap," "billing," or "budget" (the last being reserved for the unrelated `max_attempts`/`max_tokens` executor feature from Batch #32/#40, to avoid operator confusion between an advisory historical estimate and an active admission-control threshold). Approved labels are exactly **"Estimated model-attempt runtime"** and **"Estimated model tokens"**, optionally qualified further (e.g., "attempt-only") but never renamed to imply an end-to-end or monetary measurement.

## 11. Test Plan

All items below must be pinned as explicit, named test cases (not merely covered incidentally):

1. **Zero history:** empty `results/` directory (or no `results/` directory at all) yields `HistorySummary` with zero loaded, zero ignored; rendered output matches Invariant D1's normal, exit-0, non-error guidance.
2. **Malformed/unreadable whole report:** a `report.json` that is not valid JSON, one that is valid JSON but not an object, one missing `qualification_id`, and one missing/mistyped `executions`, are each Ignored (§6.2) with the correct fixed-category reason string (§6.1); none raises out of the loader.
3. **Mixed valid+malformed history:** one directory with a well-formed `report.json` alongside one or more malformed ones in the same `results/` tree; loaded/ignored counts are both correctly non-zero, and the well-formed report's data is fully reflected in analysis output.
4. **Malformed `attempts[]` element does not invalidate the report:** a report where one `attempts[]` element is not a JSON object (e.g. a bare string) sits alongside otherwise well-formed executions/attempts in the same report; the report is Loaded (not Ignored), the malformed element normalizes to an unusable `HistoricalAttempt` that breaks its own execution's pairing identity (§6.4), and sibling executions in the same report are fully unaffected (§6.3, Invariant P1).
5. **Pre-#40 artifact salvage:** a fixture `report.json` with attempts that omit the `usage` key entirely (simulating a pre-#40 artifact) is loaded successfully (not Ignored), contributes to effectiveness and runtime sampling normally, and is correctly excluded from token sampling only (§6.7, §8).
6. **Skipped execution excluded:** an execution with `attempts: []` (matching the executor's `_skipped_canary` shape) is excluded from effectiveness, runtime, and token sampling alike via the shared pairing-identity non-empty-schedule rule (§6.4), with no special-case branch required in the test's assertions beyond confirming zero contribution to all three.
7. **Duplicate/mismatched repetition schedule excluded from all three metrics:** (a) an execution with two `"baseline"` attempts both at `repetition=1` and no `repetition=2` baseline (duplicate slot) is excluded from effectiveness, runtime, and token sampling; (b) an execution where the baseline repetition set is `{1,2,3}` but the candidate repetition set is `{1,2}` (mismatched sets) is likewise excluded from all three (§6.4).
8. **Malformed `success`/`valid` loses effectiveness only:** an execution with usable pairing identity (§6.4), every `duration_ms` valid, and every attempt's usage trustworthy, but one attempt's `success` is a non-boolean (e.g. a string) — excluded from effectiveness only (§6.5); its runtime sample and its token sample are both still collected (§6.6, §6.7, Invariant P3).
9. **Malformed `duration_ms` loses runtime only:** an execution with usable pairing identity, every `success`/`valid` an actual boolean with the baseline stable, and every attempt's usage trustworthy, but one attempt's `duration_ms` is negative (or non-integer) — excluded from the runtime sample only (§6.6); it is still an eligible effectiveness execution (§6.5) and still contributes a token sample (§6.7), proving `duration_ms` never gates either (Invariant P3).
10. **Malformed usage loses token only:** an execution with usable pairing identity, valid outcomes with a stable baseline, and valid durations throughout, but one attempt has `usage.observed=false` (a separate case: one attempt's `usage.input_tokens` missing) — excluded from the token sample only (§6.7); effectiveness and runtime are both unaffected (Invariant P3).
11. **`valid=False` outcome excludes effectiveness but keeps runtime/token:** an execution with usable pairing identity where one attempt has a genuine `valid=False` outcome (not a corrupted field — `success`/`valid` are still actual booleans) but every `duration_ms` and every `usage` is trustworthy — excluded from effectiveness (§6.5 requires `valid == True` for every attempt) while its runtime sample and token sample are both still collected.
12. **Candidate `success=False` with `valid=True` is a detection and still a cost sample:** an execution with usable pairing identity, a stable baseline, every attempt `valid=True`, and at least one candidate attempt with `success=False` — classified as a detection (§7.3), and separately contributes both a runtime sample and (given trustworthy usage) a token sample, proving a failed-but-valid candidate attempt is excluded from neither effectiveness nor cost sampling.
13. **Unstable baseline excluded:** an execution with usable pairing identity and every attempt's `success`/`valid` an actual boolean and `valid == True`, but at least one `side="baseline"` attempt with `success=False`, is excluded from effectiveness (§6.5's stable-baseline requirement) while still contributing runtime/token samples (§9.1 has no baseline-stability requirement).
14. **PASS eligible non-detection:** an eligible execution where every candidate attempt succeeded is classified as a non-detection (§7.3).
15. **WARN/BLOCK candidate regression detection:** an eligible execution with at least one failed candidate attempt is classified as a detection (§7.3), regardless of what `CanaryExecution.verdict`/`reason` say (Invariant E1) — assert the classification is unchanged even when the fixture's `verdict`/`reason` fields are deliberately set to contradict the attempt-level facts, to prove independence from policy/reason strings.
16. **>=3 ranking threshold:** canaries with `eligible_samples` of `0`, `1`, `2`, and `3` are respectively not-enough-history, not-enough-history, not-enough-history, and ranked (Invariant E3); a boundary test at exactly `3`.
17. **Deterministic tie breaks:** construct two or more canaries with identical `detection_rate` and `detections` but differing `eligible_samples`, and separately two canaries identical on all three numeric keys differing only by `canary_id`; assert the exact order from Invariant E4 in both cases.
18. **Median even/odd:** an odd-count runtime/token sample set and an even-count one (asserting the even case yields the arithmetic-mean-of-two-middle-values `statistics.median` semantics, including a case producing a non-integer float internally per Invariant M1) both produce correct per-canary estimates; a separate rendering test confirms the even-case float is rounded only at display time, per the exact rules in Invariant D3/D4.
19. **No cache/reasoning double count:** a fixture with large nonzero `cached_input_tokens`/`cache_write_input_tokens`/`reasoning_output_tokens` values asserts the token sample equals exactly `input_tokens + output_tokens`, unaffected by the subset fields' magnitude (Invariant P2).
20. **Current-config-only ranking and obsolete-ID exclusion (merged):** a single historical report containing (a) an execution for a canary ID present in the current configured suite fixture, and (b) an execution for a canary ID absent from it, with the absent one given abundant, fully-eligible historical data — asserts the present canary is correctly ranked/not-enough-history while the absent (obsolete) canary contributes **zero** rows anywhere: not to ranking, not to not-enough-history, not to any missing-estimate listing. It is simply invisible to `analyze_history`'s current-suite view (§7.1). (This test subsumes what would otherwise be two overlapping tests for the same invariant.)
21. **Suite estimate all-or-nothing independently for runtime/tokens:** a fixture where every currently configured canary has runtime samples but one lacks any token sample asserts the runtime suite estimate is available while the token suite estimate is unavailable and correctly lists only the token-missing canary ID(s) (Invariant M2); a mirror-image fixture for the reverse case.
22. **Partial historical runs contribute per-canary medians:** a fixture simulating a `max_attempts`/`max_tokens`-budgeted historical run (some canaries with `attempts=()` skips, others fully executed) contributes valid per-canary samples for the executed canaries while contributing nothing for the skipped ones, without crashing or fabricating a suite estimate from partial data.
23. **Deterministic scan and list ordering:** a fixture with qualification directories whose names sort differently than their creation order asserts `scan_results` processes them in directory-name sort order (Invariant L2); a separate assertion on a current-canary-ID fixture given in a specific, non-alphabetical config order confirms `not_enough_history`, `missing_runtime_canaries`, and `missing_token_canaries` all preserve that exact config order while `ranked` follows only Invariant E4's sort (Invariant E5).
24. **Windows/path-neutral scanning:** a test using only `pathlib`-relative fixture construction (no POSIX-specific path string assumptions) confirms `scan_results` behaves identically regardless of path separator conventions; this may be a characterization test run under the project's existing Windows CI lane rather than requiring a second local implementation.
25. **Zero-write proof:** a test that snapshots the mtimes and byte-for-byte contents of a fixture `results/` tree, invokes `scan_results` (and, separately, the full low-tech command end-to-end), and asserts every file's mtime and contents are byte-identical afterward (Invariant L1).
26. **`qualification/policy.py` and `run/executor.py` unchanged:** a diff-scope test/check (or explicit PR review step, §12) confirming no lines in `qualification/policy.py` or `run/executor.py` changed as part of this batch's implementation.
27. **Duplicate `qualification_id` deduplicates deterministically:** two structurally valid qualification directories with the same top-level `qualification_id`, arranged so directory-name order is explicit, yield exactly one Loaded report (the first in scan order) and one Ignored report with reason `"duplicate qualification_id"`; samples/counts are not doubled (Invariant P0).
28. **Empty current canary suite is exit 3:** a valid project/config whose canary globs resolve to zero canaries makes `execute_history` raise exactly `CommandError("no canaries found")`; CLI maps it to exit `3` and never renders `0s`/`0` suite estimates.
29. **Detection-rate display rounding:** ranked fixtures around rounding boundaries assert `render_history_text` uses exactly `round(detection_rate * 100)` and whole-percent `N%` output (Invariant D5), while the internal float remains unrounded.
30. **History CLI preserves configuration-error exit semantics:** representative `ConfigError`, `CanaryLoadError`, `CommandError`, and `ValueError` paths are mapped by `history_command` to exit `3`, matching existing `check`/`baseline` handlers rather than relying on commands-layer exception wrapping.

## 12. Review Gates (Required Before Roadmap Update)

All of the following must pass before this feature may be marked delivered in `ROADMAP.md`:

- Full test suite (all items in §11) green.
- Ruff: changed Batch #41 Python files (`src/qualock/history/*.py`, the bounded `commands.py`/`cli.py` additions) are Ruff-clean, **and** a full-tree Ruff diagnostics comparison against this document's exact base SHA (`f6410c610642b4e271a9932172326006f37cfaa6`) introduces **no new findings** anywhere in the tree beyond that exact base's existing diagnostics. This gate is stated as a diff against the exact base, not as an assumption that the repo-wide baseline is already clean.
- Strict mypy introduces no new errors beyond the same three known PyYAML-stub `import-untyped` errors already present at this document's exact base SHA (`src/qualock/config/io.py`, `src/qualock/canary/loader.py`, `src/qualock/project_setup/config.py`), unless the base branch itself changes before implementation lands, in which case the gate is restated against the new exact base. Do not install stubs or dependencies in Batch #41; any additional mypy error is blocking.
- `compileall` clean.
- Exact-head verification: record and review the committed HEAD SHA that all final local gates ran against.
- `git diff --check f6410c610642b4e271a9932172326006f37cfaa6..HEAD` clean on that exact HEAD — no whitespace errors or conflict markers in the Batch #41 diff.
- Protected-scope proof: an explicit diff-scope check demonstrating no changes landed under §3.1's protected paths (including `qualification/policy.py` and `run/executor.py` specifically, per Test 26) unless justified and called out individually in the PR description.
- Windows CI green, including the path-neutral scanning test (§11 item 24).
- Zero-write proof (§11 item 25) passes in CI, not merely locally.
- No authenticated agent qualification run is required to validate this batch — all behavior is testable via fixture `report.json` files without live credentials or Docker/host execution, consistent with this being a read-only, artifact-analysis batch.
- Independent review sign-off, separate from the implementing author.

Only after **all** of the above are satisfied may `ROADMAP.md` be updated to mark "historical canary effectiveness ranking and runtime/token-usage estimates" as delivered.

## 13. Roadmap Update Content (Post-Verification Only)

When the gates in §12 are satisfied, `ROADMAP.md` should be updated to:

- Move "historical canary effectiveness ranking and runtime token-usage estimates (#41)" from the "Next" section to the delivered/completed section.
- Retain, in the "Next" section (not delivered), explicitly:
  - Provider-specific monetary cost estimates (#42), pending, advisory only, outside qualification pass/fail policy — noting it now has a concrete `history/` data-plumbing foundation to build on, without claiming any part of #42 is implemented.

This document does not itself edit `ROADMAP.md`; that edit happens only as part of the implementation PR, after review gates pass.

## 14. Deferred Follow-Ups (Explicit Ordering)

1. **#42 — Provider-specific monetary estimates.** Reuses this batch's historical plumbing, but provider pricing math MUST consume the per-attempt `input_tokens`/`output_tokens` split reachable through `HistorySummary.loaded -> LoadedReport.executions -> HistoricalAttempt`. `CanaryEstimate.token_samples`/`token_median` are intentionally provider-neutral merged `input + output` totals and are **not sufficient** for provider-specific pricing. Pricing remains a separate, clearly labeled, non-authoritative layer outside qualification pass/fail policy.
2. **A JSON output flag / stable external API for `history/`.** Not required or added in #41; internal dataclasses are structured to make adding one straightforward later, but no commitment is made to when.
3. **Any persisted historical index/cache**, should re-scanning `results/` ever become a measured performance problem at scale. Not needed or justified by anything in scope for #41; deliberately not designed for here to avoid speculative infrastructure.

## 15. Design Self-Review

- **Placeholder scan:** no unresolved placeholder markers remain; every field, invariant, threshold, and label is fully specified with concrete values (e.g., minimum-history threshold `3`, the exact four-key tie-break order, the exact two output labels).
- **Contradiction check:** §7 (effectiveness) and §9 (cost estimation) are explicitly documented as having different eligibility rules for the same underlying `HistoricalExecution` concept, with §9's opening paragraph stating the asymmetry is intentional; no other section silently assumes the two eligibility rules are the same.
- **Ambiguity check:** pairing identity is defined once in §6.4 and reused by effectiveness/runtime/token sampling, while every other field gate is metric-specific (Invariant P3). `HistoricalAttempt.valid` is the normalized artifact value corresponding to `AttemptResult.valid`; malformed raw fields normalize to `None` rather than creating a second generic "invalid attempt" concept.
- **Scope-creep check:** every capability introduced (loader, analysis, render, bounded CLI wiring) is scoped strictly to read-only analysis of already-written `report.json` artifacts. No change is proposed to `qualification/policy.py`, `run/executor.py`, canary/config schema, or any other protected surface in §3.1. No network/pricing capability is partially implemented; §14 defers it cleanly.
- **Compatibility check:** the pre-#40 artifact salvage path (§8) is stated as a structural consequence of the general field-tolerance rule in §6.4/Invariant P1, not as a special-cased "if old format" branch, and is explicitly test-covered (§11 item 5). No artifact is ever rewritten (Invariant C1, mirroring Batch #40's Invariant A1).
- **Honesty-of-framing check:** all rendering language (§10, Invariant D1/D2/R1) is stated to avoid implying an end-to-end runtime measurement, a monetary cost, or a hard cap/budget, consistent with this batch's advisory-only, read-only nature.

No unresolved concerns remain for implementation to begin against this spec.
