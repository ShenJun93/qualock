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
- Let operators cap local qualification cost risk using a token threshold, with semantics that are honest about being a checkpoint, not a hard limit.
- Preserve exact backward compatibility for callers who do not use `max_tokens`, including byte-for-byte behavior of `max_attempts`-only runs.
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
- `report/render.py` (safety-relevant changes only, e.g. avoiding leaking raw provider payloads — no rendering redesign)
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
| `observed` | `bool`, true only when trustworthy runtime/terminal usage was actually observed | Governs whether counters may be trusted, including for budgeting |

**Invariant U1 (non-negativity):** All observed counters (`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`) MUST be non-negative integers. No parser may clamp, silently coerce, or accept a negative usage counter. Existing strict parser contracts remain strict: malformed required Claude or Antigravity terminal usage raises the provider's existing evidence error. Codex's intentionally permissive event parser may preserve ordinary qualification behavior by marking usage unobserved instead of converting malformed usage into a new policy failure. In every path that reaches an `AttemptResult`, negative or otherwise untrustworthy usage MUST NOT appear as `observed=True`.

**Invariant U2 (no double counting):** The computed property

```
total_tokens = input_tokens + output_tokens
```

is the **only** definition of `total_tokens`. `cached_input_tokens`, `cache_write_input_tokens`, and `reasoning_output_tokens` are strictly informational subsets/details and MUST NEVER be added again into `total_tokens`. This is the single most important invariant in this batch, because every provider reports subset/detail fields differently, and it is the primary source of double-counting bugs if violated.

**Invariant U3 (observed is truthful, never fabricated):** `observed=True` may be set **only** when the parser/backend has direct evidence that the total input/output counters are trustworthy (i.e., came from a real terminal/runtime usage report, not inferred, not defaulted, not partially reconstructed). If execution still produces an `AttemptResult` while usage could not be established, that attempt uses `observed=False` with zeroed/default detail counters — **never** `observed=True` with a fabricated zero. Provider parsers that already fail closed on malformed required terminal usage continue to raise instead; this batch does not weaken those strict contracts. A fabricated zero is indistinguishable from "genuinely used zero tokens" and would corrupt both reporting and budgeting.

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
- `observed` is set to `True` only when the required input/output totals for the turn(s) are trustworthy (i.e., present and well-formed on the terminal/accumulated usage event). Malformed or partial usage on an otherwise-successful ordinary (non-budgeted) qualification run **must not** introduce a new policy failure that would not have occurred before this batch — ordinary qualification pass/fail is governed by `qualification/policy.py`, which this batch does not touch, and usage parsing failures must degrade to `observed=False` rather than raise or fail the check.
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
- `observed=True` is set when a valid terminal result's usage block is present (i.e., the message/result usage the parser already treats as authoritative today), consistent with the strict/terminal-only observation philosophy shared with the other two providers.

**Rationale:** Claude's three-way split is the one provider shape in this batch where the "total" must be *constructed* rather than read directly. Building that construction into the canonical model definition (§4.1, Invariant U2) rather than leaving it as parser-specific arithmetic is what prevents this from silently becoming a double-count if a future maintainer adds a fourth Claude counter.

### 5.3 Antigravity

- The existing strict Antigravity parser is preserved as-is in terms of strictness policy: usage is only accepted from a validated terminal event, using the same validation rigor already in place.
- `input_tokens` is taken as reported (Antigravity already reports a total).
- `cached_input_tokens = cache_read` as reported.
- `cache_write_input_tokens = 0` always — Antigravity's protocol does not currently surface a cache-write concept, so this field is a fixed `0` for this provider rather than "absent," to make clear it is a known-zero, not an unobserved gap.
- `output_tokens` is taken as reported.
- `reasoning_output_tokens = thinking` as reported, when present.
- `observed=True` only after the existing strict terminal validation succeeds, unchanged from current behavior.
- **Important boundary:** if Antigravity's own wire protocol reports a `total_tokens`-shaped field, it MAY be validated for internal consistency (e.g., sanity-checking against the parser's own component sum) but it is **never** treated as *the* cross-provider `total_tokens`. The canonical `total_tokens` is always computed per Invariant U2, not read from any provider payload.

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

- `AgentEvidence` mirrors the new fields relevant to this batch: cache-write detail and a `usage_observed` flag, matching the canonical `Usage.cache_write_input_tokens` and `Usage.observed` respectively.
- Run backends propagate parser-produced usage into the canonical qualification `Usage` unchanged in value — backends perform no additional arithmetic on usage; all normalization happens in the parser layer (§5).
- **Invariant E1:** any parse or runtime failure that prevents establishing usage for an attempt leaves `AgentEvidence.usage_observed = False` (and correspondingly `Usage.observed = False` once propagated). It is never acceptable to emit a fake zero usage value to "fill in" a gap — this applies symmetrically to evidence and qualification layers, echoing Invariant U3.
- **Invariant E2 (no sensitive leakage):** summary metadata attached to evidence for token-usage purposes MUST NOT include raw stdout/stderr content or credentials. Token usage is a small set of integers plus a boolean; there is no legitimate reason for usage-summary metadata to carry provider transcript or secret material, and this batch must not introduce a code path where it does (e.g., via a "debug dump usage context" convenience field).

## 7. Token-Denominated Local Qualification Budget

### 7.1 Surface

- `execute_check` (the local qualification executor entry point) gains an optional parameter `max_tokens: int | None`.
- CLI: `qualock check` gains a `--max-tokens` flag, mirroring `--max-attempts` in naming, validation style, and help conventions for consistency.
- Validation: `max_tokens` MUST be a positive integer. A value `<= 0` is a `CommandError` at the command layer, surfaced as **CLI exit code 3**, matching the existing `--max-attempts` validation contract.

### 7.2 What "Checked Between Complete Canaries" Means

This is the central semantic commitment of this batch and MUST be stated verbatim in spirit (not necessarily verbatim in text) anywhere `--max-tokens` is documented in help output or user-facing UI:

> The token threshold is checked **between complete canaries**. It is **not** a hard cap and **not** a billing limit. Once a canary has started, all of its configured paired/interleaved baseline/candidate attempts run to completion and a normal verdict is computed for that canary, regardless of token usage observed during it.

Concretely:

- **Invariant B1 (no partial canary):** No configured attempt within an already-started canary is ever skipped because of `max_tokens` (or `max_attempts` — see the Batch #32 spec for that invariant, which is preserved unchanged). A canary is atomic with respect to both budgets.
- **Invariant B2 (checkpoint, not cap):** Budget admission is evaluated **only** at canary boundaries, i.e., before starting the *next* canary, never mid-canary.
- **Invariant B3 (overshoot is expected and must be described honestly):** Because the check happens between canaries, actual observed total token usage for the run may exceed `max_tokens` by up to the cost of the last complete canary that was allowed to run. This is expected behavior, not a bug. Documentation, help text, and rendered output MUST NOT describe `max_tokens` using language implying a hard cap or a billing limit (e.g., must avoid words like "limit enforced exactly," "will not exceed," "billing cap"). Acceptable framing: "threshold checked between complete canaries."

### 7.3 Admission Order at a Canary Boundary

Before starting each canary after the first, the executor evaluates admission in this fixed order:

1. **Attempt-budget admission first** (existing `max_attempts` logic from Batch #32, unchanged). If attempt budget alone would stop the run, it does, exactly as before this batch — this governs the interaction and ordering rule below.
2. **Token-budget admission second**, evaluated only if attempt-budget admission did not already stop the run:
   - If token accounting is *known* (see §7.5) for all attempts run so far, and cumulative observed usage `>= max_tokens`, the next canary is skipped (budget reached or exceeded).
   - If token accounting is *unknown* for any started attempt so far (i.e., some started attempt has `observed=False`), the run **fails closed**: the next canary is skipped, because the executor cannot honestly claim the budget has *not* been exceeded.

**Invariant B4 (fail-closed on unknown usage):** Unknown usage is never treated as "0 tokens used, keep going." An executor that cannot verify usage for a started attempt must treat that as budget-exhausted-or-unknown for the purpose of deciding whether to admit the next canary, and stop rather than guess.

### 7.4 Skipped Canaries and Result Completeness

- **Invariant B5:** Any canary configured to run that is skipped due to either `max_attempts` or `max_tokens` admission causes the overall qualification result to be `INCOMPLETE` — this is the same completeness contract established for `max_attempts` in Batch #32, and `max_tokens` participates in it identically, not as a separate/parallel completeness signal.

### 7.5 Unknown Usage Handling (Precise State Machine)

Given token budgeting is active (`max_tokens is not None`):

1. Run the current canary to completion (all its configured attempts) — this happens unconditionally per Invariant B1, before any token check is consulted.
2. Determine whether **all** attempts started up to and including this canary have `observed=True` usage.
   - If yes: cumulative `observed_tokens` is a trustworthy sum; proceed to compare against `max_tokens` as in §7.3.
   - If no (at least one started attempt has `observed=False`): the executor **stops admitting further canaries**, records a bounded, specific reason (e.g., "token usage unavailable for one or more attempts; cannot verify budget") — not a generic error, and not silence — and the result is `INCOMPLETE` per Invariant B5.
3. In both stopping cases, no attempt beyond the current (already-completed) canary is started.

**Invariant B6 (reason must be bounded and specific):** The stop reason recorded for a token-budget-unavailable stop must be a fixed, recognizable reason string/category distinct from "attempt budget reached" and distinct from "token budget reached," so that operators and tests can distinguish "we stopped because we hit the threshold" from "we stopped because we could not verify usage."

### 7.6 Token Accounting Includes Invalid Attempts

**Invariant B7:** The cumulative token total used for budget admission includes tokens from **invalid** attempts (attempts that failed, errored, or otherwise did not count as a valid qualification data point), *as long as their usage was observed*. Token cost is a resource-consumption signal, not a validity signal — a failed attempt still spent tokens, and excluding it would understate real cost and could let a pathological "fail fast, retry forever" loop evade the budget.

### 7.7 Execution Order When Budgets Are Active

- **Invariant B8:** If either `max_attempts` or `max_tokens` (or both) is active, the executor uses **critical-first execution** order internally (i.e., attempts most likely to determine an early stop/verdict are prioritized for scheduling), consistent with the ordering behavior already established for `max_attempts` alone in Batch #32. This is purely a scheduling optimization for budgeted runs and does not change semantics.
- **Invariant B9:** Regardless of internal execution order, **returned results remain in original configuration order**. Critical-first execution must never be observable in the shape or ordering of `QualificationResult`'s attempt list.
- **Invariant B10:** When no budget is active (`max_attempts is None and max_tokens is None`), execution order is unchanged from pre-#39 behavior — this batch introduces no new reordering for unbudgeted runs.

### 7.8 Composability with `max_attempts`

- **Invariant B11 (backward compatibility of `max_attempts` alone):** A qualification run with `max_attempts` set and `max_tokens` unset behaves **exactly** as specified in the Batch #32 design — byte-for-byte identical admission decisions, ordering, and completeness semantics. This batch adds a second, independent gate; it does not alter the first.
- **Invariant B12 (both active):** When both `max_attempts` and `max_tokens` are set, admission at each canary boundary is evaluated in the fixed order given in §7.3 — attempt-budget check first, token-budget check second. Either gate stopping the run is sufficient to stop it; neither gate "waits" for the other.

## 8. `QualificationResult` Accounting

New fields, all backward-compatible via safe defaults:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_attempts` | `int \| None` | `None` | Echo of the attempt budget in effect for this run (already exists from #39; listed here for completeness of the accounting group) |
| `max_tokens` | `int \| None` | `None` | Echo of the token budget in effect for this run, if any |
| `attempts_used` | `int` | `0` | Count of every attempt that was **started**, regardless of validity or budget involvement |
| `observed_tokens` | `int \| None` | `None` | See below |

**Invariant R1 (`attempts_used` semantics):** `attempts_used` counts every started attempt, full stop — matching Invariant B7's philosophy that "started" is the unit of accounting for cost, independent of validity.

**Invariant R2 (`observed_tokens` all-or-nothing exactness):** `observed_tokens` is the exact sum of `total_tokens` (per Invariant U2) across every started attempt **if and only if every started attempt had `observed=True`** usage. If even one started attempt has `observed=False`, `observed_tokens` MUST be `None` — never a partial sum, never a best-effort estimate. A partial sum would look precise while being silently wrong, which is worse than an honest "unavailable."

**Invariant R3 (usage recorded even without a token budget):** A qualification run with `max_tokens=None` (no token budget requested) still populates `observed_tokens` according to Invariant R2, if usage is known. Token *observation and reporting* is independent of whether token *budgeting* was requested — an operator should be able to see how many tokens a run cost even if they didn't set a threshold.

**Invariant R4 (executor is sole authority):** Only the qualification executor computes and sets `attempts_used` and `observed_tokens`. No downstream layer (CLI, report renderer, storage) recomputes or overrides these values; they render/persist what the executor produced.

## 9. Artifacts and Rendering

### 9.1 `report.json`

- The expanded `Usage` shape (§4) is included via normal model serialization — no bespoke serialization logic for the new fields. Existing serialization mechanisms already used for `Usage` are extended, not replaced.

### 9.2 `qualification.json`

- Explicitly records `max_attempts`, `max_tokens`, `attempts_used`, and `observed_tokens` as top-level (or clearly-scoped) fields in the persisted qualification artifact.
- **Invariant A1 (old-artifact tolerance):** Older `qualification.json` artifacts written before this batch do not have these fields. Readers MUST tolerate their absence (treat as the same defaults given in §8) rather than requiring a migration or rewrite of historical artifacts. No artifact rewrite tooling is introduced in this batch.

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

**Invariant D1:** The default output line MUST NOT include cache-read, cache-write, or reasoning-output breakdowns. Default output is intentionally low-tech; per-category detail belongs to technical output only (§9.5), to avoid cluttering the primary signal operators check first.

### 9.5 Technical Terminal / Markdown Output

Technical-mode rendering (terminal verbose mode and/or Markdown report) includes, at minimum:

- Attempts used (`attempts_used`)
- Observed token total, or an explicit "unavailable" marker, matching Invariant R2's all-or-nothing rule (no partial/estimated totals ever rendered as if exact)
- Any active limits (`max_attempts`, `max_tokens`), rendered using the same "checked between complete canaries" framing as help text (Invariant B2/B3) — technical output is not exempt from the honesty requirement about what the threshold means.

Cache/reasoning subset detail MAY appear in technical output (it is not excluded there), but is still governed by Invariant U2/U3 — subsets are always presented as detail of a total, never summed into a second total.

## 10. Compatibility Summary

| Scenario | Behavior |
|---|---|
| No `max_attempts`, no `max_tokens` | Execution order, admission, and completeness unchanged from pre-#39 behavior (Invariant B10). Usage is now reported if observed (Invariant R3), which is the only visible change. |
| `max_attempts` only | Byte-for-byte identical to Batch #32 behavior (Invariant B11). |
| `max_tokens` only | New behavior per §7; attempt-budget admission logic is simply never engaged (its check trivially passes since no attempt limit is set). |
| Both set | Attempt-budget admission evaluated first, then token-budget admission, at every canary boundary (Invariant B12). |
| Old `qualification.json` read by new code | Missing new fields treated as defaults (Invariant A1). |
| New `qualification.json` read by old code | Out of scope — no backward-reader compatibility for old code reading new artifacts is claimed or required. |

## 11. Test Plan

All items below must be pinned as explicit, named test cases (not merely covered incidentally):

1. **Provider total pinning, no double-counting:**
   - Codex: total reflects already-inclusive provider figure; cache/cache-write/reasoning subsets never added to it.
   - Claude: total is exactly `input + cache_read + cache_creation`; a test must assert that a nonzero `cache_creation_input_tokens` is reflected in the total exactly once.
   - Antigravity: total is exactly the reported figure; `cache_write_input_tokens` is pinned to `0`, not absent/`None`.
   - Cross-cutting: a test asserting `total_tokens == input_tokens + output_tokens` for representative fixtures from all three providers, with nonzero cache/reasoning subsets present, to catch any accidental re-addition.
2. **Claude cache-read + cache-creation normalization**, including the default-`0` compatibility path for `cache_creation_input_tokens` absence.
3. **Unknown usage is never treated as real zero:** a fixture where a provider fails to report usage must produce `observed=False`, and a budget-admission test must confirm the executor treats this as fail-closed (stops), not as "0 tokens consumed, continue."
4. **No partial canary:** a test that sets `max_tokens` below the cost of the very first canary and asserts the *entire* first canary (all its configured attempts) still completes, with a normal verdict, before the executor stops.
5. **Threshold below first-canary cost still lets it finish, then stops:** explicit assertion that the run stops immediately after the first canary (does not attempt a second) once cumulative usage is known to meet/exceed the threshold.
6. **Exact-reach and overshoot both stop before the next canary:** a case where cumulative usage lands exactly on `max_tokens`, and a separate case where it overshoots it, both must stop admission for the next canary identically.
7. **Skipped canary implies `INCOMPLETE`:** for both `max_attempts`-caused and `max_tokens`-caused skips, and for the "unknown usage" fail-closed skip.
8. **Invalid-attempt usage still counts if observed:** an attempt that fails/errors but has `observed=True` usage contributes to `observed_tokens` and to budget admission.
9. **`max_attempts`-only regression suite unchanged:** the full existing Batch #32 test suite for `max_attempts` alone must pass unmodified, proving Invariant B11.
10. **Combination ordering:** a test with both budgets active, arranged so that if token-check ran first it would produce a different stop point than attempt-check-first — asserting attempt-budget-first ordering (Invariant B12) is actually observed, not just documented.
11. **No-budget behavior unchanged except usage reporting:** a snapshot/characterization test on an unbudgeted run's ordering, completeness, and verdict, confirming only `observed_tokens`/`attempts_used` reporting is newly populated.
12. **CLI validation:** `--max-tokens 0` and `--max-tokens -5` both produce `CommandError`/exit code 3; `--max-tokens` help text includes the "checked between complete canaries" framing verbatim in spirit.
13. **Artifact metadata:** `qualification.json` round-trip includes the four new fields; a fixture representing an *old* artifact (missing the fields) still loads with correct defaults (Invariant A1).
14. **All three parser suites** updated/extended for the new `Usage` shape (Codex, Claude, Antigravity), including negative-value defense (Invariant U1) and no-leakage checks on summary metadata (Invariant E2).
15. **Backend / host-backend tests** confirming propagation from evidence to qualification `Usage` performs no additional arithmetic (parsers own normalization exclusively).
16. **Budget-specific executor test module** covering all state-machine branches in §7.5.
17. **Command / CLI / report / storage integration tests** covering the default low-tech line (§9.4, all three variants: plain, threshold-annotated, unavailable) and technical output (§9.5).

## 12. Review Gates (Required Before Roadmap Update)

All of the following must pass before this feature may be marked delivered in `ROADMAP.md`:

- Full test suite (all items in §11) green.
- Ruff clean.
- Strict mypy clean.
- `compileall` clean.
- Exact-head diff check (`git diff --check`) clean — no whitespace/conflict-marker artifacts.
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
- **Compatibility check:** every new field has a documented safe default (§8, §9.2 Invariant A1) and every "only when X" behavior (e.g., `max_attempts`-only) is pinned as its own regression-test obligation (§11 item 9) rather than assumed.
- **Honesty-of-framing check:** all user-facing language templates in this document (§7.2, §9.4) avoid "cap," "limit enforced," or "will not exceed" phrasing, consistent with Invariant B3.

No unresolved concerns remain for implementation to begin against this spec.
