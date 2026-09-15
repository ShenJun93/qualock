# First Bad V1 Causal Boundary Design

Date: 2026-09-15
Status: Approved design, implementation not started
Protocol: `first-bad/v1`
Depends on: `paired-change/v1`, Evidence Bundle V1, stable-release discovery

## Goal

Add a narrow, machine-verifiable protocol that proves the first attributable bad release inside one exact frozen release-catalog range.

`first-bad/v1` answers one question:

> Given a trusted starting release, a frozen ordered stable-release catalog, and portable causal evidence for every adjacent edge required to justify the claim, which release is the first one whose immediately preceding edge has a verified attributable change?

The protocol is stronger than the existing `qualock bisect` orchestration. Existing bisect uses qualification verdicts and can call a release first bad after an all-`PASS` prefix followed by `BLOCK`. `first-bad/v1` instead requires causal edge proofs from `paired-change/v1`.

The public claim is scoped to the carried catalog snapshot. It never means globally first bad across all releases that ever existed or may be published later.

## Core rule

A release `vN` may be classified as the first attributable bad release only when:

1. the frozen catalog declares the contiguous range `v0, v1, ... vN`;
2. every required adjacent edge exists exactly once;
3. every edge is independently re-verified from Evidence Bundle V1 plus `paired-change/v1` protocol evidence;
4. every edge before `vN-1 -> vN` resolves to `NO_REGRESSION_OBSERVED` for every canary;
5. the boundary edge resolves to at least one `ATTRIBUTABLE_CHANGESET` canary and no `UNRESOLVED` canary;
6. runtime identities chain exactly from each edge candidate to the next edge baseline;
7. suite, config, model, and protocol design bindings remain frozen across the chain.

An observed `BLOCK`, a stored receipt, or a version string alone is never sufficient.

## Non-goals

V1 does not add:

- component-level attribution inside a release changeset;
- probabilistic confidence or effect-size claims;
- compatibility-range synthesis;
- automatic mutation or promotion of the user's real `baseline.lock`;
- cached/reused authenticated provider attempts across edges;
- signatures, transparency logs, hardware attestation, or release signing;
- hosted history, dashboards, organization policy, or publishing;
- a replacement for existing `qualock bisect` semantics;
- a claim that the frozen release catalog is globally complete outside the declared snapshot.

## Architecture

The design is hybrid:

```text
stable-release catalog + isolated edge orchestration
                    |
                    v
       self-contained first-bad package
                    |
                    v
          first-bad/v1 offline verifier
                    |
                    v
            recomputable chain receipt
```

The proof core is offline and deterministic. Online/provider activity exists only in the orchestration layer that produces edge evidence.

The offline verifier must require no network, registry access, subprocess execution, Docker, resolver, provider SDK, credentials, or original project checkout.

## Relationship to existing version bisect

`qualock bisect` remains unchanged. Its current contract is an ordered forward scan that stops on the first `BLOCK` after an all-`PASS` prefix and stops unresolved on `WARN` or `INCOMPLETE`.

`first-bad/v1` must not reinterpret old bisect summaries as causal proof. A bisect summary may be useful human context but is not a protocol input.

A future CLI may expose:

```text
qualock first-bad codex@0.160.0
```

or the equivalent supported-agent form. That command is an orchestration client of the protocol, not the protocol verifier itself.

## Edge execution without mutating the real baseline

Current `execute_check()` is intentionally anchored to the trusted project `baseline.lock`, and Evidence Bundle V1 provenance binds the baseline lock digest. V1 does not widen that contract merely to run arbitrary adjacent edges.

Instead orchestration uses isolated project workspaces:

```text
real project baseline: v0
edge 000000 workspace: trusted v0 -> v1
edge 000001 workspace: locally established v1 -> v2
edge 000002 workspace: locally established v2 -> v3
```

The real project `baseline.lock` is never rewritten for the scan. For each later workspace, orchestration establishes the edge baseline through the existing baseline path and then requires that baseline runtime identity to exactly equal the preceding edge candidate identity.

This is intentionally more expensive than inventing a second qualification engine. Reuse of existing qualification and evidence semantics takes priority over minimizing provider calls in V1.

## Frozen release catalog

Before running any edge, orchestration freezes the ordered stable-release catalog for the selected agent and declared upper bound.

Requirements:

- exact stable versions only;
- no duplicates;
- strictly increasing numeric stable-version order;
- first element equals the trusted starting baseline version;
- last element equals the declared upper bound;
- carried edge packages must form a gap-free prefix of the frozen catalog; a first-bad proof may end at its attributable boundary, while a no-bad proof must cover every adjacent pair through the upper bound.

The chain binds the canonical ordered catalog tuple and its digest. New releases published after the freeze do not enter the chain, and the offline verifier never claims registry completeness beyond the carried snapshot.

The correct public wording is:

> first attributable bad release within this exact frozen release-catalog snapshot.

## Portable package layout

A completed or partially completed chain is self-contained:

```text
first-bad-<id>/
  chain-evidence.json
  chain-receipt.json                 # optional, recomputable
  edges/
    000000/
      bundle/                        # exact Evidence Bundle V1
      protocol/                      # paired-change/v1 companion
    000001/
      bundle/
      protocol/
```

Edge directory names are fixed-width zero-based decimal indices. The chain root inventory is exactly `chain-evidence.json`, optional `chain-receipt.json`, and `edges/`. Each carried edge directory contains exactly `bundle/` and `protocol/`. The package contains no absolute source-project paths and requires no original checkout after construction.

The edge `bundle/` inventory remains exactly Evidence Bundle V1. The edge `protocol/` inventory remains exactly the existing `paired-change/v1` companion contract. `first-bad/v1` does not insert files into either child format.

## Chain evidence model

`FirstBadChainEvidenceV1` is strict, frozen, and extra-forbid. It contains at minimum:

```text
protocol_id = first-bad/v1
schema_version = 1
agent_name
baseline_version
baseline_runtime_identity
upper_version
catalog_versions[]
catalog_sha256
suite_sha256
config_sha256
model_pin
protocol_design_sha256
edges[]
chain_sha256
```

Each `edges[]` record contains:

```text
index
baseline_version
candidate_version
baseline_runtime_identity
candidate_runtime_identity
bundle_manifest_sha256
protocol_evidence_sha256
```

A runtime identity includes agent name, version, binary SHA-256, and support SHA-256 when the agent identity contract supplies one. The model pin reuses the current model identity/snapshot/reasoning-effort semantics.

`chain-evidence.json` deliberately does not bind a stored paired-change claim receipt. Child receipts are optional recomputable caches and never a source of truth.

## Digest and continuity rules

All protocol JSON uses the existing canonical JSON encoding contract. `catalog_sha256` binds the ordered catalog tuple. `chain_sha256` is computed from the chain-evidence payload with its own field excluded.

Each edge binds the exact child Evidence Bundle manifest and canonical `protocol-evidence.json`. The implementation plan must choose one protocol-evidence digest representation and use it consistently across writer, verifier, fixtures, and tests.

For every edge `i`:

```text
edge[i].baseline_version == catalog[i]
edge[i].candidate_version == catalog[i + 1]
```

For every adjacent edge pair:

```text
edge[i].candidate_runtime_identity == edge[i + 1].baseline_runtime_identity
```

Equality is structural and includes binary/support identity, not only version text.

The top-level `baseline_runtime_identity` must exactly equal the first edge baseline identity and its version must equal `catalog_versions[0]`. Orchestration additionally requires that identity to equal the real project baseline before construction begins.

Offline verification proves that this anchor is internally bound to the portable evidence; without future signatures or attestation it does not prove who created the package or that an external project independently trusted that baseline. Later baseline locks are never trusted merely because an isolated workspace wrote them.

## Frozen experiment identity

Every child edge must agree on:

- agent name;
- suite digest;
- config digest;
- model pin;
- paired-change protocol-design digest.

A differing value is structural chain invalidity, not causal `UNRESOLVED`.

## Child-edge verification

For every edge, the first-bad verifier must invoke the standalone paired-change verifier or its pure internal equivalent over that edge's `bundle/` and `protocol/` inputs.

The verifier recomputes child claims from evidence. It must not trust qualification verdict alone, `claim-receipt.json`, chain declarations of child claim class, or orchestration summaries.

If a child stored receipt exists, normal paired-change verification checks it against recomputation. Any child structural verification failure becomes a structural first-bad verification failure with stable edge context; it is never normalized to causal `UNRESOLVED`.

## Per-canary aggregation

Paired-change claims are per canary. `first-bad/v1` derives one conservative edge result from all required canaries.

An edge is `NO_REGRESSION_OBSERVED` only when every required canary recomputes to `NO_REGRESSION_OBSERVED`.

An edge is attributable only when:

- at least one required canary is `ATTRIBUTABLE_CHANGESET`;
- no required canary is `UNRESOLVED`;
- every remaining canary is `NO_REGRESSION_OBSERVED` or `ATTRIBUTABLE_CHANGESET`.

Any required `UNRESOLVED` canary makes the global edge unresolved even when another canary is attributable.

## Chain conditions

The verifier emits these ordered chain-level conditions:

```text
CatalogBound
BaselineAnchored
EdgesContiguous
VersionsOrdered
SuiteFrozen
ConfigFrozen
DesignFrozen
EveryEdgeVerified
PrefixNoRegression
BoundaryAttributable
```

Condition status reuses `TRUE`, `FALSE`, and `UNKNOWN`. Structural corruption that prevents safe evaluation remains a verifier error rather than a condition status.

For a structurally verified carried edge whose causal classification is `UNRESOLVED`, `PrefixNoRegression` is `UNKNOWN` with the canonical reason `causal prefix contains an unresolved edge`. This prevents an unresolved edge from being represented as a proven no-regression prefix. A clean truncated prefix containing only verified no-regression edges keeps `PrefixNoRegression=TRUE` and uses `BoundaryAttributable=UNKNOWN`.

`EdgesContiguous=TRUE` means the carried edges form a gap-free catalog prefix. For `FIRST_ATTRIBUTABLE_BAD`, that prefix must end at the attributable boundary. For `NO_ATTRIBUTABLE_BAD_FOUND`, it must cover the full catalog through the declared upper bound. A missing internal edge, duplicate edge index, or skipped catalog release is `EDGE_LAYOUT_MISMATCH`; a cleanly truncated prefix may remain structurally valid but can yield only `UNRESOLVED`.

## Claim model

V1 has exactly three chain claim classes:

```text
FIRST_ATTRIBUTABLE_BAD
NO_ATTRIBUTABLE_BAD_FOUND
UNRESOLVED
```

`FIRST_ATTRIBUTABLE_BAD` is legal only when all ten chain conditions are `TRUE`. It records the earliest justified boundary edge index and candidate version.

`NO_ATTRIBUTABLE_BAD_FOUND` is legal only after complete verification of every adjacent edge through the declared upper bound and only when every edge is a verified no-regression edge. A partial scan never yields this claim.

`UNRESOLVED` is used only for structurally valid evidence that cannot justify either stronger claim, including a child causal `UNRESOLVED` or an incomplete-yet-valid chain.

No path from `UNKNOWN` to `FIRST_ATTRIBUTABLE_BAD` exists.

## Chain receipt

`FirstBadReceiptV1` contains only recomputable results:

```text
schema_version
protocol_id
chain_sha256
catalog_sha256
ordered chain conditions
ordered edge summaries
claim
boundary_version, optional
boundary_edge_index, optional
```

Edge summaries may record recomputed per-canary child claims and the derived edge classification.

`chain-receipt.json` is optional. If present, it must exactly equal the newly recomputed receipt. A write operation verifies first, creates the canonical receipt with create-new semantics, and never overwrites an existing file.

## Stable structural error taxonomy

The verifier exposes these stable categories:

```text
MALFORMED_CHAIN_EVIDENCE
UNSUPPORTED_CHAIN_PROTOCOL
CATALOG_BINDING_MISMATCH
BASELINE_BINDING_MISMATCH
EDGE_LAYOUT_MISMATCH
EDGE_BINDING_MISMATCH
DIGEST_MISMATCH
MALFORMED_CHAIN_RECEIPT
CHAIN_CLAIM_MISMATCH
```

Child verifier failures are surfaced as `EDGE_BINDING_MISMATCH` at the first-bad boundary with a fixed edge label/index. Diagnostics may retain the child reason in bounded technical detail, but attacker-controlled raw text must not become an unbounded exception label.

## Offline verifier

The public pure verification entry point is conceptually:

```python
verify_first_bad(chain_path: Path) -> FirstBadReceiptV1
```

Verification order:

1. securely open and validate the chain root;
2. bounded-read and parse `chain-evidence.json`;
3. validate protocol/schema/catalog/digests;
4. validate fixed package inventory and edge layout;
5. validate catalog order and edge adjacency;
6. verify every child Evidence Bundle V1;
7. verify every child paired-change protocol and recompute child receipts;
8. validate runtime-identity continuity and frozen experiment identity;
9. derive ordered chain conditions and edge classifications;
10. derive claim and optional boundary;
11. canonicalize the recomputed chain receipt;
12. compare any stored `chain-receipt.json` exactly.

Structural failures abort verification. Valid causal insufficiency returns `UNRESOLVED`.

## Secure filesystem contract

The package is untrusted filesystem input. Implementation must reuse or generalize the hardened posture already required by Evidence Bundle V1 and `paired-change/v1`:

- open the chain root without following symlinks/reparse points;
- retain a stable root identity for the verification transaction;
- reject symlink/reparse/non-regular protocol files and unsafe directory substitutions;
- use bounded reads;
- reject unexpected chain-root and edge-root inventory;
- reject path traversal and absolute paths in chain metadata;
- bound edge count, catalog count, text lengths, and JSON bytes;
- verify child bundle and protocol digests after secure location resolution;
- use create-new, no-overwrite semantics for a stored chain receipt;
- never delete an unverified path during rollback/error handling.

Windows must receive the same race/reparse scrutiny already applied to paired-change companion I/O.

## Orchestration lifecycle

The orchestration layer may use release discovery, resolvers, Docker/host backends, credentials, and existing qualification commands because it produces evidence rather than verifying it offline.

Lifecycle:

1. load and validate the real project's trusted baseline;
2. freeze the stable release catalog through the exact upper bound;
3. create a private first-bad staging directory;
4. for each adjacent catalog edge, prepare an isolated project workspace;
5. establish the edge baseline through existing baseline semantics;
6. require first-edge baseline equality with the real baseline and later-edge equality with the prior candidate identity;
7. run the candidate through existing paired qualification execution;
8. export Evidence Bundle V1 plus paired-change companion directly into the staged edge directory;
9. immediately run offline paired-change verification;
10. stop on unresolved or attributable boundary; otherwise continue;
11. materialize canonical `chain-evidence.json` from frozen catalog and accepted edge metadata;
12. run `verify_first_bad()` on the staged package;
13. publish the completed package atomically with no-overwrite semantics.

A crash may preserve private staging evidence for diagnosis, but no incomplete public package may be presented as completed causal proof.

## Baseline workspace semantics

The user's real baseline is immutable during first-bad orchestration.

A later-edge baseline is accepted only after normal baseline execution succeeds, resolved binary/support/model identity exactly matches the prior edge candidate identity, suite/config identity remains frozen, and paired-change design identity remains frozen.

If a later baseline cannot be stably established, the chain becomes `UNRESOLVED` and the scan stops. The orchestrator must not skip that version.

## CLI surfaces

### Orchestration

Proposed command:

```text
qualock first-bad <agent>@<exact-stable-upper>
```

The agent must match the trusted project baseline and an orchestration capability gate. Unsupported agents fail before catalog discovery or qualification.

Progress output is low-tech and edge-oriented. An unresolved edge explicitly states that no first-attributable-bad claim can be made beyond it.

### Offline verification

Proposed command:

```text
qualock evidence verify-first-bad CHAIN_DIR [--write-receipt]
```

Read-only is the default. Existing receipts are automatically checked. `--write-receipt` verifies first and creates a receipt without overwrite.

## Exit behavior

- `0`: full declared range verified with `NO_ATTRIBUTABLE_BAD_FOUND`;
- `2`: verified `FIRST_ATTRIBUTABLE_BAD`;
- `4`: valid but `UNRESOLVED` or incomplete causal chain;
- `3`: stable input/config/verification structural error;
- `1`: unexpected internal failure.

Existing `qualock bisect` exit behavior remains unchanged.

## Publication and roadmap boundaries

Implementing `first-bad/v1` does not authorize authenticated provider traffic, evidence publication, roadmap delivery claims, tag/release, package publishing, push, PR, or merge.

The existing roadmap item requiring a published reproducible real-world regression remains governed by its own evidence/publication authorization. This spec does not update `README.md` or `ROADMAP.md`.

## Conformance vectors

Minimum golden vectors:

1. `no-bad-full-range`;
2. `first-bad-first-edge`;
3. `first-bad-middle`;
4. `multiple-canaries-one-attributable`;
5. `unresolved-prefix`;
6. `unresolved-boundary`;
7. `missing-edge`;
8. `skipped-catalog-release`;
9. `reordered-or-duplicate-catalog`;
10. `cross-edge-binary-identity-mismatch`;
11. `support-identity-mismatch`;
12. `suite-config-design-drift`;
13. `tampered-child-bundle`;
14. `tampered-paired-change-evidence`;
15. `tampered-chain-receipt`;
16. `unexpected-inventory-or-symlink`.

Identical canonical package inputs must produce identical ordered conditions, reasons, edge summaries, claim, boundary, and canonical receipt bytes.

No adversarial vector may yield `FIRST_ATTRIBUTABLE_BAD` unless all ten required chain conditions are legitimately `TRUE`.

## Test requirements

Implementation tests must cover strict models/digests, catalog adjacency, baseline anchoring, cross-edge runtime identity, frozen experiment identity, per-canary aggregation, child error propagation, claim transitions, receipt tamper, POSIX/Windows untrusted-filesystem behavior, original-project deletion, deterministic fake E2E orchestration, backward compatibility, golden vectors, full verification, and fresh independent review.

No authenticated provider run is required for implementation acceptance.

## Implementation boundaries

Expected new subsystem ownership:

```text
src/qualock/protocols/first_bad/
```

Likely modules are `models.py`, `io.py`, `verify.py`, `render.py`, and `orchestrate.py`. Existing modules may be modified only for narrow reusable interfaces or CLI wiring. The implementation plan must prefer reuse over duplicating Evidence Bundle or paired-change verification logic.

Compatibility boundaries are:

- `execute_check()` semantics;
- qualification verdict policy;
- Evidence Bundle V1 inventory;
- paired-change claim semantics;
- existing `qualock bisect` behavior;
- real project baseline immutability during first-bad orchestration.

## Definition of done

`first-bad/v1` P0 is complete when a deterministic local synthetic scan over at least three adjacent release identities:

1. freezes an ordered catalog;
2. constructs isolated edge evidence without mutating the real baseline;
3. produces self-contained Evidence Bundle V1 plus paired-change evidence for each required edge;
4. proves an all-no-regression chain as `NO_ATTRIBUTABLE_BAD_FOUND`;
5. proves a chain with an attributable middle boundary as `FIRST_ATTRIBUTABLE_BAD` at the earliest justified release;
6. deletes the original project checkout and reproduces the same receipt offline;
7. fails closed or returns `UNRESOLVED` for every required adversarial vector;
8. leaves existing bisect, qualification, evidence, and paired-change suites backward-compatible;
9. passes fresh repository verification apart from explicitly adjudicated pre-existing toolchain debt;
10. receives fresh independent review with zero unresolved Critical or Important findings.

## Follow-on work

Only after `first-bad/v1` is proven should QuaLock consider compatibility-range synthesis over multiple verified chains, component isolation, probabilistic causal protocols, signed catalog snapshots, standards exporters, or attestation integration.
