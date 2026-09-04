# Budgeted Qualification Design

## Status

Approved for local Batch #32 design work on 2026-09-04.

Base: `origin/main` at `e60b5d113e0a56fc88e850c6d54234d6cd96c3b3`.

## Problem

A normal QuaLock check runs every configured canary for both the pinned baseline and the candidate, for every configured repetition. With three canaries and three repetitions, that is 18 model attempts. This is intentionally conservative, but it can be expensive or slow when a user only wants an early, explicitly non-final signal.

QuaLock already records normalized token usage, but model-specific pricing and cache semantics differ across agents. A dollar-denominated budget would therefore create fragile pricing policy inside the local qualification core.

Batch #32 adds a deterministic attempt-count budget instead. It saves model calls while preserving QuaLock's fail-closed verdict semantics.

## Goals

1. Add an opt-in local CLI budget: `qualock check AGENT@VERSION --max-attempts N`.
2. Never execute more than `N` baseline/candidate model attempts.
3. Never run a partial canary. A selected canary receives its complete paired/interleaved schedule.
4. When a budget constrains the suite, run critical canaries before non-critical canaries while preserving original order within each priority class.
5. Represent every unexecuted canary as `INCOMPLETE` evidence so a budget-limited suite can never appear complete.
6. Preserve current behavior byte-for-byte when no budget is supplied.
7. Also preserve current behavior when `--max-attempts` is large enough to cover the full suite.
8. Keep release monitor, version bisect, scheduler, and GitHub PR qualification behavior unchanged and unlimited.

## Non-goals

- Dollar-cost estimation or provider pricing tables.
- Token-count budgets.
- Adaptive stopping in the middle of a canary.
- Changing baseline creation semantics.
- Changing qualification policy precedence.
- Converting a budget-limited result into PASS, WARN, or BLOCK when evidence is missing.
- Adding persistent budget settings to `.qualock/config.yaml` in this batch.
- Applying budgets to release monitor, version bisect, scheduler, or GitHub PR workflows.
- Changing canary fingerprints or baseline-lock format.

## User interface

The new optional flag is local-check only:

```text
qualock check codex@0.153.0 --max-attempts 6
qualock check claude@2.1.261 --max-attempts 12
```

`N` must be an integer greater than zero. Invalid values are CLI/configuration errors and use the existing user-error exit path.

Without the flag, QuaLock behaves exactly as it does today.

## Attempt accounting

One model invocation through `QualificationBackend.run_attempt()` consumes one attempt.

For one canary:

```text
attempts_per_canary = repetitions * 2
```

The factor of two is baseline plus candidate. Docker preparation, resolver work, grader execution, artifact rendering, and other local operations do not consume attempt budget.

A canary is eligible to start only when the remaining budget can cover its entire `attempts_per_canary` schedule. QuaLock never starts a canary that it cannot finish within the declared attempt budget.

This makes the cap deterministic before execution and preserves paired/interleaved evidence.

## Constrained versus unconstrained execution

Let:

```text
full_suite_attempts = len(canaries) * repetitions * 2
```

The executor is **unconstrained** when either:

- no attempt budget was supplied; or
- `max_attempts >= full_suite_attempts`.

In the unconstrained case, the executor uses the existing suite order and existing schedule without reordering anything. This is a compatibility invariant.

The executor is **constrained** only when `max_attempts < full_suite_attempts`.

In the constrained case, the execution priority is:

1. critical canaries, in their original suite order;
2. non-critical canaries, in their original suite order.

The priority changes execution order only. Final `QualificationResult.executions` remains in the original suite order so human-facing reports stay stable and easy to compare.

`QualificationResult.run_order` continues to record the actual model-attempt order and therefore exposes the critical-first execution order when a budget is constraining.

## Skipped-canary semantics

If there is not enough remaining budget to run a full canary, QuaLock must not call `prepare()` or `run_attempt()` for that canary.

It still creates a `CanaryExecution` for the canary with:

- `attempts=()`;
- baseline successes/valid counts of zero;
- candidate successes/valid counts of zero;
- verdict `INCOMPLETE`;
- a deterministic reason stating that the canary was skipped because the attempt budget could not fund its full paired schedule.

The corresponding comparison is also `INCOMPLETE` and `baseline_stable=False`.

The reason must include enough provenance to explain the decision, including the configured maximum attempt count and the attempts required for one complete canary.

No new field is added to `QualificationResult`, `CanaryExecution`, or `AttemptResult` in this batch. This avoids changing unlimited JSON/report schemas merely to carry budget metadata.

## Suite verdict invariant

Current suite precedence is:

```text
INCOMPLETE > BLOCK > WARN > PASS
```

Batch #32 does not change it.

Because every skipped canary is explicitly represented as `INCOMPLETE`, any check that omits one or more configured canaries because of budget must have final verdict `INCOMPLETE`.

This remains true even if an executed critical canary produces a result that would otherwise be `BLOCK`. QuaLock may show the critical regression evidence, but it must not claim a complete suite decision while configured evidence is missing.

## Small-budget behavior

A budget may be smaller than `attempts_per_canary`.

Example with three repetitions:

```text
--max-attempts 5
attempts_per_canary = 6
```

In that case no canary starts, all configured canaries are represented as skipped `INCOMPLETE`, `run_order` is empty, and the final verdict is `INCOMPLETE`.

This is a valid budget-limited qualification result, not an exception.

## Example

Given three canaries in config order:

```text
formatting     non-critical
safe-refactor  critical
repo-edit      non-critical
```

with three repetitions, the full suite requires 18 attempts.

For:

```text
--max-attempts 6
```

execution priority becomes:

```text
safe-refactor
```

Only `safe-refactor` receives its six paired attempts. `formatting` and `repo-edit` are emitted as skipped `INCOMPLETE` executions. The final report table remains in config order:

```text
formatting      INCOMPLETE
safe-refactor   <observed canary verdict>
repo-edit       INCOMPLETE
```

The suite verdict is `INCOMPLETE`.

## Architecture

### `QualificationExecutor`

`QualificationExecutor.run()` receives an optional attempt budget for a single qualification run. The executor owns budget planning because it already owns schedule construction and backend invocation.

The executor will:

1. compute `attempts_per_canary` and full-suite cost;
2. select normal order or constrained critical-first order;
3. execute only whole canaries that fit the remaining budget;
4. create explicit skipped executions/comparisons for every canary that does not fit;
5. restore original suite order for returned executions/comparisons;
6. pass all comparisons through the unchanged suite policy.

The backend interface does not change.

### `execute_check`

`execute_check()` gains an optional keyword-only `max_attempts: int | None = None` and forwards it to the executor.

The default remains `None`, so release monitor and all existing callers keep current behavior without code changes.

### CLI

`check_command()` gains `--max-attempts`. It validates that a supplied value is greater than zero and forwards it to `execute_check()`.

The existing exit-code mapping remains unchanged. A budget-limited result is `INCOMPLETE`, so the existing check command exits through the current incomplete-result path.

### Reports and artifacts

Existing report and artifact formats remain unchanged.

Budget provenance is carried by skipped-canary reason strings plus actual `run_order`. No extra artifact file or schema field is added in Batch #32.

Unlimited checks must remain byte-identical to the pre-Batch-#32 behavior for the same deterministic qualification inputs.

## Compatibility boundaries

The following flows remain unlimited because they call `execute_check()` without `max_attempts`:

- native release monitor;
- version bisect;
- scheduled release checks;
- GitHub PR qualification producer;
- any existing library caller that does not opt into the new keyword.

Baseline creation is also unchanged and always runs the complete configured suite.

No config schema version, baseline-lock schema, canary schema, agent adapter, resolver, Docker runner, grader, or verdict-policy change is required.

## Error handling

- `max_attempts <= 0`: reject as user input error before model execution.
- budget smaller than one canary: valid `INCOMPLETE` result with zero model attempts.
- backend/runtime failure during a selected canary: preserve existing failure behavior; Batch #32 does not reinterpret runtime exceptions as budget exhaustion.
- skipped canary: deterministic `INCOMPLETE`, never an exception.

## Testing strategy

### Executor tests

Add tests proving:

1. no budget preserves existing suite/run order;
2. budget equal to full-suite cost preserves existing suite/run order;
3. budget above full-suite cost preserves existing suite/run order;
4. a constraining budget executes critical canaries before non-critical canaries;
5. original suite order is preserved in `result.executions`;
6. no canary is partially executed;
7. actual `run_attempt()` calls never exceed the budget;
8. a too-small budget executes zero attempts and returns all canaries `INCOMPLETE`;
9. skipped canaries never call `prepare()`;
10. a critical observed BLOCK plus skipped canaries still yields suite `INCOMPLETE`;
11. the same qualification ID still produces deterministic paired schedules for every selected canary.

### Command and CLI tests

Add tests proving:

- `execute_check(..., max_attempts=N)` forwards the value;
- the default command path remains unlimited;
- `--max-attempts N` is accepted and forwarded;
- zero/negative values fail before qualification execution;
- budget-limited `INCOMPLETE` retains the existing exit code.

### Compatibility tests

Run the existing full suite unchanged.

For a deterministic fake qualification, compare pre-feature and post-feature unlimited outputs for:

- terminal technical report;
- safety report;
- `report.md`;
- `report.json`;
- `qualification.json`.

The unlimited path must be byte-identical.

## Security and safety review points

- Budget input is an integer only and never becomes shell text.
- No credential, sandbox, network, Docker, or agent-runtime boundary changes.
- No PR-controlled source gains authority over privileged GitHub workflows in this batch.
- Budgeting cannot weaken verdict completeness because skipped configured canaries force `INCOMPLETE`.
- The implementation must not use partial-canary evidence to manufacture a suite verdict.

## Deferred follow-ups

After this batch is validated, future work may consider:

- token budgets using normalized usage evidence;
- historical canary effectiveness ranking;
- canary runtime/cost estimates from local history;
- provider-specific monetary estimates outside the qualification policy core;
- persistent low-tech budget presets.

Those are deliberately excluded from Batch #32.
