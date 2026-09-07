# Token-Aware Qualification Budget: Normalized Token Usage + Token-Denominated Local Qualification Budget

- **Batch:** #40
- **Status:** Proposed (design only; no implementation in this batch)
- **Branch:** `feat/token-budget`
- **Base:** `4138d21a58eabe0236c3e6d6d7309ca638148248`
- **Related:** `docs/superpowers/specs/2026-09-04-budgeted-qualification-design.md` (attempt-count budget)
- **Author context:** design decisions below are pre-approved; this document formalizes them into binding spec language, invariants, and review gates. No open questions are left for the reader to resolve.

## 1. Summary

This batch adds two related capabilities to the local `qualock check` qualification path only:

1. **Normalized, provider-neutral token usage accounting** on the canonical qualification `Usage` model, populated by the Codex, Claude, and Antigravity evidence parsers/backends.
2. **An optional token-denominated budget** (`max_tokens`) for local qualification, enforced by the executor at canary boundaries, alongside (and composable with) the existing attempt-count budget (`max_attempts`) from Batch #32.

No monetary pricing, credit/subscription estimation, historical ranking, persistent presets, or hard pre-execution token cap is introduced. Only the local qualock check gains `max_tokens`; baseline creation, monitor, bisect, GitHub PR qualification, and the scheduler are unaffected.

## 2. Goals

- Give operators a provider-neutral signal for how many tokens a qualification run actually consumed, without requiring provider-specific pricing knowledge.
- Let operators constrain local qualification token exposure using a threshold, with semantics that are honest about being a checkpoint, not a hard limit.
- Preserve Batch #32 behavioral compatibility for callers who do not use `max_tokens`; Batch #40 intentionally expands usage/accounting artifacts and therefore does not claim byte-identical serialization.
- Keep the token accounting model simple enough to reason about across three structurally different provider usage reporting schemes, without inventing per-provider pricing tables.

## 3. Non-Goals (Batch #40 Boundary)

The following are explicitly out of scope for this batch and must not be introduced as side effects:

- Monetary cost estimation, credit accounting, or subscription-quota estimation of any kind, for any provider.
- Historical canary effectiveness tracking or runtime/token estimation across runs (deferred to #41).
- Provider-specific monetary estimates (deferred to #42, and explicitly kept outside qualification policy even then).
- Persistent, saved, or low-tech "preset" budgets (deferred, no target batch yet).
- A hard pre-execution token cap of any kind (mid-canary abort, request throttling, or refusal to start a canary based on a token estimate).
- Any change to config schema, baseline-lock schema, or canary schema.
- Any change to `qualification/policy.py` pass/fail semantics, resolver behavior, credential handling, or sandbox/source isolation.
- Token budgeting for baseline creation, `monitor`, `bisect`, GitHub PR qualification, or the scheduler. Only the local `qualock check` command accepts `max_tokens` in this batch.

### 3.1 Protected Surfaces

Unless a change is *directly* necessitated by this batch's goals, the following remain untouched:

- `agents/*`
- `github_pr/*`
- `release_monitor/*`
- `version_bisect/*`
- `scheduler/*`
- `source/*`
- `qualification/policy.py`
- `pyproject.toml`

### 3.2 Expected Surfaces

- Evidence models and the Codex/Claude/Antigravity evidence parsers
- Qualification models (`Usage`, `QualificationResult`)
- Run backends and the qualification executor
- `commands.py`, `cli.py`
- `evidence/storage.py`
- `report/render.py`, limited to surfacing the new usage/accounting fields. Existing `report.json` payload content such as `events_jsonl` is otherwise unchanged; Invariant E2 constrains only newly-added usage metadata.
- `README.md` / `ROADMAP.md`, updated only after implementation, exact-head verification, CI, and independent review land

## 4. Canonical Qualification `Usage` Model

### 4.1 Field Semantics

The canonical `Usage` model gains a precise, provider-neutral shape. Every field is defined in terms of **volume categories**, not provider-specific terminology, so that parsers translate provider vocabulary into this shape rather than the shape adapting per provider.

| Field | Meaning | Relationship |
|---|---|---|
| `input_tokens` | Normalized **total** input token volume for the attempt (or accumulated turn) | Superset; includes cached and cache-write subsets |
| `cached_input_tokens` | Cache-**read** subset/detail of `input_tokens` | Subset of `input_tokens`, informational |
| `cache_write_input_tokens` | Cache-**write**/cache-creation subset/detail of `input_tokens` | Subset of `input_tokens`, informational |
| `output_tokens` | Normalized **total** output token volume for the attempt | Superset; includes reasoning subset |
| `reasoning_output_tokens` | Reasoning/thinking subset/detail of `output_tokens` | Subset of `output_tokens`, informational |
| `observed` | `bool`, true only when trustworthy runtime/terminal usage was actually observed | Defaults to `False`; governs whether counters may be trusted, including for budgeting |

**Invariant U1 (non-negativity):** All observed counters (`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`) MUST be non-negative integers. No parser may clamp, silently coerce, or accept a negative usage counter. Existing strict parser contracts remain strict: malformed required Claude or Antigravity terminal usage raises the provider's existing evidence error. Codex's intentionally permissive event parser may preserve ordinary qualification behavior by marking usage unobserved instead of converting malformed usage into a new policy failure. In every path that reaches an `AttemptResult`, negative or otherwise untrustworthy usage MUST NOT appear as `observed=True`.

**Invariant U2 (no double counting):** The computed property

```
total_tokens = input_tokens + output_tokens
```

is the **only** definition of `total_tokens`. `cached_input_tokens`, `cache_write_input_tokens`, and `reasoning_output_tokens` are strictly informational subsets/details and MUST NEVER be added again into `total_tokens`. This is the single most important invariant in this batch, because every provider reports subset/detail fields differently, and it is the primary source of double-counting bugs if violated.

**Invariant U3 (observed is truthful, never fabricated):** `observed=True` may be set **only** when the parser/backend has direct evidence that the total input/output counters are trustworthy (i.e., came from a real terminal/runtime usage report, not inferred, not defaulted, not partially reconstructed). `Usage.observed` defaults to `False`, so `AttemptResult.usage` created through `field(default_factory=Usage)` can never fabricate an observed zero. If execution still produces an `AttemptResult` while usage could not be established, that attempt uses `observed=False` with zeroed/default counters — **never** `observed=True` with a fabricated zero. Provider parsers that fail closed on malformed required terminal usage continue to raise instead. A fabricated zero is indistinguishable from "genuinely used zero tokens" and would corrupt both reporting and budgeting. All in-tree `Usage(...)` construction is keyword-based; this batch does not introduce reliance on positional field order.

### 4.2 Why Subset/Detail Fields Exist

Cache-read, cache-write, and reasoning-output figures are valuable operational detail (e.g., "how much of my input was cache hits"), but they are not part of the additive cost model. Every provider considered in this batch reports at least one of these as a component of a "total" figure it also reports separately, and the historical failure mode analyzed while approving this design was: naively summing every field a provider returns, silently double-counting cache or reasoning tokens as both "component" and "whole." Fixing the model shape (rather than trusting each parser to remember not to double count) removes that failure class structurally.

## 5. Provider Normalization

Each parser/backend is responsible for translating its provider's native usage vocabulary into the canonical `Usage` shape above. No parser may introduce a `total_tokens`-shaped field of its own that is trusted directly; only `input_tokens`/`output_tokens` totals matter for the canonical total.

### 5.1 Codex

- Codex already reports `input_tokens` as a **total** (cache-read and other subsets are already folded in on the wire); no re-derivation is needed for the total.
- `cached_input_tokens` and (new) `cache_write_input_tokens` are captured as subset/detail fields alongside the existing total, without altering the total.
- `reasoning_output_tokens` is captured as a subset/detail of `output_tokens`, consistent with current behavior.
- The parser continues to accumulate usage across `turn.completed` events for the run, as it does today — this batch does not change the accumulation strategy, only the shape of what is accumulated.
- `cache_write_input_tokens` capture is **optional**: if the Codex event stream for a given session does not surface a cache-write figure, the field is simply absent/zero and does not affect `observed`.
- Codex remains intentionally permissive for usage metadata so ordinary qualification does not gain a new policy failure. `observed=True` requires at least one `turn.completed` usage object and trustworthy required totals on **every** accumulated usage object. If any such object has `input_tokens` or `output_tokens` missing, non-integer, boolean, or negative, usage for the whole attempt is `observed=False` and the parser does not raise solely for that usage defect. Missing/malformed optional subset fields (`cached_input_tokens`, optional cache-write detail, `reasoning_output_tokens`) are treated as detail `0` and do not by themselves clear `observed`. Counters attached to an `observed=False` attempt are never used for token sums or admission.
- Token-budget admission, however, **must not** treat unobserved Codex usage as zero. See §7.5 (unknown-usage handling) — this is enforced at the executor level regardless of which provider produced the gap.

### 5.2 Claude

Claude's raw usage vocabulary splits input into three distinct raw counters (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) rather than reporting a pre-summed total. Normalization is therefore additive on the Claude side only:

```
Usage.input_tokens          = raw.input_tokens + raw.cache_read_input_tokens + raw.cache_creation_input_tokens
Usage.cached_input_tokens   = raw.cache_read_input_tokens
Usage.cache_write_input_tokens = raw.cache_creation_input_tokens
Usage.output_tokens         = raw.output_tokens
```

- `raw.cache_creation_input_tokens` is **optional** and defaults to `0` for compatibility with Claude responses/events that predate or omit cache-creation reporting. Its absence does not affect `observed` as long as `raw.input_tokens` and `raw.output_tokens` are present and trustworthy.
- Optional `output_tokens_details.thinking_tokens`, when present, populates `reasoning_output_tokens` as a subset/detail of `output_tokens`. Its absence leaves `reasoning_output_tokens` at `0`/absent and does not affect `observed`.
- `observed=True` is set when a valid terminal result's usage block is present and all required counters are valid. Claude is deliberately strict for terminal usage: a negative required `input_tokens`, `cache_read_input_tokens`, `output_tokens`, or present `cache_creation_input_tokens`/reasoning-detail counter raises `ClaudeEvidenceError`, just as malformed required terminal fields already do. This is an explicit strictness increase for negative integers and is pinned by tests rather than left to implementer choice.
- This changes `AgentEvidence.input_tokens` semantics for Claude only: it becomes the inclusive normalized input total. The locked 2.1.260 transcript therefore re-pins from raw input `4` to normalized `18202` (`4 + 9035 + 9163`), with `cached_input_tokens=9035` and `cache_write_input_tokens=9163`.

**Rationale:** Claude's three-way split is the one provider shape in this batch where the "total" must be *constructed* rather than read directly. Building that construction into the canonical model definition (§4.1, Invariant U2) rather than leaving it as parser-specific arithmetic is what prevents this from silently becoming a double-count if a future maintainer adds a fourth Claude counter.

### 5.3 Antigravity

- The existing strict Antigravity parser is preserved as-is in terms of strictness policy: usage is only accepted from a validated terminal event, using the same validation rigor already in place.
- `input_tokens` is taken as reported (Antigravity already reports a total).
- `cached_input_tokens = cache_read` as reported.
- `cache_write_input_tokens = 0` always — Antigravity's protocol does not currently surface a cache-write concept, so this field is a fixed `0` for this provider rather than "absent," to make clear it is a known-zero, not an unobserved gap.
- `output_tokens` is taken as reported.
- `reasoning_output_tokens = thinking` as reported, when present.
- `observed=True` only after the existing strict terminal validation succeeds, unchanged from current behavior.
- **Important boundary:** Antigravity's provider `total_tokens` field keeps its existing validation only: when present it must be a non-negative integer. Batch #40 MUST NOT cross-check that provider field against component sums, derive canonical totals from it, or add any new failure mode based on assumptions about its semantics. The canonical `total_tokens` is always computed per Invariant U2.

### 5.4 Cross-Provider Consistency Table

| | Codex | Claude | Antigravity |
|---|---|---|---|
| Input total source | Provider-reported total (already inclusive) | Constructed: `input + cache_read + cache_creation` | Provider-reported total |
| `cached_input_tokens` | Provider subset field | `cache_read_input_tokens` | `cache_read` |
| `cache_write_input_tokens` | Provider subset field (optional) | `cache_creation_input_tokens` (default 0) | Always `0` (protocol has no concept) |
| Output total source | Provider-reported total | Provider-reported total | Provider-reported total |
| `reasoning_output_tokens` | Provider subset field | `output_tokens_details.thinking_tokens` (optional) | `thinking` field |
| `observed` trigger | Trustworthy accumulated turn usage | Valid terminal result usage block | Strict terminal validation (existing) |
| Provider's own "total" field, if any | Not used as canonical total | Not applicable (no provider total field) | Not used as canonical total |

## 6. Evidence Layer

- `AgentEvidence` mirrors the new fields relevant to this batch: cache-write detail and a `usage_observed` flag, matching the canonical `Usage.cache_write_input_tokens` and `Usage.observed` respectively. For Claude only, `AgentEvidence.input_tokens` intentionally changes from raw ordinary input to the inclusive normalized input total defined in §5.2.
- Run backends propagate parser-produced usage into the canonical qualification `Usage` unchanged in value — backends perform no additional arithmetic on usage; all normalization happens in the parser layer (§5).
- **Invariant E1:** when a parser/runtime path still returns evidence despite being unable to establish trustworthy usage, it leaves `AgentEvidence.usage_observed = False` (and correspondingly `Usage.observed = False` once propagated). Existing strict provider parser errors remain errors and may instead make the attempt invalid through the existing backend path. It is never acceptable to emit a fake observed-zero usage value to "fill in" a gap.
- **Invariant E2 (no sensitive leakage):** summary metadata attached to evidence for token-usage purposes MUST NOT include raw stdout/stderr content or credentials. Token usage is a small set of integers plus a boolean; there is no legitimate reason for usage-summary metadata to carry provider transcript or secret material, and this batch must not introduce a code path where it does (e.g., via a "debug dump usage context" convenience field).

## 7. Token-Denominated Local Qualification Budget

### 7.1 Surface

- `execute_check` (the local qualification executor entry point) gains an optional parameter `max_tokens: int | None`.
- CLI: `qualock check` gains a `--max-tokens` flag, mirroring `--max-attempts` in naming, validation style, and help conventions for consistency.
- Validation: `max_tokens` MUST be a positive integer. Both direct `execute_check` validation and CLI validation reject `<= 0` as `CommandError`; the CLI surfaces it as **exit code 3**, mirroring the existing two-layer `--max-attempts` contract.

### 7.2 What "Checked Between Complete Canaries" Means

This is the central semantic commitment of this batch and MUST be stated verbatim in spirit (not necessarily verbatim in text) anywhere `--max-tokens` is documented in help output or user-facing UI:

> The token threshold is checked **between complete canaries**. It is **not** a hard cap and **not** a billing limit. Once a canary has started, all of its configured paired/interleaved baseline/candidate attempts run to completion and a normal verdict is computed for that canary, regardless of token usage observed during it.

Concretely:

- **Invariant B1 (no partial canary):** Once a canary has passed admission and started, no configured attempt within it is skipped because of `max_tokens` or `max_attempts`. A canary is atomic after admission. This does not require the first canary to start when the existing attempt-budget gate cannot fund one complete canary.
- **Invariant B2 (checkpoint, not cap):** Token-budget admission is evaluated **only** at boundaries after a complete canary, never mid-canary and never before the first canary. The existing attempt-budget gate remains a pre-canary admission check, including before canary one.
- **Invariant B3 (overshoot is expected and must be described honestly):** Because the check happens between canaries, actual observed total token usage for the run may exceed `max_tokens` by up to the cost of the last complete canary that was allowed to run. This is expected behavior, not a bug. Documentation, help text, and rendered output MUST NOT describe `max_tokens` using language implying a hard cap or a billing limit (e.g., must avoid words like "limit enforced exactly," "will not exceed," "billing cap"). Acceptable framing: "threshold checked between complete canaries."

### 7.3 Admission Order at a Canary Boundary

Before starting **every** canary, including the first, the executor evaluates the existing attempt-budget admission gate first. Token-budget admission is then evaluated only for canaries after the first, because no prior observed usage exists before canary one:

1. **Attempt-budget admission first** (existing `max_attempts` logic from Batch #32, unchanged), before every canary. If fewer than one complete canary's attempts remain, that canary is skipped exactly as today; a small attempt budget may therefore run nothing.
2. **Token-budget admission second**, only when `max_tokens` is active and at least one canary has already completed:
   - If token accounting is known for all attempts run so far and cumulative observed usage `>= max_tokens`, the next canary is skipped.
   - If token accounting is unknown for any started attempt so far, the run fails closed and the next canary is skipped.

When both gates would stop the same canary, the attempt-budget reason wins because that gate is evaluated first.

**Invariant B4 (fail-closed on unknown usage):** Unknown usage is never treated as "0 tokens used, keep going." An executor that cannot verify usage for a started attempt must treat that as budget-exhausted-or-unknown for the purpose of deciding whether to admit the next canary, and stop rather than guess.

### 7.4 Skipped Canaries and Result Completeness

- **Invariant B5:** Any canary configured to run that is skipped due to either `max_attempts` or `max_tokens` admission causes the overall qualification result to be `INCOMPLETE` — this is the same completeness contract established for `max_attempts` in Batch #32, and `max_tokens` participates in it identically, not as a separate/parallel completeness signal.

### 7.5 Unknown Usage Handling (Precise State Machine)

Given token budgeting is active (`max_tokens is not None`):

1. Before each canary, run the existing attempt-budget admission check. If it refuses the canary, record the existing attempt-budget skip reason and do not evaluate token admission for that canary.
2. Before canary one, skip token admission because no attempt has run yet. If attempt admission passes, run canary one completely.
3. Before each later canary, inspect **all** attempts already started:
   - If any has `observed=False`, skip this and every remaining canary fail-closed with `INCOMPLETE: skipped because token usage was unavailable for one or more attempts (max_tokens=N)`.
   - Otherwise cumulative `observed_tokens` is exact. If it is `>= max_tokens`, skip this and every remaining canary with `INCOMPLETE: skipped by token budget (max_tokens=N, observed_tokens=M)`.
   - Otherwise admit the canary and run all configured paired/interleaved attempts to completion.
4. After an admitted canary completes, update accounting; the next token decision occurs only at the next canary boundary.

**Invariant B6 (reason must be bounded and specific):** The two token stop categories use the deterministic prefixes above. They are distinct from the existing attempt-budget reason. If both gates could stop a canary, attempt-budget-first ordering means the attempt-budget reason is authoritative.

### 7.6 Token Accounting Includes Invalid Attempts

**Invariant B7:** The cumulative token total used for budget admission includes tokens from **invalid** attempts (attempts that failed, errored, or otherwise did not count as a valid qualification data point), *as long as their usage was observed*. Token cost is a resource-consumption signal, not a validity signal — a failed attempt still spent tokens, and excluding it would understate real cost and could let a pathological "fail fast, retry forever" loop evade the budget.

### 7.7 Execution Order When Budgets Are Active

- **Invariant B8:** Critical-first execution is used iff the run is constraining: `(max_attempts is not None and max_attempts < full_suite_attempts) or max_tokens is not None`. An unconstraining attempt budget (`max_attempts >= full_suite_attempts`) preserves existing configuration order exactly; an active token budget is constraining by definition because its future token cost is not known before execution.
- **Invariant B9:** Regardless of internal execution order, **returned results remain in original configuration order**. Critical-first execution must never be observable in the shape or ordering of `QualificationResult`'s attempt list.
- **Invariant B10:** When no budget is active (`max_attempts is None and max_tokens is None`), execution order is unchanged from pre-#32 behavior — this batch introduces no new reordering for unbudgeted runs.

### 7.8 Composability with `max_attempts`

- **Invariant B11 (backward compatibility of `max_attempts` alone):** A qualification run with `max_attempts` set and `max_tokens` unset preserves Batch #32 admission decisions, execution order, `run_order`, returned execution ordering, verdicts, and completeness semantics. Batch #40 intentionally expands serialized usage/accounting artifacts, so no byte-for-byte artifact identity is claimed.
- **Invariant B12 (both active):** When both `max_attempts` and `max_tokens` are set, admission at each canary boundary is evaluated in the fixed order given in §7.3 — attempt-budget check first, token-budget check second. Either gate stopping the run is sufficient to stop it; neither gate "waits" for the other.

## 8. `QualificationResult` Accounting

Batch #40 adds four new `QualificationResult` fields, each with a safe default:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_attempts` | `int \| None` | `None` | Echo of the attempt budget in effect for this run; Batch #32 had executor input only and did not persist this on `QualificationResult` |
| `max_tokens` | `int \| None` | `None` | Echo of the token budget in effect for this run, if any |
| `attempts_used` | `int` | `0` | Count of every attempt that was **started**, regardless of validity or budget involvement |
| `observed_tokens` | `int \| None` | `None` | See below |

**Invariant R1 (`attempts_used` semantics):** `attempts_used` counts every started attempt, full stop — matching Invariant B7's philosophy that "started" is the unit of accounting for cost, independent of validity.

**Invariant R2 (`observed_tokens` all-or-nothing exactness):** `observed_tokens` is the exact sum of `total_tokens` (per Invariant U2) across every started attempt **if and only if every started attempt had `observed=True`** usage. If even one started attempt has `observed=False`, `observed_tokens` MUST be `None` — never a partial sum, never a best-effort estimate. If zero attempts were started, the exact empty sum is `0`, not `None`; this preserves the distinction between "nothing ran" and "usage was unavailable."

**Invariant R3 (usage recorded even without a token budget):** A qualification run with `max_tokens=None` (no token budget requested) still populates `observed_tokens` according to Invariant R2, if usage is known. Token *observation and reporting* is independent of whether token *budgeting* was requested — an operator should be able to see how many tokens a run cost even if they didn't set a threshold.

**Invariant R4 (executor is sole authority):** Only the qualification executor computes and sets `attempts_used` and `observed_tokens`. No downstream layer (CLI, report renderer, storage) recomputes or overrides these values; they render/persist what the executor produced.

## 9. Artifacts and Rendering

### 9.1 `report.json`

- The expanded `Usage` shape (§4) is included via normal model serialization — no bespoke serialization logic for the new fields. Existing serialization mechanisms already used for `Usage` are extended, not replaced. This intentionally supersedes Batch #32's artifact byte-identity expectation for `report.json`; the behavioral qualification result remains compatible per Invariant B11. Existing payload content such as `events_jsonl` is otherwise unchanged.

### 9.2 `qualification.json`

- Explicitly records `max_attempts`, `max_tokens`, `attempts_used`, and `observed_tokens` as top-level fields in the persisted qualification artifact. This intentionally supersedes Batch #32's byte-identical `qualification.json` artifact expectation; tests that pin the old exact key set must be deliberately re-baselined.
- **Invariant A1 (writer-side forward compatibility):** No in-tree `qualification.json` reader exists in Batch #40. This batch MUST NOT invent one. Older artifacts are not rewritten. Any future reader must treat absent accounting fields as the §8 defaults, but that future-reader behavior is a contract only, not implementation scope for this batch.

### 9.3 Baseline Creation

- Baseline creation gains the expanded `Usage` **detail** fields (cache-read/cache-write/reasoning subsets, `observed`) purely as richer usage reporting, because baseline creation already reports usage today and this batch's `Usage` shape supersedes the old one.
- **Invariant A2:** Baseline creation does **not** gain a `max_tokens` budget parameter or any budget-checking behavior. This batch's budgeting feature is scoped to local qualock check only (§3).

### 9.4 Default (Low-Tech) Output

Default human-facing output for a completed run reports observed usage in a single plain line, thousands-separated, with no jargon:

```
Observed model tokens: 57,231
```

When a token budget was active, the same line gains a bounded, plain-language parenthetical:

```
Observed model tokens: 57,231 (threshold 50,000; checked between complete canaries)
```

When usage is unknown (`observed_tokens is None`), the line states unavailability plainly, without a number:

```
Observed model tokens: unavailable
```

When usage is unknown and a token threshold was active, the threshold framing remains visible:

```
Observed model tokens: unavailable (threshold 50,000; checked between complete canaries)
```

**Invariant D1:** The default output line MUST NOT include cache-read, cache-write, or reasoning-output breakdowns. Default output is intentionally low-tech; per-category detail belongs to technical output only (§9.5), to avoid cluttering the primary signal operators check first.

### 9.5 Technical Terminal / Markdown Output

Technical-mode rendering (terminal verbose mode and/or Markdown report) includes, at minimum:

- Attempts used (`attempts_used`)
- Observed token total, or an explicit "unavailable" marker, matching Invariant R2's all-or-nothing rule (no partial/estimated totals ever rendered as if exact)
- Any active budgets. `max_tokens` MUST use the same "threshold; checked between complete canaries" framing as help text (Invariant B2/B3). `max_attempts` retains its existing attempt-count wording and MUST NOT be mislabeled as a token-style threshold.

Cache/reasoning subset detail MAY appear in technical output (it is not excluded there), but is still governed by Invariant U2/U3 — subsets are always presented as detail of a total, never summed into a second total.

## 10. Compatibility Summary

| Scenario | Behavior |
|---|---|
| No `max_attempts`, no `max_tokens` | Execution order, admission, verdict, and completeness unchanged from pre-#32 behavior (Invariant B10). Usage/accounting is now reported if observed, so artifacts and terminal output intentionally gain fields/lines. |
| `max_attempts` only, constraining | Batch #32 admission decisions, critical-first execution, `run_order`, returned execution ordering, verdicts, and completeness are preserved. Artifacts intentionally gain Batch #40 accounting fields. |
| `max_attempts` only, unconstraining | Existing configuration execution order is preserved exactly, including when `max_attempts == full_suite_attempts` or is larger. |
| `max_tokens` only | New behavior per §7; critical-first execution applies because token cost cannot be known before running. `run_order` may therefore differ from an unbudgeted run, while returned executions remain in configuration order. |
| Both set | Attempt-budget admission evaluated first before every canary; token admission is second and begins only after the first completed canary (Invariant B12). |
| Historical `qualification.json` | No in-tree reader exists and none is added. Future readers are contractually required to default absent accounting fields per Invariant A1. |
| New `qualification.json` read by old code | Out of scope — no backward-reader compatibility for old code reading new artifacts is claimed or required. |

## 11. Test Plan

All items below must be pinned as explicit, named test cases (not merely covered incidentally):

1. **Provider total pinning, no double-counting:**
   - Codex: normalized `input_tokens` reflects the already-inclusive provider input figure; canonical `total_tokens` is `input_tokens + output_tokens`, with cache/cache-write/reasoning subsets never added again.
   - Claude: total is exactly `input + cache_read + cache_creation`; a test must assert that a nonzero `cache_creation_input_tokens` is reflected in the total exactly once.
   - Antigravity: normalized input/output are the reported component figures and canonical `total_tokens` is their sum; provider `total_tokens` never drives or cross-checks the canonical total. `cache_write_input_tokens` is pinned to `0`, not absent/`None`.
   - Cross-cutting: a test asserting `total_tokens == input_tokens + output_tokens` for representative fixtures from all three providers, with nonzero cache/reasoning subsets present, to catch any accidental re-addition.
2. **Claude cache-read + cache-creation normalization**, including the default-`0` compatibility path for `cache_creation_input_tokens` absence.
   - The locked 2.1.260 golden transcript is intentionally re-pinned to `input_tokens=18202`, `cached_input_tokens=9035`, `cache_write_input_tokens=9163`, and `output_tokens=74`.
3. **Unknown usage is never treated as real zero:** a fixture where a provider fails to report usage must produce `observed=False`, and a budget-admission test must confirm the executor treats this as fail-closed (stops), not as "0 tokens consumed, continue."
4. **No partial canary:** a test that sets `max_tokens` below the cost of the very first canary and asserts the *entire* first canary (all its configured attempts) still completes, with a normal verdict, before the executor stops.
5. **Threshold below first-canary cost still lets it finish, then stops:** explicit assertion that the run stops immediately after the first canary (does not attempt a second) once cumulative usage is known to meet/exceed the threshold.
6. **Exact-reach and overshoot both stop before the next canary:** a case where cumulative usage lands exactly on `max_tokens`, and a separate case where it overshoots it, both must stop admission for the next canary identically.
7. **Skipped canary implies `INCOMPLETE`:** for both `max_attempts`-caused and `max_tokens`-caused skips, and for the "unknown usage" fail-closed skip.
8. **Invalid-attempt usage still counts if observed:** an attempt that fails/errors but has `observed=True` usage contributes to `observed_tokens` and to budget admission.
9. **`max_attempts` behavioral regression:** existing Batch #32 admission/order/verdict tests must remain semantically unchanged, including unconstraining `max_attempts` values preserving configuration order. Artifact-shape assertions that pin the old exact `qualification.json` key set or old `report.json` `Usage` shape are deliberately updated for the four new result fields and expanded usage model; this is an explicit re-baseline, not an accidental regression.
10. **Combination ordering:** a test with both budgets active, arranged so that if token-check ran first it would produce a different stop point than attempt-check-first — asserting attempt-budget-first ordering (Invariant B12) is actually observed, not just documented.
11. **No-budget behavior unchanged except usage reporting:** a snapshot/characterization test on an unbudgeted run's ordering, completeness, and verdict, confirming only `observed_tokens`/`attempts_used` reporting is newly populated.
12. **CLI validation:** `--max-tokens 0` and `--max-tokens -5` both produce `CommandError`/exit code 3; `--max-tokens` help text includes the "checked between complete canaries" framing verbatim in spirit.
13. **Artifact metadata writer:** `qualification.json` contains the four new top-level fields with exact values, including the zero-attempt case (`attempts_used=0`, `observed_tokens=0`). Update the existing exact key-set assertion in `tests/unit/test_commands.py`; do not add an artifact reader or an old-artifact loading test.
14. **All three parser suites** updated/extended for the new `Usage` shape: Codex whole-attempt `observed=False` on malformed required totals without raising; Claude negative terminal counters raise `ClaudeEvidenceError`; Antigravity keeps its existing strict non-negative checks without component-sum validation; all three pin no-double-counting and no new usage-summary leakage.
15. **Backend / host-backend tests** confirming propagation from evidence to qualification `Usage` performs no additional arithmetic (parsers own normalization exclusively).
16. **Budget-specific executor test module** covering all state-machine branches in §7.5.
17. **Command / CLI / report / storage integration tests** covering all four low-tech lines in §9.4 (known/plain, known+threshold, unavailable/plain, unavailable+threshold), technical output (§9.5), and direct/CLI nonpositive `max_tokens` validation.

## 12. Review Gates (Required Before Roadmap Update)

All of the following must pass before this feature may be marked delivered in `ROADMAP.md`:

- Full test suite (all items in §11) green.
- Ruff clean.
- Strict mypy clean.
- `compileall` clean.
- Exact-head verification: record and review the committed HEAD SHA that all final local gates ran against.
- `git diff --check` clean on that exact HEAD — no whitespace errors or conflict markers.
- Protected-scope proof: an explicit diff-scope check demonstrating no changes landed under §3.1's protected paths unless justified and called out individually in the PR description.
- Windows CI green (this repo has prior history of Windows-specific credential/path issues per recent commits; token-budget CLI/help text and integer parsing must be verified cross-platform).
- No authenticated agent qualification run is required to validate this batch — all provider-parsing behavior is testable via fixtures/unit tests without live credentials, consistent with this being a design-and-implementation batch, not a live-agent validation batch.
- Independent review sign-off, separate from the implementing author.

Only after **all** of the above are satisfied may `ROADMAP.md` be updated to mark "provider-neutral, token-denominated local qualification budgeting" as delivered.

## 13. Roadmap Update Content (Post-Verification Only)

When the gates in §12 are satisfied, `ROADMAP.md` should be updated to:

- Move "provider-neutral token-denominated local qualification budgeting" to the delivered/completed section.
- Retain, in the "Next" section (not delivered), explicitly:
  - Historical canary effectiveness tracking and runtime/token estimation (→ #41).
  - Provider-specific monetary estimates (→ #42, explicitly noted as living outside qualification policy).

This document does not itself edit `ROADMAP.md`; that edit happens only as part of the implementation PR, after review gates pass.

## 14. Deferred Follow-Ups (Explicit Ordering)

1. **#41 — Historical canary effectiveness + runtime/token estimates.** Builds on the `observed_tokens`/`attempts_used` accounting introduced here to produce cross-run historical signals. Explicitly deferred; this batch introduces no persistence or aggregation across runs.
2. **#42 — Provider-specific monetary estimates**, kept outside qualification policy (i.e., advisory/reporting only, never a pass/fail input). Depends on #41's historical data plumbing and on provider pricing being tracked as a separate, clearly-labeled, non-authoritative estimate.
3. **Later, unscheduled — low-tech persistent presets** (e.g., saved `--max-tokens` defaults per project/profile). No target batch number assigned; explicitly not part of #41 or #42's scope and not committed to any roadmap timing yet.

## 15. Design Self-Review

- **Placeholder scan:** no unresolved placeholder markers remain; every field, invariant, and behavior above is fully specified with concrete values (e.g., exit code `3`, exact formulas, exact default values).
- **Contradiction check:** the two budgets (`max_attempts`, `max_tokens`) are defined with a single, non-overlapping admission order (attempt-first, then token) at every decision point in this document (§7.3, §7.8, Invariant B12), with no alternate ordering asserted elsewhere.
- **Ambiguity check:** "checked between complete canaries" is defined precisely as a state-machine (§7.5) rather than left as prose alone, specifically to prevent divergent interpretations by implementers (e.g., "does mid-canary partial usage count?" — no, per Invariant B1/B2).
- **Scope-creep check:** every capability introduced (normalized `Usage`, `max_tokens`, `attempts_used`/`observed_tokens`) is scoped strictly to local qualock check and its supporting layers (evidence, parsers, executor, CLI, artifacts). No change is proposed to baseline/config/canary schema, policy pass/fail logic, or any protected surface in §3.1. Monetary/historical features are explicitly deferred rather than partially implemented.
- **Compatibility check:** every new field has a documented safe default (§8); the absence contract for historical artifacts is forward-looking only because no in-tree reader exists (§9.2 Invariant A1); and `max_attempts` behavioral compatibility is pinned by explicit regression obligations (§11 item 9) rather than by impossible artifact byte-identity claims.
- **Honesty-of-framing check:** all user-facing language templates in this document (§7.2, §9.4) avoid "cap," "limit enforced," or "will not exceed" phrasing, consistent with Invariant B3.

No unresolved concerns remain for implementation to begin against this spec.
