# Change Targeting V0 Design

**Date:** 2026-09-16
**Status:** proposed canonical design after market/reliability research and two validation spikes
**Scope:** pure local planning layer before existing QuaLock qualification

## Purpose

QuaLock already detects releases, qualifies control/candidate pairs, exports verifiable evidence, and localizes first-bad release boundaries. The missing layer is earlier: QuaLock cannot yet determine whether the current canary suite actually covers the behavioral surface implicated by a specific upstream change.

Change Targeting V0 adds a fail-closed planning layer that answers one narrow question:

> Given an explicit upstream change signal and an explicit target context, which existing QuaLock canaries are relevant, and is their declared coverage sufficient to begin qualification?

`READY` means only that qualification has an adequate declared probe surface. It never means that an upgrade is safe.

## Motivation

The public Codex managed-shell case demonstrates the gap.

Under frozen managed requirements:

```toml
[features]
shell_tool = true
unified_exec = false
```

the published case-study evidence records:

- `0.149.1`: command tool present;
- `0.150.0`: command tool absent, first observed bad release;
- `0.150.1`: command tool absent;
- `0.151.0`: command tool restored.

An earlier authenticated generic Click qualification on `0.149.1 -> 0.150.1` produced `3/3 -> 3/3 PASS`. That run did not exercise the managed-policy combination above. Therefore a generic suite pass was not evidence that the affected behavioral surface was covered.

Change Targeting V0 must make this gap explicit:

```text
upstream signal
    ↓
affected behavioral contract
    ↓
target-context relevance
    ↓
declared coverage matching
    ↓
READY | NOT_APPLICABLE | INCOMPLETE
```

If the relevant contract is not explicitly covered, the correct result is `INCOMPLETE`, not a generic qualification-derived safety inference.

## Strategic boundary

Change Targeting V0 is not another eval framework, release bot, registry, control plane, or changelog intelligence system.

It owns only:

1. explicit change-signal validation;
2. explicit target-context validation;
3. behavioral-contract coverage declarations on probes/canaries;
4. deterministic relevance matching;
5. deterministic minimal-source selection;
6. fail-closed coverage assessment.

Existing QuaLock components continue to own:

- release discovery;
- baseline locking;
- agent execution;
- qualification verdicts;
- paired-change causal evidence;
- Evidence Bundle export/verification;
- first-bad orchestration.

V0 does not automatically connect these stages.

## Non-goals

V0 does not include:

- changelog parsing;
- GitHub issue mining;
- automatic change-signal generation;
- LLM classification;
- public or cross-user release intelligence;
- confidence scores or risk ranks;
- canary generation;
- structural-probe execution;
- automatic provider qualification;
- automatic first-bad escalation;
- automatic deployment or promotion;
- agent registry or control-plane integration;
- general Effective Context fingerprinting;
- redesign of `paired-change/v1` material dimensions;
- modification of existing qualification verdict semantics;
- modification of existing release-monitor execution semantics;
- root README or ROADMAP changes.

## Design principles

### Explicit coverage only

QuaLock must never infer behavioral coverage from:

- canary names;
- task prose;
- grader commands;
- tools observed during previous attempts;
- prior PASS results;
- repository contents.

A source contributes coverage only through an explicit declaration.

### Unknown is not false

A missing target-context fact yields `UNKNOWN`, not `MISMATCH`.

This prevents QuaLock from declaring a change irrelevant merely because the local context was not captured.

### Planning is not qualification

`READY` means:

> sufficient declared coverage exists to begin qualification.

It does not mean:

> the candidate is safe to promote.

### Pure, deterministic evaluation

The planner is a pure function over validated inputs. It does not access the network, execute agent code, inspect binaries, mutate the repository, or query historical results.

### Separate behavioral contracts from causal-control dimensions

`paired-change/v1` `MaterialDimension` answers:

> Which material dimensions must be controlled or observed for a causal claim to be valid?

Change Targeting `BehavioralContract` answers:

> Which behavior does this probe actually exercise?

They are different abstractions and must remain separate.

## Architecture

```text
ChangeSignalV0
      +
TargetContextV0
      +
CoverageDeclarationV0[]
      │
      ▼
┌────────────────────────────┐
│ Change Targeting Planner   │
│                            │
│ 1. relevance matching      │
│ 2. coverage matching       │
│ 3. minimal-source select   │
└──────────────┬─────────────┘
               │
      ┌────────┼─────────┐
      ▼        ▼         ▼
    READY   INCOMPLETE  NOT_APPLICABLE
      │
      ▼
future composition with existing qualification
```

The V0 command exposes the planner directly. It does not call `execute_check()`.

## Behavioral contract registry

V0 uses canonical string IDs validated against a small in-code registry.

Initial registry:

```text
command.execution
tool.inventory
mcp.visibility
approval.semantics
```

The registry is intentionally small. New contract IDs may be added later without changing the V0 matching model.

An unknown contract ID is a hard input error.

The registry must not silently accept arbitrary free-form contract names because spelling drift would create false coverage gaps or false matches.

## Canonical context facts

Context is represented as flat key/value facts.

Example:

```yaml
facts:
  os.family: linux
  execution.mode: container
  codex.managed.shell_tool: true
  codex.managed.unified_exec: false
```

V0 fact values are limited to canonical JSON scalar values:

- string;
- boolean;
- integer;
- null.

Lists, objects, regular expressions, ranges, executable predicates, and embedded expressions are out of scope.

Context keys are canonical strings. Duplicate keys with conflicting values are invalid input.

## Data model

### `ChangeSignalV0`

Conceptual schema:

```yaml
schema_version: 0
agent: codex
baseline_version: 0.149.1
candidate_version: 0.150.1
impacts:
  - contract_id: command.execution
    scope_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
source:
  kind: upstream-issue
  ref: openai/codex#41099
```

Required fields:

- `schema_version`;
- `agent`;
- `baseline_version`;
- `candidate_version`;
- one or more `impacts`.

Each impact contains:

- one validated `contract_id`;
- zero or more exact-match `scope_requirements`.

`source` is optional inert provenance metadata. The V0 planner does not decide whether a source is trustworthy.

A change signal means only:

> this behavioral contract may be affected within this stated scope.

It is not itself proof of a regression.

### `TargetContextV0`

Conceptual schema:

```yaml
schema_version: 0
facts:
  os.family: linux
  codex.managed.shell_tool: true
  codex.managed.unified_exec: false
```

`TargetContextV0` is explicit planner input. V0 does not synthesize or persist it into `baseline.lock`.

### `CoverageDeclarationV0`

Coverage is owned by the source that exercises the behavior.

For existing canaries the declaration is colocated with the canary definition:

```yaml
coverage:
  - contract_id: command.execution
    context_requirements:
      execution.mode: container
```

Conceptually each normalized declaration contains:

- `source_id`;
- `source_kind`;
- `contract_id`;
- zero or more `context_requirements`.

V0 implementation supports existing canaries as `source_kind=canary`.

The generic `source_id`/`source_kind` model preserves a later path for deterministic structural probes without requiring V0 to implement them.

No coverage declaration means no targeting coverage.

### `CoverageAssessmentV0`

Conceptual output:

```json
{
  "schema_version": 0,
  "signal_sha256": "<64 lowercase hex>",
  "target_context_sha256": "<64 lowercase hex>",
  "coverage_sha256": "<64 lowercase hex>",
  "status": "INCOMPLETE",
  "relevant_contracts": ["command.execution"],
  "selected_sources": [],
  "uncovered": [
    {
      "contract_id": "command.execution",
      "reason": "COVERAGE_GAP"
    }
  ],
  "unresolved": []
}
```

`relevant_contracts` is the lexically sorted set of distinct `contract_id` values from impacts whose scope matched the target context. Multiple `MATCH` impacts for the same contract contribute one entry. An impact whose relevance is `UNKNOWN` is not promoted into `relevant_contracts`; it remains represented in `unresolved`, including when another impact for the same contract is `MATCH`.

`uncovered` is reserved for proven-relevant contracts with a real coverage gap. V0 emits at most one `uncovered` entry per distinct relevant contract, every such entry uses `COVERAGE_GAP`, and entries are sorted lexically by `contract_id`. Missing context is never encoded as an uncovered probe.

`unresolved` records valid inputs whose applicability or compatible coverage cannot be decided. Each entry contains `contract_id`, `reason`, and lexically sorted distinct `missing_context_keys`; `COVERAGE_CONTEXT_UNKNOWN` entries additionally contain lexically sorted distinct `source_ids` for the declarations whose requirements could not be decided. Entries with the same `(contract_id, reason)` are aggregated by set union. The final `unresolved` array is sorted lexically by `(contract_id, reason)`, so neither duplicate records nor input order affect output.

`selected_sources` is non-empty only for `READY`; it is empty for `INCOMPLETE` and `NOT_APPLICABLE`, and when present it is sorted lexically.

The normalized assessment output must be canonically ordered and reproducible. Canonical assessment bytes are UTF-8 JSON for `CoverageAssessmentV0` with object keys sorted lexically, field arrays ordered by the explicit rules in this spec, compact JSON separators, and no trailing newline. Human-readable terminal presentation is not part of those canonical assessment bytes; it has a separate stable-presentation requirement below.

`signal_sha256`, `target_context_sha256`, and `coverage_sha256` are required assessment fields. Each is the SHA-256 digest of the same canonical-JSON encoding rule applied to its validated normalized input: `ChangeSignalV0`, `TargetContextV0`, and the complete coverage-declaration set respectively. Before hashing, exact duplicate impact/declaration records are removed, map keys are sorted recursively, `ChangeSignalV0.impacts` is sorted lexically by `(contract_id, canonical scope_requirements bytes)`, and coverage declarations are sorted lexically by `(source_kind, source_id, contract_id, canonical context_requirements bytes)`. `TargetContextV0.facts` is a map and therefore canonicalizes by lexical key order; duplicate mapping keys are invalid input rather than an ordering case. Consequently, semantically identical validated inputs that differ only by record order or exact duplicate impact/declaration records produce identical digest values and identical canonical assessment bytes.

## Requirement matching

Each requirement map is evaluated against `TargetContextV0` using exact scalar equality.

For one requirement key:

```text
target contains same value      → MATCH
target contains different value → MISMATCH
target lacks key                → UNKNOWN
```

For a map of multiple requirements:

```text
any MISMATCH → MISMATCH
else any UNKNOWN → UNKNOWN
else → MATCH
```

An empty requirement map is `MATCH`.

`UNKNOWN` is never collapsed into `MISMATCH`.

## Relevance matching

For every `ChangeSignalV0.impact`, compare its `scope_requirements` with the target context.

The result is one of:

- `MATCH`: the behavioral contract is relevant;
- `MISMATCH`: that impact is proven not applicable to the target;
- `UNKNOWN`: target facts are insufficient to decide.

Any `UNKNOWN` impact prevents a fully resolved assessment and therefore contributes an `INCOMPLETE` `TARGET_CONTEXT_UNKNOWN` record. Its `missing_context_keys` are exactly the requirement keys absent from `TargetContextV0` after applying the requirement-map rule above; keys with explicit different values are mismatches, not missing.

Only `MATCH` impacts proceed to coverage matching and only their contracts appear in `relevant_contracts`. An `UNKNOWN` impact remains explicit in `unresolved` even when another impact for the same contract is `MATCH`.

## Coverage matching

For each relevant contract, consider only declarations with the same `contract_id`.

Each declaration's `context_requirements` is matched against `TargetContextV0`.

Only `MATCH` declarations count as compatible coverage.

A declaration with `UNKNOWN` context does not count as coverage. If no `MATCH` declaration covers a relevant contract and at least one declaration for that contract is `UNKNOWN`, the result for that contract is `COVERAGE_CONTEXT_UNKNOWN` regardless of whether other declarations are `MISMATCH`. The unresolved record contains the sorted union of missing requirement keys from the `UNKNOWN` declarations and their sorted `source_id` values.

A declaration with `MISMATCH` context is ignored for coverage and never converts an `UNKNOWN` declaration into a coverage gap. If no declaration is `MATCH` and none is `UNKNOWN`, the contract is `COVERAGE_GAP`.

## Minimal source selection

When all relevant contracts have compatible coverage, the planner chooses a deterministic minimum-cardinality set of sources that covers all relevant contracts.

Rules:

1. minimize number of distinct `source_id` values;
2. among equal-cardinality solutions, choose the lexicographically smallest sorted source-ID tuple;
3. return selected source IDs in lexical order.

V0 does not weight:

- execution cost;
- historical success rate;
- provider token cost;
- latency;
- contract severity;
- source confidence.

Those optimizations are follow-on work.

The expected V0 problem size is small enough for an exhaustive deterministic set-cover search. Premature heuristic optimization is not required.

## Assessment states

### `READY`

Requirements:

- at least one impact is `MATCH`;
- no impact is `UNKNOWN`;
- every relevant behavioral contract has at least one compatible coverage declaration.

Meaning:

> qualification may begin using the selected coverage sources.

`READY` is not an upgrade verdict.

### `NOT_APPLICABLE`

Requirement:

- every impact is `MISMATCH`.

Meaning:

> the supplied signal is proven outside the supplied target context.

It does not mean the candidate is generally safe.

### `INCOMPLETE`

Returned when validated inputs do not provide enough evidence to reach `READY` or `NOT_APPLICABLE`.

Stable V0 reasons:

```text
COVERAGE_GAP
TARGET_CONTEXT_UNKNOWN
COVERAGE_CONTEXT_UNKNOWN
```

`COVERAGE_GAP` appears only in `uncovered`. `TARGET_CONTEXT_UNKNOWN` and `COVERAGE_CONTEXT_UNKNOWN` appear only in `unresolved`, with the missing context keys required to explain what remains unknown. An assessment may contain multiple canonically sorted entries.

## Truth table

| Signal relevance | Compatible coverage | Assessment |
| --- | --- | --- |
| `MATCH` | yes | candidate for `READY` |
| `MATCH` | no `MATCH`; at least one context-`UNKNOWN` declaration | `INCOMPLETE / COVERAGE_CONTEXT_UNKNOWN` |
| `MATCH` | no `MATCH`; zero context-`UNKNOWN` declarations | `INCOMPLETE / COVERAGE_GAP` |
| `UNKNOWN` | any | `INCOMPLETE / TARGET_CONTEXT_UNKNOWN` |
| `MISMATCH` | any | impact ignored |
| all impacts `MISMATCH` | n/a | `NOT_APPLICABLE` |

If one impact is `MATCH` and another is `UNKNOWN`, the overall result is `INCOMPLETE`.

## Contradictions and duplicates

Input validation rejects structural contradictions rather than attempting to rank them.

Hard errors include:

- unsupported schema version;
- malformed agent or version identity;
- unknown behavioral contract;
- unsupported context value type;
- duplicate mapping keys at any schema level, detected before ordinary map materialization;
- repeated coverage declarations for the same normalized `source_id + contract_id + context key` that assert conflicting required values;
- malformed coverage declaration.

Exact duplicate, semantically identical impact and coverage-declaration records are normalized away deterministically after validation. Distinct impacts may legitimately share a `contract_id` when their `scope_requirements` differ; they remain independent relevance assertions and are not treated as contradictory. Mapping-key duplicates are never normalized, even when their values are identical: the loader rejects them fail-closed before constructing the conceptual maps shown above. No duplicate policy is deferred to implementation planning, and input record order must not affect normalized assessment output.

## CLI surface

Conceptual V0 command:

```bash
qualock target-change SIGNAL --context CONTEXT
```

The precise file-format flags may follow existing CLI conventions during implementation planning, but the command contract is:

- both signal and target context are explicit inputs;
- project canaries provide coverage declarations;
- execution is local and side-effect free apart from terminal output;
- no provider call occurs;
- no qualification result is created;
- no baseline is mutated.

Exit behavior:

```text
0  READY
2  NOT_APPLICABLE
4  INCOMPLETE
3  invalid input/configuration
1  unexpected operational error
```

`NOT_APPLICABLE` is a resolved planner outcome, not a qualification failure, but it intentionally uses a non-zero exit code so shell/CI chaining cannot mistake "nothing applicable to qualify" for `READY` and proceed fail-open. Automation that wants to accept `NOT_APPLICABLE` must handle exit `2` explicitly.

## Integration boundaries

### Release monitor

V0 does not modify release-monitor behavior.

Today release monitor discovers the latest release and calls the existing qualification check. A future design may insert Change Targeting before qualification, but that integration must be separately designed because it changes automated provider-spend and safety semantics.

### Existing qualification

`execute_check()` is unchanged in V0.

Change Targeting therefore cannot yet constrain execution to the selected source set. V0 proves only planner semantics and exposes selected source IDs for human/CI use and later executor integration.

### Paired Change V1

No `paired-change/v1` model or claim semantic changes are part of V0.

Change Targeting operates before paired execution. Paired Change continues to prove whether an observed regression is attributable under its existing validity conditions.

### First Bad V1

No `first-bad/v1` algorithm change is part of V0.

First Bad remains useful only after the relevant behavioral surface is represented by a suitable qualification probe. Change Targeting prevents users from interpreting an unrelated suite as adequate coverage.

### Baseline lock

`TargetContextV0` is not added to `baseline.lock`.

General Observed Effective Context capture is a separate design problem. V0 accepts explicit context rather than broadening baseline identity prematurely.

## Artifact ownership

V0 has three external planner artifacts:

1. `ChangeSignalV0` — external/manual input;
2. `TargetContextV0` — explicit target input;
3. `CoverageAssessmentV0` — deterministic output.

Canary `coverage` declarations are project probe metadata rather than a fourth standalone artifact.

V0 does not:

- sign these artifacts;
- include them in Evidence Bundle V1;
- bind them into paired-change protocol evidence;
- publish them automatically.

Those are follow-on decisions after planner semantics prove useful.

## Security model

Change signals are inert data.

The planner must not:

- follow URLs in source metadata;
- fetch referenced issues;
- execute source-provided commands;
- evaluate expressions;
- support dynamic imports;
- evaluate regular expressions supplied by signals;
- invoke an LLM;
- inspect credentials;
- invoke a provider;
- run a canary.

Context matching is exact equality over validated scalar values only.

File parsing must use the repository's normal safe-size/schema-validation patterns and reject malformed payloads rather than partially accepting them.

## Backward compatibility

Existing canaries without `coverage` remain valid for all existing QuaLock workflows.

For Change Targeting they contribute no behavioral coverage.

Therefore:

- `baseline` behavior is unchanged;
- `check` behavior is unchanged;
- release monitor behavior is unchanged;
- GitHub PR qualification behavior is unchanged;
- Evidence Bundle V1 inventory is unchanged;
- paired-change behavior is unchanged;
- first-bad behavior is unchanged.

No historical canary receives inferred coverage automatically.

## Managed-shell acceptance case

Historical signal:

```yaml
agent: codex
baseline_version: 0.149.1
candidate_version: 0.150.1
impacts:
  - contract_id: command.execution
    scope_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
```

Target:

```yaml
facts:
  codex.managed.shell_tool: true
  codex.managed.unified_exec: false
```

Current Click sentinel has no behavioral coverage declaration.

Required V0 result:

```text
status = INCOMPLETE
relevant_contracts = [command.execution]
selected_sources = []
uncovered = [command.execution / COVERAGE_GAP]
unresolved = []
```

It is forbidden to infer `command.execution` coverage merely because the Click task normally requires shell/file work.

If a future dedicated source explicitly declares compatible `command.execution` coverage, the same signal/context may become `READY`.

## Acceptance matrix

V0 must prove at least:

1. managed-shell signal + current legacy Click canary → `INCOMPLETE / COVERAGE_GAP`;
2. same signal + explicitly compatible coverage declaration → `READY`;
3. required target fact absent → `INCOMPLETE / TARGET_CONTEXT_UNKNOWN`;
4. target explicitly sets `codex.managed.unified_exec=true` → `NOT_APPLICABLE`;
5. two relevant contracts where one source covers both and two alternatives cover one each → select the one-source solution;
6. equal-cardinality solutions → lexical deterministic tie-break;
7. coverage declaration requires a missing target fact → `INCOMPLETE / COVERAGE_CONTEXT_UNKNOWN` with the missing key and affected `source_id`;
8. one coverage declaration `MISMATCH` plus another `UNKNOWN`, with no `MATCH`, → `INCOMPLETE / COVERAGE_CONTEXT_UNKNOWN`, never `COVERAGE_GAP`;
9. unknown relevance because a signal requirement fact is absent → `TARGET_CONTEXT_UNKNOWN` in `unresolved`, not in `relevant_contracts` or `uncovered`;
10. unknown contract ID → input error / exit `3`;
11. any duplicate mapping key, including a repeated target-context key with the same value, → input error / exit `3`;
12. exact semantic duplicate impact/declaration records normalize away and input record ordering differences do not change canonical assessment;
13. semantically identical signal/context/coverage inputs with reordered maps or records produce identical `signal_sha256`, `target_context_sha256`, and `coverage_sha256` values;
14. legacy canary without coverage declaration never contributes implicit coverage;
15. CLI returns `0` only for `READY`, `2` for `NOT_APPLICABLE`, and `4` for `INCOMPLETE`;
16. no planner path invokes an agent/provider or mutates baseline/results.

## Testing strategy

Implementation must be TDD.

Required test layers:

### Pure model tests

Validate:

- schema constraints;
- contract registry enforcement;
- scalar fact constraints;
- contradictions/duplicates;
- canonical serialization.

### Pure matcher tests

Exhaustive truth tables for:

- requirement-map `MATCH/MISMATCH/UNKNOWN`;
- relevance aggregation;
- coverage aggregation;
- overall status.

### Selection tests

Prove:

- minimum cardinality;
- lexical tie-breaking;
- deterministic ordering;
- empty/redundant coverage behavior.

### Canary-loader compatibility tests

Prove:

- legacy canaries still load unchanged;
- optional coverage declarations normalize correctly;
- invalid declarations fail closed.

### CLI tests

Prove:

- distinct fail-closed exit codes, including `READY=0`, `NOT_APPLICABLE=2`, and `INCOMPLETE=4`;
- shell/CI chaining cannot advance from `NOT_APPLICABLE` as if it were `READY`;
- stable human-readable terminal representation, explicitly separate from canonical assessment bytes;
- malformed input behavior;
- no provider execution.

### Historical acceptance test

Encode the managed-shell coverage gap as a deterministic regression test. The test must fail if a legacy generic canary is ever treated as implicit coverage.

## Review requirements

Before implementation closure:

- all implementation work follows TDD;
- existing unrelated Ruff/MyPy debt is not expanded into this task;
- every implementation task/fix receives fresh independent review;
- unresolved Critical or Important findings block closure;
- no provider qualification is required for V0 correctness.

## Definition of done

Change Targeting V0 is complete when:

1. canonical V0 models and a small behavioral-contract registry exist;
2. requirement matching implements deterministic `MATCH/MISMATCH/UNKNOWN`;
3. planner returns `READY`, `NOT_APPLICABLE`, or fail-closed `INCOMPLETE`, with unknown applicability/coverage represented separately from proven coverage gaps;
4. CLI automation distinguishes `READY=0`, `NOT_APPLICABLE=2`, and `INCOMPLETE=4`;
5. minimal compatible source selection is deterministic;
6. canaries may opt into explicit behavioral coverage without changing legacy behavior;
7. managed-shell historical coverage gap deterministically yields `INCOMPLETE`;
8. explicit compatible coverage changes that case to `READY`;
9. planner is pure and performs no provider/network execution;
10. existing baseline/check/monitor/evidence/paired-change/first-bad behavior remains backward compatible;
11. canonical serialization and all three required input digests are reproducible under map/record reordering and exact duplicate impact/declaration records;
12. targeted tests plus full appropriate repository verification pass;
13. fresh independent review has zero unresolved Critical or Important findings.

## Follow-on work

Only after V0 is proven should QuaLock consider:

1. selected-source execution rather than planner-only output;
2. deterministic structural-probe sources;
3. explicit Observed Effective Context capture;
4. release-monitor integration;
5. automatic signal ingestion/classification;
6. first-bad escalation from a targeted qualification;
7. public/cross-user behavioral release intelligence;
8. standards/attestation integration.

Each follow-on requires a separate design because it changes either execution, provider-spend, trust, or data-sharing boundaries.
