# Token-Aware Qualification Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement QuaLock Batch #40 by normalizing trustworthy token usage from Codex, Claude, and Antigravity; propagating it into qualification attempts; enforcing an optional local `max_tokens` threshold only between complete canaries; persisting exact all-or-nothing accounting; and rendering honest check-only usage information without changing monitor behavior.


**Architecture:** Provider parsers translate native usage fields into `AgentEvidence`. Docker and Linux host backends copy those normalized values without arithmetic into canonical `Usage`. `QualificationExecutor` is the sole authority for `attempts_used`, `observed_tokens`, attempt-budget admission, token-threshold admission, critical-first execution, skipped-canary reasons, and restoration of configuration order. Commands and CLI validate and forward only supplied budgets. Storage and report rendering persist or display executor-produced accounting without recomputation.


**Tech Stack:** - Python 3.11+
- Frozen dataclasses and Pydantic configuration models
- Typer CLI and Rich terminal rendering
- pytest
- Ruff
- strict mypy
- `compileall`
- Git
- Canonical local tools: `/home/pacmap/qualock-easy/.venv/bin/python`, `/home/pacmap/qualock-easy/.venv/bin/ruff`, `/home/pacmap/qualock-easy/.venv/bin/mypy`


**Spec:** `docs/superpowers/specs/2026-09-07-token-aware-qualification-budget-design.md`

Base SHA for the complete Batch #40 diff:

`4138d21a58eabe0236c3e6d6d7309ca638148248`

## Global Constraints

- Work test-first. Every behavior change begins with a named failing test and a focused pytest run demonstrating the expected failure.
- Keep commits small and reviewable. Do not combine provider parsers, executor policy, rendering, and documentation in one commit.
- Do not change `agents/*`, `github_pr/*`, `release_monitor/*`, `version_bisect/*`, `scheduler/*`, `source/*`, `qualification/policy.py`, or `pyproject.toml` unless a directly necessary exception is independently reviewed and explicitly documented.
- Do not change config, baseline-lock, or canary schemas.
- Do not add monetary estimates, historical aggregation, saved budgets, estimated admission, request throttling, or mid-canary cancellation.
- Apply `max_tokens` only to local `qualock check`. Do not add it to baseline creation, monitor, bisect, GitHub PR qualification, or scheduler paths.
- Preserve `Usage.total_tokens == input_tokens + output_tokens`. Cache-read, cache-write, and reasoning counters are informational subsets and must never be added again.
- Keep `total_tokens` as a property. It must not become a dataclass field and must not be serialized by `asdict`.
- Never use counters from `observed=False` usage for totals, admission, or exact rendering.
- All new `Usage(...)` construction must use keyword arguments.
- Do not invent a Codex cache-write wire key. Codex `cache_write_input_tokens` remains exactly `0`.
- Preserve Claude’s golden normalization: `4 + 9035 + 9163 == 18202`.
- Preserve Antigravity’s strict terminal contract. `thinking_tokens` and `cache_read_tokens` remain required; provider `total_tokens` remains optional, validated only for type/non-negativity, and is never cross-checked against canonical totals.
- Use the deterministic token skip reasons exactly:
  - `INCOMPLETE: skipped because token usage was unavailable for one or more attempts (max_tokens=N)`
  - `INCOMPLETE: skipped by token budget (max_tokens=N, observed_tokens=M)`
- Preserve the existing attempt-budget reason exactly:
  - `INCOMPLETE: skipped by attempt budget (max_attempts=N, complete_canary_attempts=M)`
- Attempt-budget admission runs first. Its reason wins whenever both gates would stop the same canary.
- Once admitted, every paired/interleaved attempt in a canary runs. Neither budget may create a partial canary.
- Token admission is never evaluated before the first canary and is evaluated only at later canary boundaries.
- `observed_tokens` is an exact sum only when every started attempt has `usage.observed=True`; otherwise it is `None`. Zero started attempts produces `0`.
- Invalid attempts count toward `attempts_used`; their tokens count when observed.
- `QualificationExecutor` alone computes `attempts_used` and `observed_tokens`.
- Add no `qualification.json` reader and no historical-artifact loading test.
- Preserve `events_jsonl`; do not attach raw output, credentials, or transcript context to usage-summary metadata.
- Implement `render_usage_line` and optional `usage_line` exactly as specified. `check_command` opts in; monitor and shared renderer callers retain byte-for-byte output when `usage_line=None`.
- The CLI keyword mapping contains only budgets actually supplied. With neither budget, call exactly `execute_check(root, candidate)`.
- Do not update `README.md` or `ROADMAP.md` until implementation verification, Windows CI, and independent review have passed.
- Do not push or open a PR before Task 8 local implementation verification and independent review. Task 8 may perform routine push/PR/CI/rebase-merge integration. Never tag, release, or publish in Batch #40.

### Task 1: Add Canonical Usage, Evidence, and Result Models

**Files:**

- Create: `tests/unit/test_usage_models.py`
- Modify: `src/qualock/qualification/models.py`
- Modify: `src/qualock/evidence/models.py`
- Modify: `tests/unit/test_report.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_storage.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    observed: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
```

```python
@dataclass
class AgentEvidence:
    ...
    cache_write_input_tokens: int = 0
    usage_observed: bool = False
```

```python
@dataclass(frozen=True)
class QualificationResult:
    ...
    run_order: tuple[tuple[str, str, int], ...]
    max_attempts: int | None = None
    max_tokens: int | None = None
    attempts_used: int = 0
    observed_tokens: int | None = None
```

**Steps:**

- [ ] Create `tests/unit/test_usage_models.py` with a representative `Usage(input_tokens=100, cached_input_tokens=40, cache_write_input_tokens=15, output_tokens=30, reasoning_output_tokens=20, observed=True)`. Assert `total_tokens == 130`, proving none of the three subset fields is re-added.

- [ ] In the same test, assert `asdict(usage)` equals:

  ```python
  {
      "input_tokens": 100,
      "cached_input_tokens": 40,
      "cache_write_input_tokens": 15,
      "output_tokens": 30,
      "reasoning_output_tokens": 20,
      "observed": True,
  }
  ```

  and assert `"total_tokens" not in asdict(usage)`.

- [ ] Add tests asserting `Usage().observed is False`, `AttemptResult(...).usage.observed is False`, `AgentEvidence().usage_observed is False`, and the four `QualificationResult` defaults are `None`, `None`, `0`, and `None`.

- [ ] Update existing report, command, and storage test factories to construct observed usage explicitly where their later assertions treat token counts as trustworthy:

  ```python
  Usage(input_tokens=10, output_tokens=2, observed=True)
  ```

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_usage_models.py tests/unit/test_report.py tests/unit/test_commands.py tests/unit/test_storage.py
  ```

  Expected failure: `Usage` rejects `cache_write_input_tokens` and `observed`, has no `total_tokens`, `AgentEvidence` has no new fields, and `QualificationResult` rejects the four accounting fields.

- [ ] Implement the interfaces above in `qualification/models.py` and `evidence/models.py`. Keep `total_tokens` a derived property and append result fields after existing required fields so existing constructors remain valid.

- [ ] Run the same pytest command. Expected result: all selected tests pass.

- [ ] Run broader model and serialization coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_usage_models.py tests/unit/test_report.py tests/unit/test_storage.py tests/unit/test_commands.py tests/unit/test_baseline_lock.py
  ```

  Expected result: pass.

- [ ] Request an independent review of field defaults, dataclass compatibility, keyword construction, derived-total serialization, and the no-double-counting invariant. Resolve every finding and rerun the focused tests.

- [ ] Commit:

  ```bash
  git add src/qualock/qualification/models.py src/qualock/evidence/models.py tests/unit/test_usage_models.py tests/unit/test_report.py tests/unit/test_commands.py tests/unit/test_storage.py
  git commit -m "feat: add canonical qualification usage accounting"
  ```

### Task 2: Normalize Codex Usage Permissively

**Files:**

- Modify: `src/qualock/evidence/codex_jsonl.py`
- Modify: `tests/unit/test_codex_jsonl.py`

**Interfaces:**

```python
def parse_codex_jsonl(lines: Iterable[str]) -> AgentEvidence:
    ...
```

```python
def _trusted_required_total(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )
```

**Steps:**

- [ ] Extend `test_codex_parser_returns_normalized_agent_evidence` to assert the existing missing-output fixture retains `input_tokens == 2` but has `usage_observed is False`.

- [ ] Add `test_codex_usage_accumulates_trustworthy_completed_turns` using two `turn.completed` events. Assert totals and optional subsets accumulate, `cache_write_input_tokens == 0`, and `usage_observed is True`.

- [ ] Add parameterized tests for a missing usage object, non-object usage, missing `input_tokens`, missing `output_tokens`, boolean totals, string totals, and negative totals. Assert parsing does not raise, the compatible `_int_value` counters remain available where applicable, and whole-attempt `usage_observed is False`.

- [ ] Add parameterized tests for malformed `cached_input_tokens` and `reasoning_output_tokens` values, including boolean, string, and negative values. With trustworthy required totals, assert each malformed optional value contributes `0` and does not clear `usage_observed`.

- [ ] Add a test with nonzero cache and reasoning detail asserting canonical construction gives:

  ```python
  usage = Usage(
      input_tokens=evidence.input_tokens,
      cached_input_tokens=evidence.cached_input_tokens,
      cache_write_input_tokens=evidence.cache_write_input_tokens,
      output_tokens=evidence.output_tokens,
      reasoning_output_tokens=evidence.reasoning_output_tokens,
      observed=evidence.usage_observed,
  )
  assert usage.total_tokens == evidence.input_tokens + evidence.output_tokens
  ```

- [ ] Add a leakage regression assertion that no new `AgentEvidence` field contains the raw JSONL line, stdout, stderr, token-bearing transcript, or credentials.

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_codex_jsonl.py
  ```

  Expected failure: `usage_observed` never becomes true, malformed required totals are not tracked across the whole attempt, malformed negative optional details survive `_int_value`, and cache-write is not represented.

- [ ] Implement explicit parser state: count `turn.completed` events, maintain a `usage_trustworthy` boolean, and set `evidence.usage_observed = saw_turn_completed and usage_trustworthy` after parsing. A missing/non-object usage or any untrustworthy required total permanently clears trust for the attempt.

- [ ] Retain `_int_value` for compatible evidence accumulation, but add a separate optional-detail helper that accepts only non-negative non-boolean integers. Do not read any cache-write key; leave the model default `0`.

- [ ] Run `/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_codex_jsonl.py`. Expected result: pass.

- [ ] Run broader Codex coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_codex_jsonl.py tests/unit/test_codex_adapter.py
  ```

  Expected result: pass.

- [ ] Request independent review specifically for permissive failure behavior, whole-attempt trust invalidation, raw-counter compatibility, and confirmation that no cache-write key was invented.

- [ ] Commit:

  ```bash
  git add src/qualock/evidence/codex_jsonl.py tests/unit/test_codex_jsonl.py
  git commit -m "feat: normalize observed Codex token usage"
  ```

### Task 3: Normalize Claude Usage and Re-Pin the Golden Transcript

**Files:**

- Modify: `src/qualock/evidence/claude_stream_json.py`
- Modify: `tests/unit/test_claude_stream_json.py`
- Modify: `tests/unit/test_claude_real_contract.py`

**Interfaces:**

```python
def _required_usage_value(usage: dict[str, Any], key: str) -> int:
    """Return a non-negative, non-boolean integer or raise ClaudeEvidenceError."""
```

```python
def _optional_usage_value(
    usage: dict[str, Any],
    key: str,
    *,
    default: int = 0,
) -> int:
    """Return default only when absent; reject invalid present values."""
```

```python
def _optional_thinking_tokens(usage: dict[str, Any]) -> int:
    """Validate output_tokens_details and its optional thinking_tokens."""
```

**Steps:**

- [ ] Update the normal result fixture to include `cache_creation_input_tokens` and `output_tokens_details.thinking_tokens`. Assert:

  ```python
  assert evidence.input_tokens == (
      raw_input + raw_cache_read + raw_cache_creation
  )
  assert evidence.cached_input_tokens == raw_cache_read
  assert evidence.cache_write_input_tokens == raw_cache_creation
  assert evidence.output_tokens == raw_output
  assert evidence.reasoning_output_tokens == raw_thinking
  assert evidence.usage_observed is True
  ```

- [ ] Add a compatibility test omitting `cache_creation_input_tokens` and `output_tokens_details`. Assert cache-write and reasoning detail are `0`, normalized input is `input_tokens + cache_read_input_tokens`, and usage remains observed.

- [ ] Extend `test_result_usage_requires_integer_fields` with boolean and negative cases for required `input_tokens`, `cache_read_input_tokens`, and `output_tokens`. Assert `ClaudeEvidenceError` names the invalid key and requires a non-negative integer.

- [ ] Add parameterized tests proving a present `cache_creation_input_tokens` rejects string, float, boolean, and negative values while absence alone defaults to `0`.

- [ ] Add parameterized reasoning-detail tests proving a present non-object `output_tokens_details`, or a present string, boolean, float, or negative `thinking_tokens`, raises `ClaudeEvidenceError`.

- [ ] Re-pin the locked `2.1.260` transcript in `test_claude_real_contract.py`:

  ```python
  assert evidence.input_tokens == 18202
  assert evidence.cached_input_tokens == 9035
  assert evidence.cache_write_input_tokens == 9163
  assert evidence.output_tokens == 74
  assert evidence.usage_observed is True
  ```

- [ ] Add an explicit no-double-count test asserting the golden canonical total is `18202 + 74`, not a sum that adds cache fields again.

- [ ] Add a leakage regression assertion that normalization adds no raw transcript or credential-bearing summary field.

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_claude_stream_json.py tests/unit/test_claude_real_contract.py
  ```

  Expected failure: input remains raw `4` instead of `18202`, cache creation and thinking are absent, usage is unobserved, and negative values are accepted.

- [ ] Implement strict non-negative validation. Compute normalized input exactly once:

  ```python
  raw_input = _required_usage_value(usage, "input_tokens")
  cache_read = _required_usage_value(usage, "cache_read_input_tokens")
  cache_creation = _optional_usage_value(
      usage, "cache_creation_input_tokens"
  )
  evidence.input_tokens = raw_input + cache_read + cache_creation
  evidence.cached_input_tokens = cache_read
  evidence.cache_write_input_tokens = cache_creation
  evidence.output_tokens = _required_usage_value(usage, "output_tokens")
  evidence.reasoning_output_tokens = _optional_thinking_tokens(usage)
  evidence.usage_observed = True
  ```

- [ ] Run the focused pytest command. Expected result: pass.

- [ ] Run broader Claude coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_claude_stream_json.py tests/unit/test_claude_real_contract.py tests/unit/test_claude_adapter.py
  ```

  Expected result: pass.

- [ ] Request independent review of strict validation, optional-only-when-absent behavior, the exact `18202` normalization, and no-double-counting.

- [ ] Commit:

  ```bash
  git add src/qualock/evidence/claude_stream_json.py tests/unit/test_claude_stream_json.py tests/unit/test_claude_real_contract.py
  git commit -m "feat: normalize Claude cache and thinking usage"
  ```

### Task 4: Extend Antigravity Usage Without Relaxing Its Contract

**Files:**

- Modify: `src/qualock/evidence/antigravity_stream_json.py`
- Modify: `tests/unit/test_antigravity_stream_json.py`

**Interfaces:**

```python
def _record_result(
    evidence: AgentEvidence,
    event: dict[str, Any],
) -> None:
    ...
```

**Steps:**

- [ ] Extend the successful terminal-result test to assert:

  ```python
  assert evidence.input_tokens == 10
  assert evidence.cached_input_tokens == 3
  assert evidence.cache_write_input_tokens == 0
  assert evidence.output_tokens == 2
  assert evidence.reasoning_output_tokens == 1
  assert evidence.usage_observed is True
  ```

- [ ] Add a canonical no-double-count test with a deliberately unrelated provider `total_tokens` value. Assert `Usage(...).total_tokens == 12`, based only on input `10` and output `2`.

- [ ] Add tests proving omitted provider `total_tokens` succeeds, while present string, boolean, or negative provider totals retain the existing validation error.

- [ ] Preserve and strengthen named tests showing missing `thinking_tokens` and missing `cache_read_tokens` raise `AntigravityEvidenceError`.

- [ ] Add an explicit test proving no new equality check rejects a non-negative provider `total_tokens` that differs from `input_tokens + output_tokens`.

- [ ] Add a leakage regression assertion that no raw terminal event, stdout/stderr, or credentials are copied into usage-summary fields.

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_antigravity_stream_json.py
  ```

  Expected failure: cache-write and observed fields are not populated.

- [ ] Set `cache_write_input_tokens = 0` and `usage_observed = True` only after all existing terminal validation succeeds. Make no other validation or arithmetic changes.

- [ ] Run the focused pytest command. Expected result: pass.

- [ ] Run broader Antigravity coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_antigravity_stream_json.py tests/unit/test_antigravity_adapter.py tests/unit/test_antigravity_resolver.py
  ```

  Expected result: pass.

- [ ] Request independent review confirming strictness is unchanged, required fields remain required, provider `total_tokens` is not used or cross-checked, and cache-write is a known zero.

- [ ] Commit:

  ```bash
  git add src/qualock/evidence/antigravity_stream_json.py tests/unit/test_antigravity_stream_json.py
  git commit -m "feat: expose observed Antigravity token usage"
  ```

### Task 5: Propagate Usage Through Docker and Host Backends

**Files:**

- Modify: `src/qualock/run/backend.py`
- Modify: `src/qualock/run/host_backend.py`
- Modify: `tests/unit/test_docker_backend.py`
- Modify: `tests/unit/test_host_backend.py`

**Interfaces:**

Use the same keyword-only value mapping in both backends:

```python
def _usage_from_evidence(evidence: AgentEvidence) -> Usage:
    return Usage(
        input_tokens=evidence.input_tokens,
        cached_input_tokens=evidence.cached_input_tokens,
        cache_write_input_tokens=evidence.cache_write_input_tokens,
        output_tokens=evidence.output_tokens,
        reasoning_output_tokens=evidence.reasoning_output_tokens,
        observed=evidence.usage_observed,
    )
```

The helper may remain private to each module; do not create a new shared abstraction unless independent review demonstrates a concrete need.

**Steps:**

- [ ] Update Docker and host fake adapters to return evidence with distinct values for every field:

  ```python
  AgentEvidence(
      input_tokens=20,
      cached_input_tokens=7,
      cache_write_input_tokens=5,
      output_tokens=9,
      reasoning_output_tokens=3,
      usage_observed=True,
  )
  ```

- [ ] In each backend suite, assert successful and policy-invalid attempts preserve every value exactly and produce `usage.total_tokens == 29`.

- [ ] Add a regression test in each backend suite proving invalid paths that fail before trustworthy parsing retain the default `Usage(observed=False)`.

- [ ] Cover evidence-parser errors, integrity errors, and timeout/no-terminal evidence paths already represented by backend fixtures. Assert none fabricates observed zero.

- [ ] Assert `events_jsonl` behavior remains unchanged and usage propagation adds no raw stdout/stderr or credential metadata.

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_docker_backend.py tests/unit/test_host_backend.py
  ```

  Expected failure: cache-write and observed values are dropped.

- [ ] Implement exact field copying in every `Usage(...)` construction for valid attempts and invalid attempts created after successful evidence parsing. Leave early `_invalid_attempt` paths on default unobserved usage.

- [ ] Do not sum Claude components, recompute totals, clamp values, or infer observation in either backend.

- [ ] Run the focused pytest command. Expected result: pass.

- [ ] Run broader runtime coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_docker_backend.py tests/unit/test_host_backend.py tests/unit/test_host_runner.py tests/unit/test_process.py tests/unit/test_integrity.py
  ```

  Expected result: pass.

- [ ] Request independent review comparing both backends field-for-field and verifying parser ownership of normalization.

- [ ] Commit:

  ```bash
  git add src/qualock/run/backend.py src/qualock/run/host_backend.py tests/unit/test_docker_backend.py tests/unit/test_host_backend.py
  git commit -m "feat: propagate normalized usage through run backends"
  ```

### Task 6: Implement Executor Token Accounting and Admission State Machine

**Files:**

- Create: `tests/unit/test_token_budgeted_qualification.py`
- Modify: `src/qualock/run/executor.py`
- Modify: `tests/unit/test_budgeted_qualification.py`

**Interfaces:**

```python
def QualificationExecutor.run(
    self,
    baseline_binary: AgentBinary,
    candidate_binary: AgentBinary,
    suite: Sequence[CanarySpec],
    *,
    qualification_id: str,
    max_attempts: int | None = None,
    max_tokens: int | None = None,
) -> QualificationResult:
    ...
```

```python
def _token_budget_skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    max_tokens: int,
    observed_tokens: int | None,
) -> tuple[CanaryComparison, CanaryExecution]:
    ...
```

Accounting logic after every started attempt:

```python
attempts_used += 1
if observed_tokens is not None:
    if attempt.usage.observed:
        observed_tokens += attempt.usage.total_tokens
    else:
        observed_tokens = None
```

**Steps:**

- [ ] Build a recording backend in `test_token_budgeted_qualification.py` that can return per-canary/per-side/per-repetition `AttemptResult` objects with configurable token totals, validity, success, and observation.

- [ ] Add `test_max_tokens_below_first_canary_cost_finishes_whole_first_canary_then_stops`. Use two canaries, repetitions that produce multiple paired attempts, and `max_tokens` below the first complete canary’s cost. Assert every first-canary attempt runs, the normal first-canary verdict is computed, the second canary has no attempts, and the suite is `INCOMPLETE`.

- [ ] Add separate exact-reach and overshoot tests. Assert both skip the next canary with:

  ```python
  f"INCOMPLETE: skipped by token budget "
  f"(max_tokens={max_tokens}, observed_tokens={observed_tokens})"
  ```

- [ ] Add `test_unknown_usage_after_first_canary_stops_every_later_canary`. Include an invalid attempt using default `Usage()`. Assert every later canary uses:

  ```python
  f"INCOMPLETE: skipped because token usage was unavailable "
  f"for one or more attempts (max_tokens={max_tokens})"
  ```

  and final `observed_tokens is None`.

- [ ] Add `test_invalid_observed_attempt_usage_counts`. Return `valid=False` with `Usage(input_tokens=8, output_tokens=2, observed=True)`. Assert it increments `attempts_used`, contributes `10`, and can stop the next canary.

- [ ] Add zero-attempt accounting coverage using an attempt budget too small for one canary. Assert `attempts_used == 0` and `observed_tokens == 0`.

- [ ] Add no-budget characterization coverage. Assert configuration execution order, `run_order`, verdict, and completeness are unchanged while `attempts_used` and `observed_tokens` are populated.

- [ ] Add token-only ordering coverage. Assert `max_tokens` activates stable critical-first execution even when the threshold is large enough to run the suite, while returned executions remain in original configuration order.

- [ ] Preserve and extend existing attempt-only tests. Assert constraining and unconstraining `max_attempts` retain Batch #32 admission, execution order, `run_order`, verdict, and completeness. Assert `max_tokens=None` does not alter these decisions.

- [ ] Add a combination-precedence test where both gates independently stop the same later canary. Assert the attempt-budget reason is used and no token reason is attached.

- [ ] Add a test proving `max_tokens` alone never arms attempt-budget skipping.

- [ ] Add parameterized direct-call validation for `max_tokens=0` and `-1`, expecting:

  ```python
  ValueError("max_tokens must be greater than zero")
  ```

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_token_budgeted_qualification.py tests/unit/test_budgeted_qualification.py
  ```

  Expected failure: `run()` rejects `max_tokens`, result accounting stays at defaults, and token skip behavior does not exist.

- [ ] Implement separate booleans:

  ```python
  attempt_constrained = (
      max_attempts is not None
      and max_attempts < full_suite_attempts
  )
  prioritize_critical = attempt_constrained or max_tokens is not None
  ```

- [ ] Before every canary, evaluate the existing attempt gate first. Only if it admits the canary and at least one prior canary completed, evaluate the token gate.

- [ ] When either gate skips a canary, continue constructing skipped executions for every remaining configured canary. Never prepare or start a skipped canary.

- [ ] Initialize `attempts_used = 0` and `observed_tokens: int | None = 0`. Update both immediately after each `run_attempt` returns, regardless of validity or success.

- [ ] Return the four accounting fields from `QualificationResult`, including the echo of both budgets.

- [ ] Preserve dictionaries keyed by original suite index and reconstruct comparisons/executions in configuration order before qualification.

- [ ] Run the focused pytest command. Expected result: pass.

- [ ] Run broader executor and policy coverage:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_token_budgeted_qualification.py tests/unit/test_budgeted_qualification.py tests/unit/test_policy.py tests/unit/test_schedule.py
  ```

  Expected result: pass.

- [ ] Request independent review against every state-machine branch: first-canary exception, exact reach, overshoot, unknown usage, invalid observed usage, attempt-first precedence, atomic canaries, stable priority, original result order, and attempt-only compatibility.

- [ ] Commit:

  ```bash
  git add src/qualock/run/executor.py tests/unit/test_token_budgeted_qualification.py tests/unit/test_budgeted_qualification.py
  git commit -m "feat: enforce token thresholds between complete canaries"
  ```

### Task 7: Wire Commands, CLI, Artifacts, and Check-Only Rendering

**Files:**

- Modify: `src/qualock/commands.py`
- Modify: `src/qualock/cli.py`
- Modify: `src/qualock/evidence/storage.py`
- Modify: `src/qualock/report/render.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_storage.py`
- Modify: `tests/unit/test_report.py`

**Interfaces:**

```python
def execute_check(
    root: Path,
    candidate_spec: str,
    *,
    resolver: Resolver | None = None,
    backend: QualificationBackend | None = None,
    qualification_id: str | None = None,
    max_attempts: int | None = None,
    max_tokens: int | None = None,
) -> QualificationResult:
    ...
```

```python
def render_usage_line(result: QualificationResult) -> str:
    ...
```

```python
def render_safety_terminal(
    summary: SafetySummary,
    evidence_path: str,
    *,
    usage_line: str | None = None,
) -> str:
    ...
```

```python
def _render_safety_result(
    root: Path,
    result: QualificationResult,
    display_name: str,
    *,
    include_usage: bool = False,
) -> None:
    ...
```

CLI budget forwarding:

```python
budgets: dict[str, int] = {}
if max_attempts is not None:
    budgets["max_attempts"] = max_attempts
if max_tokens is not None:
    budgets["max_tokens"] = max_tokens
result = execute_check(root, candidate, **budgets)
```

**Steps:**

- [ ] Add direct command tests for `execute_check(..., max_tokens=0)` and `-5`, expecting `CommandError("max tokens must be greater than zero")` before backend execution.

- [ ] Add a command forwarding test asserting `execute_check` passes both `max_attempts` and `max_tokens` to `QualificationExecutor.run`.

- [ ] Extend `test_unstable_baseline_persists_attempt_evidence` to assert `baseline.json` serializes the expanded `Usage` fields (`cache_write_input_tokens` and `observed`) through existing `asdict` handling, without adding any baseline token-budget option.

- [ ] Update the exact `qualification.json` key-set assertion to include:

  ```python
  {
      "qualification_id",
      "baseline_version",
      "candidate_version",
      "run_order",
      "verdict",
      "max_attempts",
      "max_tokens",
      "attempts_used",
      "observed_tokens",
  }
  ```

  Assert exact values, including `attempts_used=0` and `observed_tokens=0` for the no-start case.

- [ ] Add no reader, loader, migration, or old-artifact loading test for `qualification.json`.

- [ ] Add `render_usage_line` tests for all four exact outputs:

  ```text
  Observed model tokens: 57,231
  Observed model tokens: 57,231 (threshold 50,000; checked between complete canaries)
  Observed model tokens: unavailable
  Observed model tokens: unavailable (threshold 50,000; checked between complete canaries)
  ```

- [ ] Add a standalone safety-renderer regression asserting `render_safety_terminal(summary, path)` and `render_safety_terminal(summary, path, usage_line=None)` equal the pre-Batch #40 exact output.

- [ ] Add a safety-renderer test asserting a supplied usage line appears immediately before `Technical evidence:` and appears exactly once.

- [ ] Re-baseline `test_check_easy_output_is_exactly_preserved` with the new check-only usage line. Add or retain an exact monitor-output test proving monitor remains unchanged.

- [ ] Add CLI forwarding tests for all four mappings:
  - no budgets: exact two-argument `execute_check(root, candidate)`;
  - only `--max-attempts`: only `max_attempts`;
  - only `--max-tokens`: only `max_tokens`;
  - both flags: both keywords.

- [ ] Add CLI validation tests for `--max-tokens 0` and `--max-tokens -5`. Assert exit code `3`, the exact command error, and no executor call.

- [ ] Add a CLI help test asserting `--max-tokens` help describes a threshold checked between complete canaries and does not call it a hard cap or billing limit.

- [ ] Extend technical terminal and Markdown tests to assert attempts used, observed tokens or `unavailable`, active `max_attempts`, and token-threshold framing. Do not label `max_attempts` as a token threshold.

- [ ] Assert default low-tech output contains no cache-read, cache-write, or reasoning breakdown.

- [ ] Run:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_storage.py tests/unit/test_report.py
  ```

  Expected failure: `execute_check` and CLI reject `max_tokens`, accounting is missing from artifacts, rendering interfaces do not exist, and exact check output lacks the usage line.

- [ ] Validate `max_tokens` in `execute_check`, pass it to `QualificationExecutor.run`, and leave `execute_baseline` unchanged.

- [ ] Add the Typer option with help that explicitly says it is a threshold checked between complete canaries, not a hard cap or billing limit.

- [ ] Construct the CLI keyword mapping exactly from supplied values. Do not always pass `None`.

- [ ] Persist the executor-produced four accounting values as top-level `qualification.json` keys. Let existing `asdict` handling expand `report.json` and baseline/report usage shapes.

- [ ] Implement `render_usage_line` using thousands separators and `result.observed_tokens` without recomputation.

- [ ] Extend technical terminal and Markdown output with executor-produced accounting. Preserve existing evidence content and `events_jsonl`.

- [ ] Extend `render_safety_terminal` with the keyword-only optional line. Insert it immediately before `Technical evidence:` only when non-`None`.

- [ ] Have `_render_safety_result(..., include_usage=True)` pass `render_usage_line(result)`. Call it with `include_usage=True` only from `check_command`; leave `monitor_command` and shared callers on the default.

- [ ] Run the focused pytest command. Expected result: pass.

- [ ] Run the complete focused Batch #40 suite:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
    tests/unit/test_usage_models.py \
    tests/unit/test_codex_jsonl.py \
    tests/unit/test_codex_adapter.py \
    tests/unit/test_claude_stream_json.py \
    tests/unit/test_claude_real_contract.py \
    tests/unit/test_claude_adapter.py \
    tests/unit/test_antigravity_stream_json.py \
    tests/unit/test_antigravity_adapter.py \
    tests/unit/test_docker_backend.py \
    tests/unit/test_host_backend.py \
    tests/unit/test_token_budgeted_qualification.py \
    tests/unit/test_budgeted_qualification.py \
    tests/unit/test_commands.py \
    tests/unit/test_cli.py \
    tests/unit/test_storage.py \
    tests/unit/test_report.py
  ```

  Expected result: pass.

- [ ] Request independent review of command validation, supplied-keyword-only forwarding, absence of a qualification reader, artifact exactness, all four low-tech strings, technical framing, check-only opt-in, and unchanged monitor/shared rendering.

- [ ] Commit:

  ```bash
  git add src/qualock/commands.py src/qualock/cli.py src/qualock/evidence/storage.py src/qualock/report/render.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_storage.py tests/unit/test_report.py
  git commit -m "feat: expose token budgets and usage in local checks"
  ```

### Task 8: Verify, Integrate, Document, and Close Batch #40

**Files:**

- Modify only after implementation-head CI and independent review pass: `README.md`
- Modify only after implementation-head CI and independent review pass: `ROADMAP.md`

**Interfaces:**

README must state that `--max-tokens` is a threshold checked between complete canaries, is not a hard cap/billing limit, may overshoot by the last complete canary, fails closed when usage is unknown, composes with `--max-attempts`, and is local-`check` only.

ROADMAP must mark provider-neutral token-denominated local qualification budgeting delivered while retaining #41 historical canary effectiveness/runtime-token estimates and #42 provider-specific monetary estimates outside qualification policy as pending.

**Steps:**

- [ ] Confirm all Task 1–7 commits are present and the worktree has no uncommitted implementation changes except `.superpowers/` scratch:

  ```bash
  git log --oneline 4138d21a58eabe0236c3e6d6d7309ca638148248..HEAD
  git status --short
  ```

- [ ] Run the complete focused Batch #40 suite using the canonical interpreter:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
    tests/unit/test_usage_models.py \
    tests/unit/test_codex_jsonl.py tests/unit/test_codex_adapter.py \
    tests/unit/test_claude_stream_json.py tests/unit/test_claude_real_contract.py tests/unit/test_claude_adapter.py \
    tests/unit/test_antigravity_stream_json.py tests/unit/test_antigravity_adapter.py \
    tests/unit/test_docker_backend.py tests/unit/test_host_backend.py \
    tests/unit/test_token_budgeted_qualification.py tests/unit/test_budgeted_qualification.py \
    tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_storage.py tests/unit/test_report.py
  ```

  Expected result: all selected tests pass with no new skip introduced by Batch #40.

- [ ] Run the full suite:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
  ```

  Expected result: pass; inspect and explain every skip rather than accepting a new unexplained skip.

- [ ] Run Ruff and compileall:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/ruff check .
  /home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
  ```

  Expected result: both commands exit `0`.

- [ ] Run strict mypy against the exact implementation head:

  ```bash
  /home/pacmap/qualock-easy/.venv/bin/mypy --strict src
  ```

  Expected result: the only errors are the three pre-existing missing-PyYAML-stub `import-untyped` errors in `src/qualock/config/io.py`, `src/qualock/canary/loader.py`, and `src/qualock/project_setup/config.py`. Do not install `types-PyYAML`; any additional error is blocking.

- [ ] Run base-relative diff and protected-scope gates:

  ```bash
  git diff --check 4138d21a58eabe0236c3e6d6d7309ca638148248..HEAD
  git diff --name-only 4138d21a58eabe0236c3e6d6d7309ca638148248..HEAD -- \
    src/qualock/agents src/qualock/github_pr src/qualock/release_monitor \
    src/qualock/version_bisect src/qualock/scheduler src/qualock/source \
    src/qualock/qualification/policy.py pyproject.toml
  ```

  Expected result: `git diff --check` has no output; protected-scope command has no output.

- [ ] Obtain independent implementation review for the complete implementation diff through Task 7. Any Critical/Important finding blocks integration; fix findings in separate commits and repeat all local gates.

- [ ] Push `feat/token-budget` and open a non-draft PR against `main` only after the implementation review is clean. Do not tag, release, or publish.

- [ ] Require PR CI, including the Windows job, to be green on that implementation head. If CI finds a defect, fix it with TDD, rerun focused/full local gates, obtain scoped review for the fix, push, and require CI green again.

- [ ] Only after implementation-head local gates, independent review, and Windows CI are green, update `README.md` with the exact user-facing semantics above. Do not claim token pricing, a hard cap, monitor/bisect/PR budgeting, historical prediction, or automatic updates.

- [ ] Update `ROADMAP.md` only at the same post-CI stage: mark provider-neutral token-denominated local qualification budgeting delivered; keep #41 and #42 pending and preserve #42 as advisory/outside qualification policy.

- [ ] Commit documentation only:

  ```bash
  git add README.md ROADMAP.md
  git commit -m "docs: document token-aware qualification budgets"
  ```

- [ ] Rerun the complete focused suite, full suite, Ruff, compileall, strict-mypy baseline check, `git diff --check 4138d21a58eabe0236c3e6d6d7309ca638148248..HEAD`, and protected-scope proof on the documentation-inclusive head.

- [ ] Push the documentation commit to the existing PR and require every PR CI job, including Windows, to be green on the new exact head.

- [ ] Obtain final independent review of the complete base-to-HEAD diff, including README and ROADMAP. Any fix invalidates the current exact-head evidence; after a fix, repeat local gates, push, CI, and review.

- [ ] Record the final exact head and verify branch/PR head identity:

  ```bash
  git rev-parse HEAD
  ```

  The recorded SHA must equal the PR head SHA that has green CI and the SHA reviewed in the final independent review.

- [ ] Rebase-merge the PR only after exact-head local gates, final review, and all CI are green. After merge, verify the PR is merged and record the resulting `main` SHA. Do not create a tag, release, package publication, or other release artifact.
