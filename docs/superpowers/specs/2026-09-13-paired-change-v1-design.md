# Paired Change V1 Causal Attribution Design

**Date:** 2026-09-13
**Base:** `main@fdd4ecae148d8724ffa3bf9998b30b60da5f4da0`
**Status:** Design approved in chat; implementation pending

## Purpose

Add a narrow, machine-verifiable causal attribution protocol on top of QuaLock's existing qualification engine and Evidence Bundle V1 without changing current qualification verdict semantics.

`paired-change/v1` answers one question:

> When does a deterministic protected-workflow A/B qualification justify attributing an observed regression to the exact tested agent-dependency changeset?

The protocol must also know when it cannot justify that statement. Missing or opaque material evidence therefore produces `UNRESOLVED`, never an inferred match.

## Strategic boundary

QuaLock does not compete by rebuilding generic eval infrastructure. Exact identity, isolation, repeats, provenance, and infra-validity handling are increasingly table stakes. P0 focuses on four differentiators:

1. balanced deterministic paired execution;
2. temporal pair validity;
3. behavior-contract-relative material controls;
4. fail-closed, offline-verifiable causal claims.

The long-term consumer is first-attributable-bad release and behavioral compatibility ranges, but those are out of scope for this P0.

## Non-goals

P0 explicitly does not include:

- Evidence Bundle V2 or any mutation to the strict V1 bundle inventory;
- a public `qualock.lock`, proprietary BOM, or general configuration-management model;
- component-level causal attribution inside a multi-component changeset;
- probabilistic quality-regression statistics or adaptive stopping;
- changing current `PASS`, `WARN`, `BLOCK`, or `INCOMPLETE` policy meanings;
- a public regression registry, compatibility database, dashboard, or range materialization.

P0 deliberately certifies only deterministic protected behavioral contracts with complete valid attempts. Existing attempts classified `valid=False` cannot produce an attributable claim in v1.

## Design principles

Three rules are constitutional:

> Unknown is never equal to unchanged.
> Invalid is never equal to failure.
> Observed difference is never automatically equal to attributable regression.

The verifier is deterministic, offline, read-only, and independent of the original project checkout. Stored claims are never trusted when they can be recomputed from bound evidence.

## Architecture

The recommended architecture is an additive two-stage protocol overlay. Qualification execution writes a prospective `PairedChangeRunV1` sidecar that captures the frozen protocol design and run-local protocol context but has no Evidence Bundle manifest binding. Later, `qualock evidence export` creates Evidence Bundle V1 and materializes portable `ProtocolEvidenceV1` by binding that sidecar to the exact exported manifest digest. The `paired-change/v1` verifier then recomputes validity conditions and the causal claim from the bundle plus portable protocol evidence and emits Claim Receipt V1.

`qualification` and `evidence` remain lower-level dependencies. The new protocol subsystem may depend on them; existing qualification/evidence modules must not depend on `paired_change`.

## Compatibility contract

Current decision values stay unchanged: `PASS`, `WARN`, `BLOCK`, and `INCOMPLETE`. The new attribution result is separate: `NO_REGRESSION_OBSERVED`, `ATTRIBUTABLE_CHANGESET`, or `UNRESOLVED`.

Legacy qualifications without protocol overlay have **no causal claim available**. They are not retroactively labeled `UNRESOLVED`; that value means a valid `paired-change/v1` evaluation was attempted and could not justify attribution.

During P0, the existing verdict remains the source for current workflow decisions. Attribution is additional evidence only.

## Agent dependency state

P0 keeps trusted dependency state deliberately narrow:

```text
AgentDependencyStateV1
- agent_name
- version
- binary_sha256
- support_sha256?
- model.id
- model.snapshot?
- model.reasoning_effort
```

`support_sha256` is generic for every adapter with support material. Codex and Gemini therefore bind support identity; agents with no support material may use `null`.

A state digest means only "the same canonical values captured by this state schema". It is not a claim that every external causal input or provider-side variable is identical.

`ChangeSetV1` is derived from canonical baseline and candidate states. User-declared text such as "only the version changed" is never authoritative.

## Protocol design freeze

Before the first attempt is scheduled, the executor constructs a canonical `ProtocolDesignV1`. It contains the protocol ID/digest, repetitions, alternating-order policy, `FRESH` lifecycle, `max_pair_gap_ms`, and each canary's material-dimension and expected control-profile declarations.

The canonical design is hashed as `protocol_design_sha256`. Every attempt context binds that same digest. The offline verifier recomputes the design digest and requires all attempt contexts to reference it.

This is a runner-enforced prospective freeze, not remote attestation. Offline verification proves internal consistency of the recorded experiment and QuaLock's protocol invariants; it does not cryptographically prove what an untrusted producer did before producing all artifacts.

## Prospective run sidecar

`PairedChangeRunV1` is the check-time artifact. It is written beside normal qualification artifacts immediately after execution and contains all run-local protocol inputs needed later for causal verification, except any Evidence Bundle manifest binding.

Required top-level identity is:

```text
schema_version = 1
protocol_id = paired-change/v1
protocol_digest
protocol_design
protocol_design_sha256
qualification_id
baseline_state
candidate_state
changeset_sha256
canaries[]
```

It MUST NOT contain an `evidence_manifest_sha256`, because no exported manifest exists yet. `ProtocolEvidenceV1` later adds that one portable binding while preserving the canonical run evidence.

The sidecar is prospective evidence, not a portable claim artifact. Its existence alone cannot produce `ClaimReceiptV1`; portable causal verification begins only after export binds the run to an exact Evidence Bundle V1 manifest.

## Protocol evidence overlay

`ProtocolEvidenceV1` is an export-time materialization derived from a valid `PairedChangeRunV1` plus the newly created Evidence Bundle V1 manifest digest. It is additive, lives beside the exported bundle, and does not change the V1 bundle inventory.

Required top-level identity:

```text
schema_version = 1
protocol_id = paired-change/v1
protocol_digest
protocol_design
protocol_design_sha256
qualification_id
evidence_manifest_sha256
baseline_state
candidate_state
changeset_sha256
canaries[]
```

Each canary record binds the existing `canary_fingerprint_sha256` as its behavior-contract identity and records material dimensions, expected preparation/isolation/resource/runtime profile digests, and pair evidence.

P0 reuses the existing canary fingerprint instead of introducing a second behavior-contract schema.

### Material dimensions

V1 uses a closed enum:

```text
AGENT_BINARY
AGENT_SUPPORT
MODEL_DECLARATION
PROJECT_CONFIG
PREPARED_TARGET
RUNTIME_PROFILE
PREPARATION_PROFILE
ISOLATION_PROFILE
RESOURCE_POLICY
```

A canary declares only dimensions material to attribution for that behavior. In P0 this declaration is protocol-only metadata, not part of the behavior-contract fingerprint. `CanarySpec` may expose it through an optional `paired_change` block, but `_canary_fingerprint_payload()` and therefore legacy `canary_fingerprint` / `suite_fingerprint` MUST exclude that block so existing baselines do not become stale solely because causal protocol metadata was added. `ProtocolDesignV1` binds the declaration instead.

A legacy canary with no material-dimension declaration remains valid for existing qualification semantics but cannot receive an attributable paired-change claim. The protocol records the declaration as unavailable and `MaterialDimensionsControlled=UNKNOWN`; absence is never interpreted as an empty material set.

A local structural shell contract may omit provider-side model identity; a model-quality contract cannot safely omit provider/model routing if that state is material but opaque.

Adding a diagnostic reason within an existing gate is compatible. Adding a new required dimension or changing a gate's success criterion requires a new protocol version.

## Pair evidence

Every repetition contains exactly one baseline attempt and one candidate attempt. Each protocol attempt context records the exact side/repetition, the public attempt's `events_sha256`, the frozen `protocol_design_sha256`, monotonic start/finish offsets, one isolation-instance digest, and the applied preparation/isolation/resource/runtime profile digests.

`events_sha256` binds protocol metadata to the exact public attempt already represented in Evidence Bundle V1. Time values are monotonic offsets from qualification start rather than wall-clock timestamps.

The verifier derives pair orientation, adjacency, temporal gap, isolation distinctness, and applied-profile equivalence. Producers do not provide trusted booleans such as `order_valid: true`.

## Balanced scheduler

The current independent per-repetition AB/BA coin flip is insufficient for causal attribution because all repetitions can land in the same orientation.

V1 chooses the first orientation deterministically from the existing qualification/canary seed and then alternates: `AB BA AB ...` or `BA AB BA ...`.

Scheduler properties require one baseline and one candidate slot per repetition, adjacent pair slots, deterministic ordering, and a first orientation that may vary by qualification identity.

Even repetition counts have equal AB/BA counts; odd counts differ by at most one. Budget semantics remain unchanged: QuaLock never starts a partial canary.

## Protocol conditions

Every condition has `type`, `status`, a stable machine-readable `reason`, and optional `evidence_sha256`.

`status` is exactly `TRUE`, `FALSE`, or `UNKNOWN`.

- `TRUE`: evidence is sufficient and the invariant is proven.
- `FALSE`: evidence is sufficient and proves the invariant was violated.
- `UNKNOWN`: evidence is missing or opaque, so the invariant cannot be decided.

Missing required evidence is always `UNKNOWN`, never implicit success. Human prose is not part of policy semantics.

### EvidenceBound

`TRUE` when the referenced Evidence Bundle V1 verifies fully and the overlay binds the exact manifest and qualification identity.

`FALSE` when both inputs are structurally valid but their cross-artifact binding disagrees. A malformed or internally tampered Evidence Bundle is a hard verification error, not a causal condition.

`UNKNOWN` when protocol assessment has insufficient bundle evidence to decide the binding. A stored receipt cannot be verified in that state.

Stable reasons: `EvidenceVerified`, `EvidenceMismatch`, `EvidenceUnavailable`.

No attributable claim may exist unless `EvidenceBound=TRUE`.

### DesignFrozen

`TRUE` when `ProtocolDesignV1` recomputes to its stored digest, every attempt binds that digest, and frozen suite/config/canary/repetition/order/temporal/material-control declarations agree with qualification evidence.

`FALSE` when structurally valid evidence shows a different design identity or post-freeze design value. `UNKNOWN` applies when prospective design binding is missing.

Stable reasons: `DesignVerified`, `DesignChanged`, `DesignFreezeUnavailable`.

Thresholds and materiality declarations cannot be changed after execution and still satisfy this gate.

### StateBound

This gate is qualification-wide. `TRUE` requires baseline and candidate `AgentDependencyStateV1` to bind exactly to trusted baseline/resolved runtime identities for every V1 field.

A captured-vs-trusted identity mismatch produces `FALSE`; a required identity that cannot be bound produces `UNKNOWN`.

`support_sha256=null` is valid only when the resolved adapter has no support material.

Stable reasons: `StateVerified`, `StateMismatch`, `StateIdentityIncomplete`.

Canary-specific materiality does not weaken `StateBound`; `MaterialDimensionsControlled` decides which additional controls matter to a particular behavior claim.

### PreparationEquivalent

`TRUE` when both arms begin from the same prepared workload identity and every applied preparation-profile digest matches the frozen profile.

A verified material preparation difference outside the derived `ChangeSetV1` produces `FALSE`. Missing applied-profile evidence produces `UNKNOWN`.

Stable reasons: `PreparationVerified`, `PreparationMismatch`, `PreparationUnverified`.

### BaselineStable

For deterministic V1 contracts, all expected baseline attempts must be valid and satisfy the contract for this condition to be `TRUE`.
A valid baseline violation produces `FALSE`; missing or invalid baseline evidence produces `UNKNOWN`.

Stable reasons: `BaselineStable`, `BaselineViolation`, `BaselineEvidenceIncomplete`. V1 has no "mostly stable" state.

### AttemptsComplete

`TRUE` requires every expected A/B attempt to exist, occupy one unique expected side/repetition slot, and be classified valid.
A missing or explicitly invalid attempt produces `FALSE`. A structurally contradictory attempt layout is a verification error; an uninterpretable but structurally valid layout produces `UNKNOWN`.

Stable reasons: `AttemptsComplete`, `AttemptMissing`, `AttemptInvalid`, `AttemptLayoutUnknown`.

P0 intentionally does not reinterpret current `valid=False` outcomes, including process crashes or timeouts, as candidate behavioral failures for causal certification.

### AttemptsIsolated

For the V1 `FRESH` lifecycle, `TRUE` requires a distinct isolation instance for every attempt and applied isolation/profile digests equal to the frozen design.

Verified mutable-state reuse or an applied isolation profile different from the frozen profile produces `FALSE`. Missing isolation evidence produces `UNKNOWN`.

Stable reasons: `IsolationVerified`, `IsolationReuseDetected`, `IsolationProfileMismatch`, `IsolationUnverified`.

Persistent-session semantics are outside `paired-change/v1`.

### OrderValid

`TRUE` requires exactly one adjacent A/B pair per repetition and the alternating orientation sequence specified by the frozen design.

Duplicate or missing sides, non-adjacent pair members, or a nonconforming orientation sequence produce `FALSE`. Missing or uninterpretable run-order evidence produces `UNKNOWN`.

Stable reasons: `OrderVerified`, `PairLayoutInvalid`, `OrderImbalanced`, `OrderUnknown`.

### TemporalPairValid

For each pair, temporal gap is `start(second) - finish(first)` using monotonic offsets.

`TRUE` requires every gap to be non-negative and no greater than the frozen `max_pair_gap_ms`.

A verified excessive gap or impossible overlap under the sequential scheduler produces `FALSE`. Missing timing evidence produces `UNKNOWN`.

Stable reasons: `TemporalPairVerified`, `TemporalGapExceeded`, `TemporalEvidenceMissing`, `PairLayoutInvalid`.

There is no universal hard-coded temporal window. `ProtocolDesignV1` freezes the applicable value before execution.

### MaterialDimensionsControlled

For every material dimension declared by the frozen canary profile, the verifier evaluates the observed baseline/candidate values against the derived `ChangeSetV1` and frozen control profile.

A material difference represented by the derived changeset is the treatment under test and is allowed to differ. A dimension frozen as a control must have verified equality across arms.

A verified change in a controlled material dimension produces `FALSE`; missing or opaque evidence for a controlled material dimension produces `UNKNOWN`.

Stable reasons: `MaterialControlsVerified`, `UnexpectedMaterialChange`, `RequiredDimensionOpaque`.

The verifier never converts “not observed changing” into “verified unchanged.”

## Claim model

Claims are computed per canary. V1 has exactly three claim classes:

```text
NO_REGRESSION_OBSERVED
ATTRIBUTABLE_CHANGESET
UNRESOLVED
```

The transition rule is pure and deterministic:

```text
if any qualification-wide required condition != TRUE:
    UNRESOLVED
elif any canary required condition != TRUE:
    UNRESOLVED
elif baseline contract is not satisfied:
    UNRESOLVED
elif candidate contract is satisfied:
    NO_REGRESSION_OBSERVED
elif candidate contract is violated:
    ATTRIBUTABLE_CHANGESET
```

There is no path from a required `UNKNOWN` condition to an attributable claim. `ATTRIBUTABLE_COMPONENT`, confidence labels, and probabilistic effect claims are outside v1.

## Claim receipt

`ClaimReceiptV1` contains only recomputable, digest-bound results:

- schema version and protocol ID/digest;
- qualification ID;
- evidence-manifest and protocol-evidence digests;
- baseline/candidate state digests and changeset digest;
- qualification-wide conditions;
- per-canary conditions and claims;
- verifier name/version.

Qualification-wide conditions are `EvidenceBound`, `DesignFrozen`, and `StateBound`. If any is not `TRUE`, every canary claim is `UNRESOLVED`.

Per-canary conditions are `PreparationEquivalent`, `BaselineStable`, `AttemptsComplete`, `AttemptsIsolated`, `OrderValid`, `TemporalPairValid`, and `MaterialDimensionsControlled`.

Canonical receipt content excludes verification timestamps, random UUIDs, hostnames, absolute paths, and other machine-local data so identical canonical inputs yield a byte-identical receipt.

A stored receipt is never a source of truth. Verification recomputes the receipt and rejects a stored claim, reason, condition, or digest that disagrees.

## Storage

Check-time and export-time artifacts have separate lifecycles. Normal qualification storage gains one local sidecar:

```text
qualification/
  ... existing qualification artifacts ...
  paired-change-run-v1.json
```

`paired-change-run-v1.json` contains `PairedChangeRunV1` and deliberately has no Evidence Bundle manifest digest.

After `qualock evidence export --out BUNDLE`, `BUNDLE` remains exactly the Evidence Bundle V1 directory so existing CLI/API consumers and strict bundle inventory semantics do not change. Portable protocol artifacts live in a deterministic companion directory named `<BUNDLE>.paired-change-v1`:

```text
BUNDLE/
  ... exact Evidence Bundle V1 inventory ...
BUNDLE.paired-change-v1/
  protocol-evidence.json
  claim-receipt.json   # created or checked by the claim verifier
```

`protocol-evidence.json` is materialized from the local run sidecar and binds the exact manifest digest inside `BUNDLE`. `claim-receipt.json` binds both portable inputs and is itself recomputable. The standalone verifier receives the bundle path and companion protocol path explicitly; protocol files are never inserted into the strict Evidence Bundle V1 directory.

For a qualification with a paired-change sidecar, export validates and prepares the protocol overlay before publication. Publication must fail closed rather than silently return a portable causal artifact with an unbound or mismatched overlay. Legacy qualifications with no sidecar continue to export only the V1 bundle.

The existing strict Evidence Bundle V1 inventory and schema remain unchanged. Legacy qualification artifacts remain valid for their existing consumers even when no protocol sidecar exists. A check-time sidecar without a later evidence export has no portable causal claim.

## Error model

Structural verification errors are separate from epistemic `UNRESOLVED` results.

Hard verification errors include malformed protocol JSON, unsupported protocol/schema versions, digest mismatch, qualification binding mismatch, contradictory pair layout, state binding contradiction, and a stored receipt that differs from recomputation.

Stable error categories should include `MALFORMED_PROTOCOL_EVIDENCE`, `UNSUPPORTED_PROTOCOL`, `EVIDENCE_BINDING_MISMATCH`, `STATE_BINDING_MISMATCH`, `PAIR_LAYOUT_MISMATCH`, `DIGEST_MISMATCH`, `MALFORMED_RECEIPT`, and `CLAIM_MISMATCH`.

Valid artifacts whose experiment lacks sufficient causal control return `UNRESOLVED` with condition reasons instead of a verifier error.

## Migration

Rollout is additive and ordered.

**Phase 1 — strengthen substrate.** Generalize support identity, switch the scheduler to balanced deterministic pairing, and capture pair timing/profile bindings. No causal claim is produced yet.

**Phase 2 — write prospective run evidence.** New qualifications persist `ProtocolDesignV1` and `PairedChangeRunV1` beside existing qualification artifacts. Missing dimensions remain explicitly unavailable rather than fabricated. The sidecar has no Evidence Bundle manifest digest and cannot itself yield a portable claim.

**Phase 3 — materialize portable protocol evidence during export.** `qualock evidence export` first creates Evidence Bundle V1, obtains its exact manifest digest, then derives `ProtocolEvidenceV1` from the local run sidecar and binds it to that manifest. The strict V1 bundle inventory is unchanged.

**Phase 4 — verify claims offline.** A pure verifier consumes standalone Evidence Bundle V1 plus portable protocol evidence, recomputes all ten conditions and per-canary claims, and writes or checks `ClaimReceiptV1`.

**Phase 5 — present attribution.** CLI/report surfaces may display current qualification verdict and attribution side-by-side. Existing exit codes, GitHub check conclusions, and `PASS/WARN/BLOCK/INCOMPLETE` enforcement remain unchanged in P0.

Legacy qualifications without a protocol overlay have **no causal claim available**. They are not retroactively labeled `UNRESOLVED`.

## Offline verification purity

The claim verifier must require no network, Docker, subprocess execution, provider SDK, agent resolver, credentials, or original project checkout.

It verifies what the artifacts justify: evidence consistency, frozen-design binding, protocol validity, and the deterministic claim transition. It does not prove that an unobserved external service was globally immutable or that a remote runtime was hardware-attested unless future evidence explicitly supplies such an attestation.

## Conformance tests

Protocol behavior is specified by golden vectors as well as unit tests. Identical canonical inputs must produce identical ordered conditions, reasons, claims, and canonical receipt bytes.

Required golden vectors include:

- clean attributable regression;
- clean no-regression result;
- unstable baseline;
- missing Codex support identity;
- unknown or mismatched preparation profile;
- reused isolation instance;
- invalid or same-orientation schedule;
- excessive/missing temporal evidence;
- unexpected controlled material change;
- opaque controlled material dimension;
- Evidence Bundle/protocol overlay binding mismatch;
- tampered stored receipt.

Every required gate has explicit `TRUE`, `FALSE`, and `UNKNOWN` truth-table tests where those states are structurally meaningful.

Required test groups:

- strict schema tests for unknown fields/enums, version mismatches, duplicate IDs, invalid digests, and inconsistent pair records;
- scheduler property tests for one slot per side/repetition, adjacency, determinism, alternating orientation, and balance;
- exhaustive claim-transition tests;
- adversarial vectors for shared mutable state, support drift, runtime/profile drift, missing attempts, manifest rebinding, changed canary fingerprint, and post-run protocol-design mutation;
- lifecycle tests proving check-time `PairedChangeRunV1` has no manifest binding, portable `ProtocolEvidenceV1` cannot be materialized before an Evidence Bundle V1 manifest exists, and the materialized overlay binds the exact exported manifest digest;
- backward-compatibility tests proving Evidence Bundle V1 parsing/verification and current verdict recomputation are unchanged;
- offline-purity tests that fail if the verifier attempts network, subprocess, Docker, resolver, provider, or source-checkout work;
- determinism tests proving repeated verification emits byte-identical canonical receipts.

No adversarial vector may yield `ATTRIBUTABLE_CHANGESET` unless every required invariant still legitimately holds.

## Backward compatibility

Evidence Bundle V1 keeps its exact required file inventory and `schema_version=1`. Existing bundle fixtures must continue to parse and verify unchanged.

Current qualification verdict policy, budget semantics, history, release monitor, PR qualification, and bisect semantics are not reinterpreted by P0.

The only intentional execution-order behavior change is replacing independent per-repetition orientation flips with deterministic balanced alternation while retaining adjacent A/B pairs and existing budget accounting.

## TDD sequence

Implementation proceeds red-to-green in this order:

1. generic support identity and legacy trust-gap tests;
2. balanced scheduler property tests;
3. strict protocol design/run-sidecar/protocol-evidence/receipt model tests;
4. qualification-side `PairedChangeRunV1` capture and writer tests;
5. evidence-export materialization and manifest-binding tests for `ProtocolEvidenceV1`;
6. individual gate truth tables;
7. claim transition tests;
8. offline verifier and receipt tests;
9. qualification/export integration and presentation tests;
10. full existing test suite and static checks;
11. fresh independent review with Critical/Important findings blocking closure.

No authenticated provider run is required to implement or review P0.

## Definition of done

P0 is complete when a synthetic/local deterministic qualification writes `PairedChangeRunV1`, a subsequent Evidence Bundle V1 export materializes manifest-bound `ProtocolEvidenceV1`, and the standalone verifier produces Claim Receipt V1, recomputes all ten required conditions as `TRUE`, and classifies a clean deterministic regression as `ATTRIBUTABLE_CHANGESET`.

A clean candidate that preserves the contract yields `NO_REGRESSION_OBSERVED`. Adversarial mutations deterministically degrade to `UNRESOLVED` or a stable structural verification error as appropriate.

The complete existing qualification/evidence suite must remain green, backward-compatible bundle verification must remain green, and an independent reviewer must report no unresolved Critical or Important findings.

## Follow-on work

Only after P0 is proven should QuaLock design `first-bad/v1`, requiring verified no-regression claims on every preceding release edge and a verified attributable boundary edge.

Compatibility ranges, public/private intelligence projections, component isolation, probabilistic protocols, standards exporters, signatures, and attestation integration remain separate later designs.
