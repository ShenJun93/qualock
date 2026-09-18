# Selected-Source Execution V1 Design

## Status

Proposed first execution follow-on to Change Targeting V0.

V1 adds one explicit user-invoked path that may spend runtime/provider resources only after the current Change Targeting assessment is `READY`.

## Goal

Given an explicit `ChangeSignalV0`, explicit `TargetContextV0`, the current project, and current baseline lock, execute exactly the canary source IDs selected by Change Targeting V0.

The central rule is:
> Planning decides what is relevant. Execution may consume that exact plan, but must never widen, infer, replace, or silently repair the selected source set.

A targeted result is not a full-suite update-safety verdict.

## Problem

Change Targeting V0 ends at `READY + selected_sources`. Existing `execute_check()` always orchestrates the full project suite.

Without a dedicated contract, callers could ignore the selected set, run fallback canaries, execute after `INCOMPLETE`, confuse targeted PASS with global safety, or persist partial runs where legacy tooling assumes full-suite qualification.

V1 closes only this planner-to-execution gap.

## Principles

1. `READY` is an execution gate, not a safety verdict.
2. `selected_sources` is authoritative.
3. No fallback or source widening.
4. Full project freshness still applies even when a subset executes.
5. The explicit `target-check` command is provider-spend consent.
6. No resolver/backend/provider work occurs before a current `READY` assessment.
7. Existing `check`, monitor, PR, evidence export, paired-change, first-bad, and V0 planner semantics stay unchanged.
8. Targeted results live in an isolated result namespace.
9. The exact targeting assessment is bound to execution evidence without persisting raw context facts by default.

## Non-goals

- release-monitor integration;
- automatic execution after `target-change`;
- adding `--execute` to `target-change`;
- changelog/issue/PR signal ingestion;
- LLM classification;
- structural-probe source kinds;
- source fallback or adaptive replanning;
- automatic first-bad escalation;
- Evidence Bundle V1 changes;
- portable/public targeted evidence export;
- history/cost analytics for targeted runs;
- baseline schema changes;
- Change Targeting V0 schema changes;
- paired-change protocol semantic changes;
- implicit coverage inference.

## Existing primitives reused

V1 reuses `assess_change()`, strict signal/context loading, project loading, baseline freshness checks, current agent resolvers/backends, `QualificationExecutor`, paired attempt scheduling, qualification policy, `QualificationResult`, runtime provenance, paired-change run evidence, and pricing-sidecar generation.

V1 must not call `execute_check()` directly because that function intentionally owns full-suite orchestration.

## Chosen approach

### Rejected: selected-canary argument on `execute_check()`

This weakens the semantic boundary of normal full-suite checks and makes partial results easy to misplace in the normal result namespace.

### Chosen: dedicated targeted orchestration over lower-level primitives

The new path recomputes targeting, gates on `READY`, resolves exactly the selected canaries, checks full-project baseline freshness, executes the selected subset, and writes targeted-only artifacts.

Implementation may extract private shared helpers from `execute_check()` only when regression tests prove existing full-suite behavior and artifact bytes/locations remain unchanged.

### Rejected: stored plan file consumed later

A stored plan introduces freshness/signing lifecycle questions. V1 instead recomputes targeting immediately before execution.

## CLI

New command:
```text
qualock target-check SIGNAL --context CONTEXT
```

Optional existing budget controls:
```text
--max-attempts N
--max-tokens N
```

There is no candidate argument. `signal.agent`, `signal.baseline_version`, and `signal.candidate_version` are the single source of truth.

`qualock target-change SIGNAL --context CONTEXT` remains planner-only and V0-compatible.

## High-level flow

```text
SIGNAL + CONTEXT
      |
      v
strict load + current project + baseline.lock
      |
      v
local identity/freshness preflight
  - signal agent matches config + lock
  - signal baseline matches lock
  - full project suite/config fresh
      |
      v
recompute CoverageAssessmentV0
      |
      +-- INCOMPLETE -----> no execution, exit 4
      +-- NOT_APPLICABLE -> no execution, exit 5
      '-- READY with non-empty selected_sources
            |
            v
    resolve exact selected canary IDs
            |
            v
    resolve binaries/backend
            |
            v
    QualificationExecutor(selected_canaries)
            |
            v
    targeted-only artifacts + receipt
```

Project/config/canary/baseline-lock reads and local freshness checks are allowed before the gate because `target-check` is an execution command and invalid execution identity must not be masked as a planner disposition. Resolver calls, backend construction, credential selection, Docker/host execution, candidate downloads, and provider calls are forbidden before `READY`.

## Planner recomputation

`target-check` never accepts a stored assessment as authority.

It loads the V0 signal/context forms and recomputes `assess_change(signal, context, coverage_declarations(canaries))` against the current project.

This prevents stale or user-edited selections from being executed.

## Execution gate

Only `assessment.status == READY` with a non-empty `selected_sources` tuple permits execution. `CoverageAssessmentV0` already enforces non-empty selected sources for READY; V1 rechecks this as a defensive execution-boundary invariant.

For `INCOMPLETE` or `NOT_APPLICABLE`: no baseline/candidate resolution, backend construction, credentials, provider calls, targeted result directory, pricing sidecar, or fallback planning.

The terminal must explicitly say `Targeted execution: NOT STARTED`.

## Source resolution

V1 supports the V0 source kind only: `source_kind = canary`.

Resolution rules:
1. Build an ID map from already loaded project canaries.
2. Require every selected ID to exist.
3. Require uniqueness.
4. Preserve planner lexical selected-source order.
5. Construct `selected_canaries` in that order.
6. Never append critical, helpful, generic, or fallback canaries.

A missing selected ID fails before runtime resolution.

## Full-project freshness versus selected scope

Before runtime/provider work V1 reads `baseline.lock`, fingerprints all current project canaries/config, and uses the existing full-suite freshness check.

It additionally requires:
- `config.agent.name == signal.agent`;
- `baseline.lock.agent.name == signal.agent`;
- `baseline.lock.agent.version == signal.baseline_version`.

Coverage metadata remains excluded from the legacy suite fingerprint exactly as in V0. The assessment `coverage_sha256` separately binds coverage state used for selection.

## Candidate identity

The candidate derives from `signal.agent + '@' + signal.candidate_version`.

Existing resolver identity rules remain unchanged: baseline binary SHA must match the lock; support-identity rules remain unchanged; Gemini still requires its support fingerprint.

`source.ref` is inert and never dereferenced.

## Qualification semantics

Selected canaries run through existing `QualificationExecutor` paired baseline/candidate attempts. V1 does not introduce candidate-only execution.

The result is a normal `QualificationResult` over the selected subset only.

## No fallback

Preparation failure, invalid attempts, regression, attempt-budget skipping, or token-budget skipping never triggers a replacement source.

The originally selected set remains immutable. Existing qualification policy determines PASS/WARN/BLOCK/INCOMPLETE.

## Budget semantics

`--max-attempts` and `--max-tokens` reuse existing `check` semantics and apply after selection.

If budget prevents selected sources from completing, qualification is `INCOMPLETE`. No replacement is chosen.

## Targeted verdict meaning

A targeted PASS means only that the selected source set completed without an observed regression requiring WARN/BLOCK/INCOMPLETE under existing policy.

It does not mean the full project passed, all protected workflows passed, or the update is globally safe.

The targeted CLI must never reuse the full-suite `SAFE TO UPDATE` recommendation.

## Rendering

After local identity/freshness preflight succeeds, the command renders the current Change Targeting assessment.

If local preflight fails, it renders the bounded diagnostic plus `Targeted execution: NOT STARTED` and never prints an execution-start banner.

For READY, binary/backend resolution occurs next. Only immediately before invoking `QualificationExecutor` may the command render:
```text
QuaLock Targeted Qualification

Scope: selected sources only; not a full-suite update-safety verdict.
Selected sources: ...
```

followed by a technical qualification table over selected sources only.

## Exit codes

```text
0  targeted qualification completed with PASS or WARN
1  unexpected operational failure
2  targeted qualification completed with BLOCK
3  invalid input/configuration/preflight/command-local usage, including stale baseline
4  targeting INCOMPLETE; execution did not start
5  targeting NOT_APPLICABLE; execution did not start
6  targeted qualification executed but completed INCOMPLETE
```

`NOT_APPLICABLE=5` and executed `INCOMPLETE=6` are command-specific so automation can distinguish a pre-spend planner disposition from a post-start incomplete qualification. V0 `target-change` keeps `NOT_APPLICABLE=2` unchanged.

WARN keeps existing `check` automation behavior: exit 0.

## Result namespace isolation

Targeted runs are stored under:
```text
.qualock/results/targeted/<qualification_id>/
```

with a prefix such as `target-check-<timestamp>-<nonce>`.

This uses the already ignored `results/` tree while isolating targeted runs from current direct-child history/cost scanning and direct `.qualock/results/<qualification_id>` Evidence Bundle export.

Current `scan_results()` skips direct child directories that do not themselves contain `report.json`; therefore the `targeted/` container is ignored. Evidence Bundle export accepts one safe direct qualification ID and cannot address `targeted/<id>` as a qualification ID.

V1 does not modify legacy scanners to consume targeted runs. Dedicated targeted filenames below provide a second defense against future recursive discovery.

## Targeted artifacts

A completed targeted execution writes:
```text
targeted-report.md
targeted-report.json
targeted-qualification.json
targeted-evidence-provenance-v1.json
targeted-paired-change-run-v1.json
targeted-run-v1.json
pricing.json  # optional/best effort
```

Existing provenance and paired-change model builders may describe the selected execution subset in memory, but V1 persists them under targeted-specific filenames. Existing Evidence Bundle V1 does not recognize or export these filenames.

`targeted-report.md`, `targeted-report.json`, and `targeted-qualification.json` must carry an explicit targeted scope marker/banner and selected-source list. They must never persist full-suite safe-update language. The JSON forms wrap the selected-subset `QualificationResult` rather than masquerading as the ordinary direct-run report schema.

## Paired-change binding

For targeted execution, the existing paired-change run model may be built over `selected_canaries` and use `suite_fingerprint(selected_canaries)` as its selected-suite digest, but it is persisted as `targeted-paired-change-run-v1.json`.

It is not a standard Evidence Bundle V1 companion and must not be passed to current bundle materialization/verification as if it represented the full project suite.

The targeted receipt separately binds full `project_suite_sha256` used for baseline freshness and `selected_suite_sha256` used for the executed subset.

Normal `execute_check()` and standard `paired-change-run-v1.json` continue using the full project suite exactly as before.

## `targeted-run-v1.json`

Required semantic fields:
- schema version and protocol ID `selected-source-execution/v1`;
- qualification ID;
- full nested READY `CoverageAssessmentV0`;
- canonical assessment SHA256;
- full project suite SHA256;
- selected suite SHA256;
- config SHA256;
- exact baseline-lock SHA256;
- selected sources;
- attempted sources;
- qualification verdict;
- run-order SHA256;
- exact byte SHA256 values for `targeted-report.json`, `targeted-qualification.json`, `targeted-evidence-provenance-v1.json`, and `targeted-paired-change-run-v1.json`.

Exact Pydantic field layout may vary, but these bindings are mandatory.

## Receipt invariants

- assessment status is READY;
- receipt selected sources exactly equal assessment selected sources;
- selected sources are non-empty, unique, lexically sorted;
- attempted sources are a unique sorted subset of selected sources;
- assessment SHA hashes canonical V0 assessment bytes;
- project suite SHA is the full suite used for freshness;
- selected suite SHA fingerprints exact selected canaries;
- baseline-lock SHA hashes the exact lock used;
- config SHA equals `config_fingerprint(config)` from the exact preflight config;
- run-order SHA is `sha256_canonical(result.run_order)`, matching existing provenance semantics;
- required artifact digests bind exact persisted bytes;
- qualification IDs match across artifacts.

## Raw context privacy

`TargetContextV0.facts` is not copied into targeted evidence by default.

V1 persists the target-context digest and resulting assessment, not the raw context document/fact map. Targeted markdown/JSON/terminal renderers must never dump raw context fact values; only fields already present in `CoverageAssessmentV0`, such as missing-context key names, may appear. Portable disclosure policy is deferred.

## Write ordering

1. Complete selected qualification.
2. Create `.qualock/results/targeted/<id>/` with no-overwrite semantics.
3. Write targeted-scope report/qualification artifacts.
4. Write targeted runtime provenance.
5. Write targeted-named paired-change run evidence.
6. Write optional pricing sidecar best effort.
7. Hash required persisted artifacts.
8. Write `targeted-run-v1.json` last with exclusive-create semantics.

A required evidence failure makes the command fail. V1 does not redesign all qualification storage transactionality.

## Error taxonomy

Input/configuration errors include malformed signal/context, invalid budgets, signal/config agent mismatch, signal/baseline mismatch, missing selected source, malformed coverage, missing project config, and unsupported future source kind.

They exit 3. For this new command, stale/mismatched baseline preflight also exits 3 because execution has not started. Planner `INCOMPLETE` remains exit 4; planner `NOT_APPLICABLE` remains exit 5; an executed qualification that becomes `INCOMPLETE` exits 6. Unexpected internal/runtime errors exit 1 with bounded diagnostics.

## Security/provider-spend model

`target-check` is explicit consent for the same class of local provider/runtime work as ordinary `check`, but only after the targeting gate succeeds.

Direct tests must prove non-READY assessments call none of resolver, backend factory, Docker runner, host runner, credential selection, provider HTTP, or source-ref network paths.

After READY and local preflight, existing resolver/provider behavior is permitted; V1 adds no new network capability.

## Backward compatibility

Unchanged: `baseline`, `check`, `target-change`, monitor/scheduler, PR qualification, version bisect, evidence export/verify, history, cost, paired-change verification, and first-bad verification.

Normal checks remain direct children of `.qualock/results/`; targeted runs are nested under `.qualock/results/targeted/`.

## Refactoring boundary

Implementation may extract a private common qualification core from `execute_check()` only if regression tests preserve existing artifact paths, full-suite fingerprint, canary order, IDs, output models, sidecars, exceptions, and CLI exits.

The helper must distinguish full project canaries used for freshness from execution canaries used for attempts.

## Historical managed-shell acceptance

Legacy Click sentinel remains coverage-free. For the managed-shell signal/context it yields `INCOMPLETE/COVERAGE_GAP`, executes nothing, creates no targeted result directory, and exits 4.

With one explicit compatible `managed-shell-registration-probe`, the planner selects that source and V1 executes only it. Click never runs as an implicit helper or fallback.

Authenticated provider execution is not required; fake resolvers/backends are sufficient.

## Acceptance matrix

1. INCOMPLETE -> zero resolver/backend/provider work, exit 4.
2. NOT_APPLICABLE -> zero resolver/backend/provider work, exit 5.
3. READY with one source executes only that source.
4. READY with two selected sources executes exactly those two in planner order.
5. unrelated/critical project canaries are never appended.
6. preparation failure causes no fallback.
7. invalid attempts cause no fallback.
8. attempt-budget skip -> executed qualification INCOMPLETE, no replacement, exit 6.
9. token-budget skip -> executed qualification INCOMPLETE, no replacement, exit 6.
10. signal/config agent mismatch exits 3 during local preflight before planner disposition, resolver, or backend.
11. signal baseline mismatch exits 3 during local preflight before planner disposition, resolver, or backend.
12. full project suite/config staleness exits 3 during local preflight even if the selected canary itself is unchanged.
13. coverage-only changes can alter selection while legacy suite fingerprint stays unchanged; assessment coverage digest records the change.
14. baseline binary/support checks match ordinary check.
15. execution remains paired baseline/candidate, not candidate-only.
16. result contains only selected canary executions.
17. artifacts exist only under `.qualock/results/targeted/<id>/`.
18. history/cost do not ingest nested targeted runs.
19. Evidence Bundle V1 normal export cannot accidentally export the nested run.
20. receipt READY assessment and selected tuple match exactly.
21. receipt artifact hashes match exact bytes.
22. raw target-context facts are absent from receipt.
23. `source.ref` remains inert.
24. targeted PASS never prints full-suite safe-update language.
25. exit matrix is exact, including distinct planner-INCOMPLETE=4 and executed-qualification-INCOMPLETE=6.
26. V0 `target-change` remains unchanged.
27. ordinary full-suite `check` remains unchanged.
28. monitor/PR/bisect never invoke targeted execution.

## Testing strategy

Implementation is TDD.

- Gate tests use forbidden resolver/backend/provider mocks.
- Selection tests include compatible alternatives and critical unrelated canaries.
- Qualification tests prove paired attempts, budgets, verdicts, and no fallback.
- Storage tests prove nested paths, required artifacts, receipt hashes, privacy, and scanner isolation.
- CLI tests prove exact exits and targeted-only language.
- Compatibility tests cover existing check, executor, evidence, history/cost, paired-change, first-bad, and V0 targeting suites.

No authenticated provider run is required for V1 correctness.

## Review requirements

- every task/fix follows TDD;
- every task/fix receives fresh independent review;
- unresolved Critical/Important findings block progress;
- no unrelated Ruff/MyPy debt repair;
- no dependency installation;
- whole-branch review checks fail-open execution, source widening, targeted/full-suite claim confusion, freshness bypass, namespace leakage, receipt/privacy binding, provider work before READY, and ordinary-check regression.

## Definition of done

V1 is complete when `target-check` exists; local execution identity/freshness is validated before planner disposition is used; only freshly recomputed READY assessments with non-empty selected sources can execute; exactly selected sources run; no fallback exists; full-project freshness is enforced; signal identity is bound to config/baseline; paired qualification runs the selected subset; targeted artifacts use targeted-only filenames/schemas; targeted PASS is scoped; receipt bindings are reproducible; raw context is not persisted; legacy scanners/export do not consume targeted runs; V0 and ordinary check remain unchanged; acceptance/full verification pass; and fresh final review has zero unresolved Critical/Important findings.

## Follow-on work

Separate future designs may cover structural probes, targeted Evidence Bundle support, Observed Effective Context capture, release-monitor integration, automatic signal ingestion, first-bad escalation, targeted history/cost analytics, adaptive fallback/replanning, and cross-user release intelligence.

None are implicit in V1.
