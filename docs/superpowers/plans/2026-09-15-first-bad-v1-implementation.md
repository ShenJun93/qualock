# First Bad V1 Causal Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `first-bad/v1`: a self-contained, offline-verifiable proof of the first attributable bad release inside one exact frozen stable-release catalog snapshot.

**Architecture:** Preserve existing qualification, Evidence Bundle V1, paired-change/v1, and `qualock bisect` semantics. Add reader-neutral child verification APIs first, then a new `qualock.protocols.first_bad` subsystem with strict models, secure hierarchical package I/O, deterministic chain verification, isolated edge orchestration, and two CLI surfaces.

**Tech Stack:** Python 3.14, Pydantic v2, Typer, existing QuaLock Evidence Bundle V1 and paired-change/v1 primitives, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-15-first-bad-v1-design.md`

## Global Constraints

- Base implementation on commit `ff08965` or a descendant containing that exact approved spec.
- Do not change current `PASS/WARN/BLOCK/INCOMPLETE` qualification semantics.
- Do not change Evidence Bundle V1 inventory or paired-change/v1 claim semantics.
- Do not change `qualock bisect` behavior or output semantics.
- Never rewrite the user's real `.qualock/baseline.lock` during first-bad orchestration.
- Offline first-bad verification must use no network, registry, subprocess, Docker, resolver, credentials, provider SDK, or original project checkout.
- No dependency install. Existing PyYAML-stub mypy debt may be adjudicated only if unchanged; do not install stubs.
- No authenticated provider qualification during implementation or review.
- No push, PR, merge, tag, release, publish, or ROADMAP delivery claim without separate explicit authorization.
- TDD is mandatory. Every accepted implementation fix begins with a failing regression test.
- Every task ends with a fresh independent review; any Critical or Important finding blocks task closure.
- Keep review/probe output in `/tmp`, not as repo scratch files.
- Use `/home/pacmap/qualock-easy/.venv/bin/python`, `/home/pacmap/qualock-easy/.venv/bin/ruff`, and `/home/pacmap/qualock-easy/.venv/bin/mypy`.

---

## File Structure

New subsystem:

- `src/qualock/protocols/first_bad/__init__.py` — package marker/public exports only if required.
- `src/qualock/protocols/first_bad/models.py` — strict chain evidence, conditions, edge summaries, receipts, orchestration outcome types.
- `src/qualock/protocols/first_bad/fingerprint.py` — canonical catalog/chain digest helpers.
- `src/qualock/protocols/first_bad/io.py` — untrusted hierarchical package snapshot reads and create-new chain receipt writes.
- `src/qualock/protocols/first_bad/claims.py` — pure per-edge aggregation, ordered chain conditions, and claim transition.
- `src/qualock/protocols/first_bad/verify.py` — structural binding plus child verification over an immutable package snapshot.
- `src/qualock/protocols/first_bad/render.py` — low-tech offline/orchestration rendering.
- `src/qualock/protocols/first_bad/orchestrate.py` — preflight, frozen catalog, isolated edge workspaces, package staging/publication.

Narrow existing modifications:

- `src/qualock/evidence/verify.py` — add bytes/payload verification API; preserve path API.
- `src/qualock/protocols/paired_change/verify.py` — add payload verification details API; preserve path API.
- `src/qualock/agents/orchestration.py` — add explicit first-bad orchestration capability.
- `src/qualock/cli.py` — wire `first-bad` and `evidence verify-first-bad` only.

Tests:

- `tests/unit/test_evidence_verify.py`
- `tests/unit/test_paired_change_verify.py`
- `tests/unit/test_first_bad_models.py`
- `tests/unit/test_first_bad_claims.py`
- `tests/unit/test_first_bad_io.py`
- `tests/unit/test_first_bad_verify.py`
- `tests/unit/test_first_bad_orchestrate.py`
- `tests/unit/test_first_bad_cli.py`
- `tests/unit/test_first_bad_conformance.py`
- `tests/integration/test_first_bad_e2e.py`
- `tests/fixtures/first_bad_v1/`

## Execution Preflight

Before Task 1, create an isolated implementation worktree using `superpowers:using-git-worktrees`, rooted at `ff08965`, and create only one SDD ledger file under `.superpowers/sdd/2026-09-15-first-bad-v1-implementation/progress.md` if the execution skill requires it.

Run:

```bash
git status --short
git rev-parse HEAD
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
```

Expected: clean worktree, approved spec present, full pytest green. Record any pre-existing Ruff/mypy debt before changing code so later tasks do not absorb unrelated cleanup.

### Task 1: Add Reader-Neutral Child Verification APIs

**Files:**
- Modify: `src/qualock/evidence/verify.py`
- Modify: `src/qualock/protocols/paired_change/verify.py`
- Test: `tests/unit/test_evidence_verify.py`
- Test: `tests/unit/test_paired_change_verify.py`

**Interfaces:**
- Produces: `verify_evidence_bundle_payloads(files: Mapping[str, bytes]) -> VerifiedEvidenceBundle`.
- Produces: immutable `VerifiedPairedChangeV1(bundle, evidence, receipt, protocol_evidence_sha256)`.
- Produces: `verify_paired_change_payloads(bundle_files, protocol_evidence_bytes, claim_receipt_bytes) -> VerifiedPairedChangeV1`.
- Produces: `verify_paired_change_details(bundle_path: Path, protocol_path: Path) -> VerifiedPairedChangeV1`; existing `verify_paired_change()` returns `details.receipt`.
- Existing `verify_evidence_bundle(Path)` and `verify_paired_change(Path, Path)` must delegate to these cores and preserve exact public behavior/error taxonomy.

- [ ] **Step 1: Write RED payload-equivalence tests for Evidence Bundle V1**

Add tests that load an existing valid fixture into `dict[str, bytes]`, call `verify_evidence_bundle_payloads()`, and assert it equals `verify_evidence_bundle(path)`. Mutate manifest/payload bytes and require the same `EvidenceBundleReason` as the path API.

```python
files = {path.name: path.read_bytes() for path in bundle.iterdir()}
assert verify_evidence_bundle_payloads(files) == verify_evidence_bundle(bundle)
```

- [ ] **Step 2: Run the focused evidence tests and observe RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_evidence_verify.py -q
```

Expected: fail because `verify_evidence_bundle_payloads` does not exist.

- [ ] **Step 3: Extract a pure payload verifier without changing policy logic**

`verify_evidence_bundle(root)` must perform only filesystem snapshot acquisition, then call the new payload function. The new function parses `manifest.json`, verifies exact inventory/digests, parses all public models, and reuses the existing identity/canary/policy/completeness checks. For pricing, pass a non-semantic placeholder `Path(".")` only after a regression test proves `parse_pricing_sidecar_payload()` does not read that path.

- [ ] **Step 4: Write RED paired-change payload/detail tests**

Use a current paired-change golden vector. Load its bundle files and protocol files as bytes. Require the new API to return both recomputed receipt and parsed protocol evidence, and require stored receipt mismatch to preserve `CLAIM_MISMATCH`.

```python
verified = verify_paired_change_payloads(
    bundle_files,
    (protocol / "protocol-evidence.json").read_bytes(),
    None,
)
assert verified.receipt == verify_paired_change(bundle, protocol)
assert verified.evidence.candidate_state.version == expected_version
```

- [ ] **Step 5: Implement the paired-change pure payload API**

The implementation must parse protocol bytes with the existing duplicate-canary/pair-layout preflight rules, call `verify_evidence_bundle_payloads()`, run the existing protocol identity/evidence/state/pair checks once, build one receipt, and compare optional stored receipt bytes against that recomputation. `verify_paired_change_details()` performs the current secure path reads once and delegates to this core; `verify_paired_change()` becomes the compatibility wrapper returning `details.receipt`.

- [ ] **Step 6: Prove path APIs are unchanged**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_evidence_verify.py tests/unit/test_paired_change_verify.py tests/unit/test_paired_change_io.py tests/unit/test_paired_change_cli.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/evidence/verify.py src/qualock/protocols/paired_change/verify.py tests/unit/test_evidence_verify.py tests/unit/test_paired_change_verify.py
git diff --check
```

Expected: PASS; no new public-path behavior changes.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/qualock/evidence/verify.py src/qualock/protocols/paired_change/verify.py \
  tests/unit/test_evidence_verify.py tests/unit/test_paired_change_verify.py
git commit -m "refactor: expose payload verification cores"
```

- [ ] **Step 8: Fresh independent review**

Review the Task 1 commit against compatibility boundaries. Require zero Critical/Important findings before Task 2. In particular verify that no filesystem validation was silently removed from the existing path APIs and no stored verdict/claim is newly trusted.

### Task 2: Define First-Bad Models, Digests, and Pure Claim Semantics

**Files:**
- Create: `src/qualock/protocols/first_bad/__init__.py`
- Create: `src/qualock/protocols/first_bad/models.py`
- Create: `src/qualock/protocols/first_bad/fingerprint.py`
- Create: `src/qualock/protocols/first_bad/claims.py`
- Test: `tests/unit/test_first_bad_models.py`
- Test: `tests/unit/test_first_bad_claims.py`

**Interfaces:**
- Reuse `AgentDependencyStateV1`, `ModelDeclarationV1`, `ConditionStatus`, and `Sha256Hex` from paired-change models.
- Produces `FirstBadClaimClass`, `FirstBadConditionType`, `EdgeClassification`, `FirstBadChainEvidenceV1`, `FirstBadReceiptV1`.
- Produces `digest_catalog(versions: tuple[str, ...]) -> str` and `digest_chain_evidence(evidence: FirstBadChainEvidenceV1) -> str` with self-field exclusion.

Model shapes are binding for later tasks:

```python
class FirstBadEdgeEvidenceV1(StrictModel):
    index: int = Field(ge=0)
    baseline_version: str = Field(min_length=1, max_length=128)
    candidate_version: str = Field(min_length=1, max_length=128)
    baseline_runtime_identity: AgentDependencyStateV1
    candidate_runtime_identity: AgentDependencyStateV1
    bundle_manifest_sha256: Sha256Hex
    protocol_evidence_sha256: Sha256Hex

class FirstBadChainEvidenceV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["first-bad/v1"]
    agent_name: str = Field(min_length=1, max_length=64)
    baseline_version: str = Field(min_length=1, max_length=128)
    baseline_runtime_identity: AgentDependencyStateV1
    upper_version: str = Field(min_length=1, max_length=128)
    catalog_versions: tuple[str, ...] = Field(min_length=2, max_length=256)
    catalog_sha256: Sha256Hex
    suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    model_pin: ModelDeclarationV1
    protocol_design_sha256: Sha256Hex
    edges: tuple[FirstBadEdgeEvidenceV1, ...] = Field(min_length=1, max_length=255)
    chain_sha256: Sha256Hex
```

Receipt/condition shapes:

```python
class FirstBadConditionType(str, Enum):
    CATALOG_BOUND = "CatalogBound"
    BASELINE_ANCHORED = "BaselineAnchored"
    EDGES_CONTIGUOUS = "EdgesContiguous"
    VERSIONS_ORDERED = "VersionsOrdered"
    SUITE_FROZEN = "SuiteFrozen"
    CONFIG_FROZEN = "ConfigFrozen"
    DESIGN_FROZEN = "DesignFrozen"
    EVERY_EDGE_VERIFIED = "EveryEdgeVerified"
    PREFIX_NO_REGRESSION = "PrefixNoRegression"
    BOUNDARY_ATTRIBUTABLE = "BoundaryAttributable"

class FirstBadClaimClass(str, Enum):
    FIRST_ATTRIBUTABLE_BAD = "FIRST_ATTRIBUTABLE_BAD"
    NO_ATTRIBUTABLE_BAD_FOUND = "NO_ATTRIBUTABLE_BAD_FOUND"
    UNRESOLVED = "UNRESOLVED"
```

```python
class EdgeClassification(str, Enum):
    NO_REGRESSION_OBSERVED = "NO_REGRESSION_OBSERVED"
    ATTRIBUTABLE_CHANGESET = "ATTRIBUTABLE_CHANGESET"
    UNRESOLVED = "UNRESOLVED"

class FirstBadConditionV1(StrictModel):
    type: FirstBadConditionType
    status: ConditionStatus
    reason: str = Field(min_length=1, max_length=512)

class FirstBadCanarySummaryV1(StrictModel):
    canary_id: str = Field(min_length=1, max_length=256)
    claim: ClaimClass

class FirstBadEdgeSummaryV1(StrictModel):
    index: int = Field(ge=0)
    baseline_version: str = Field(min_length=1, max_length=128)
    candidate_version: str = Field(min_length=1, max_length=128)
    canaries: tuple[FirstBadCanarySummaryV1, ...]
    classification: EdgeClassification

class FirstBadReceiptV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["first-bad/v1"]
    chain_sha256: Sha256Hex
    catalog_sha256: Sha256Hex
    conditions: tuple[FirstBadConditionV1, ...]
    edges: tuple[FirstBadEdgeSummaryV1, ...]
    claim: FirstBadClaimClass
    boundary_version: str | None = Field(default=None, max_length=128)
    boundary_edge_index: int | None = Field(default=None, ge=0)
```

Canary summaries are sorted by `canary_id`; edge summaries are sorted by `index`. Model validators reject duplicate canary summary ids and duplicate edge summary indices but do not derive claims or trust stored semantic consistency.

- [ ] **Step 1: Write RED strict-model and digest tests**

Cover extra-field rejection, byte/text bounds, 256-version/255-edge bounds, self-excluding `chain_sha256`, ordered catalog digest, and deterministic canonical values. Semantic catalog errors such as duplicate/reordered versions must remain verifier taxonomy, not Pydantic parse failures.

- [ ] **Step 2: Run model tests to observe RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_models.py -q
```

Expected: import/module failures because the subsystem does not yet exist.

- [ ] **Step 3: Implement strict models and digest helpers**

`digest_catalog()` uses existing canonical JSON hashing over the ordered tuple. `digest_chain_evidence()` computes the canonical digest of `evidence.model_dump(mode="json", exclude={"chain_sha256"})`; do not hash a pre-existing `chain_sha256` value.

- [ ] **Step 4: Write RED edge/chain transition tests**

Required pure transitions:

```python
assert derive_edge_classification(all_no_regression) is EdgeClassification.NO_REGRESSION_OBSERVED
assert derive_edge_classification(one_attributable_no_unresolved) is EdgeClassification.ATTRIBUTABLE_CHANGESET
assert derive_edge_classification(one_attributable_one_unresolved) is EdgeClassification.UNRESOLVED
```

Also require all-no-regression full coverage -> `NO_ATTRIBUTABLE_BAD_FOUND`; no-regression prefix + attributable boundary -> `FIRST_ATTRIBUTABLE_BAD`; clean truncated no-regression prefix -> `UNRESOLVED`; unresolved before any later attributable edge -> `UNRESOLVED`.

- [ ] **Step 5: Implement pure claim semantics and ordered summaries**

Expose `derive_edge_summary(index, baseline_version, candidate_version, receipt)` and `derive_chain_claim(catalog_versions, edge_summaries)`. The latter assumes structural adjacency was already validated and returns `(claim, boundary_version, boundary_edge_index)`.

- [ ] **Step 6: Verify Task 2**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_models.py tests/unit/test_first_bad_claims.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad tests/unit/test_first_bad_models.py tests/unit/test_first_bad_claims.py
git diff --check
```

- [ ] **Step 7: Commit and fresh-review Task 2**

```bash
git add src/qualock/protocols/first_bad tests/unit/test_first_bad_models.py tests/unit/test_first_bad_claims.py
git commit -m "feat: model first-bad causal chains"
```

Review must verify exact enum vocabulary, deterministic ordering, no semantic error prematurely collapsed into Pydantic parse failure, and no path/network side effects in claim logic.

### Task 3: Build Secure Hierarchical First-Bad Package I/O

**Files:**
- Create: `src/qualock/protocols/first_bad/io.py`
- Test: `tests/unit/test_first_bad_io.py`

**Interfaces:**
- Produces `FirstBadVerificationReason` and `FirstBadVerificationError` with the nine exact spec categories.
- Produces immutable `EdgePackageSnapshot(bundle_files, protocol_evidence_bytes, claim_receipt_bytes)`.
- Produces immutable `FirstBadPackageSnapshot(evidence, evidence_bytes, stored_receipt, edges)`.
- Produces `read_first_bad_package(chain_path: Path) -> FirstBadPackageSnapshot`.
- Produces `write_first_bad_receipt(chain_path: Path, receipt: FirstBadReceiptV1) -> Path` with create-new/no-overwrite semantics.

Exact reasons:

```python
class FirstBadVerificationReason(str, Enum):
    MALFORMED_CHAIN_EVIDENCE = "malformed_chain_evidence"
    UNSUPPORTED_CHAIN_PROTOCOL = "unsupported_chain_protocol"
    CATALOG_BINDING_MISMATCH = "catalog_binding_mismatch"
    BASELINE_BINDING_MISMATCH = "baseline_binding_mismatch"
    EDGE_LAYOUT_MISMATCH = "edge_layout_mismatch"
    EDGE_BINDING_MISMATCH = "edge_binding_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED_CHAIN_RECEIPT = "malformed_chain_receipt"
    CHAIN_CLAIM_MISMATCH = "chain_claim_mismatch"
```

Security constants: chain JSON max 4 MiB, receipt max 4 MiB, catalog max 256, edge dirs max 255; child bundle/protocol reads use existing `FILE_MAX_BYTES`, `PROTOCOL_EVIDENCE_MAX_BYTES`, and `CLAIM_RECEIPT_MAX_BYTES` rather than new larger limits.

- [ ] **Step 1: Write POSIX RED tests for fixed inventory and no-follow traversal**

Cover root symlink, symlinked `chain-evidence.json`, symlinked `edges`, symlinked edge directory, symlinked child `bundle`/`protocol`, FIFO/non-regular file, unexpected root entry, unexpected edge entry/name, oversized JSON, missing internal edge directory, and root/edge replacement during a read transaction.

- [ ] **Step 2: Run POSIX IO tests to observe RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_io.py -q
```

- [ ] **Step 3: Implement one pinned POSIX directory-tree transaction**

Keep the chain root fd open for the entire snapshot. Open `edges`, each numeric edge directory, `bundle`, and `protocol` relative to already-open parent fds using `dir_fd` + `O_DIRECTORY` + `O_NOFOLLOW`; open files relative to the pinned child fd using `O_NOFOLLOW`. Never reconstruct a trusted child by joining an attacker-replaceable pathname after pinning.

Pseudo-interface inside `io.py`:

```python
class _PinnedTree:
    def open_dir(self, parent_fd: int, name: str) -> int: ...
    def read_file(self, parent_fd: int, name: str, *, max_bytes: int) -> bytes: ...
    def names(self, directory_fd: int) -> tuple[str, ...]: ...
```

Inventory must be read from the pinned directory identity, not a newly opened pathname. Close every fd in reverse order even on parse/verification failure.

- [ ] **Step 4: Write Windows-specific RED tests**

Monkeypatch Win32/NT calls to require pointer-safe signatures and root-relative child opens. Add native-Windows tests for reparse rejection, ancestor rename blocking while the chain root handle is held, nested edge replacement rejection, and create-new receipt behavior.

- [ ] **Step 5: Implement Windows pinned-tree reads/writes**

Use `CreateFileW(... FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, FILE_SHARE_READ)` for the root and `NtCreateFile` with `OBJECT_ATTRIBUTES.RootDirectory` for nested directories/files. Reject `FILE_ATTRIBUTE_REPARSE_POINT`; use final-path/handle metadata as a secondary containment check. Enumerate directory inventory from the already-open directory HANDLE using `NtQueryDirectoryFile` or an equivalent handle-based API; never fall back to `os.scandir(path)` for Windows chain inventory. Configure `argtypes/restype` explicitly and load kernel32 via `ctypes.WinDLL(..., use_last_error=True)`.

- [ ] **Step 6: Parse only after safe snapshot acquisition**

`read_first_bad_package()` first bounded-reads raw chain JSON under the pinned root. Before Pydantic parsing, a small raw-JSON preflight maps explicit non-v1 `schema_version` or `protocol_id` to `UNSUPPORTED_CHAIN_PROTOCOL`; malformed JSON/schema maps to `MALFORMED_CHAIN_EVIDENCE`. It then acquires the entire carried child package under pinned identities and parses the optional `FirstBadReceiptV1`. Edge directory count/names must agree with the carried `edges[]` indices. Child payload bytes remain untrusted until Task 4 verifies them.

- [ ] **Step 7: Implement create-new receipt write**

Canonicalize with `canonical_json_file_bytes()`. Verify caller already supplied a recomputed receipt; `write_first_bad_receipt()` only performs secure no-overwrite creation. Existing `chain-receipt.json`, symlink/reparse entry, or unexpected root inventory maps to stable first-bad verification error and must never be overwritten/deleted.

- [ ] **Step 8: Verify on WSL and native Windows**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_io.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad/io.py tests/unit/test_first_bad_io.py
git diff --check
```

Also run the same test file with the available native Windows Python. Record platform-specific skip counts; ordinary logic tests must not become Windows-only.

- [ ] **Step 9: Commit and fresh-review Task 3**

```bash
git add src/qualock/protocols/first_bad/io.py tests/unit/test_first_bad_io.py
git commit -m "feat: secure first-bad package io"
```

Reviewer must focus on POSIX rename races, Windows ancestor/reparse behavior, nested root-relative access, bounded reads, fixed inventories, and no unsafe rollback deletion.

### Task 4: Implement the Offline Chain Verifier

**Files:**
- Create: `src/qualock/protocols/first_bad/verify.py`
- Test: `tests/unit/test_first_bad_verify.py`

**Interfaces:**
- Produces `verify_first_bad_snapshot(snapshot: FirstBadPackageSnapshot) -> FirstBadReceiptV1` as the pure structural/causal core.
- Produces `verify_first_bad(chain_path: Path) -> FirstBadReceiptV1` as the path wrapper calling `read_first_bad_package()` exactly once.
- Consumes only Task 1 payload APIs, Task 2 models/claims/digests, and Task 3 immutable snapshots.

- [ ] **Step 1: Write RED catalog/digest/layout taxonomy tests**

Require exact errors for wrong protocol/schema, catalog digest mismatch, chain digest mismatch, duplicate/reordered/non-stable catalog, wrong baseline/upper endpoints, duplicate/missing/skipped edge index, edge versions not equal to `catalog[i] -> catalog[i+1]`, and an edge carried after the earliest attributable boundary.

Exact mapping: wrong protocol/schema -> `UNSUPPORTED_CHAIN_PROTOCOL`; catalog or chain SHA mismatch -> `DIGEST_MISMATCH`; duplicate/reordered/non-stable catalog or baseline/upper endpoint disagreement -> `CATALOG_BINDING_MISMATCH`; missing/duplicate/skipped edge layout or edge versions not equal to the frozen catalog adjacency -> `EDGE_LAYOUT_MISMATCH`.

- [ ] **Step 2: Write RED child binding tests**

For each edge, mutate separately: bundle manifest digest declaration, protocol-evidence digest declaration, baseline runtime identity, candidate runtime identity, cross-edge candidate/baseline identity, suite digest, config digest, model pin, and protocol-design digest. Declared bundle/protocol digest disagreements map to `DIGEST_MISMATCH`; top anchor disagreement maps to `BASELINE_BINDING_MISMATCH`; cross-edge runtime or frozen suite/config/model/design disagreements map to `EDGE_BINDING_MISMATCH`.

- [ ] **Step 3: Implement deterministic structural verification order**

Use this exact order so one malformed artifact maps deterministically:

1. protocol/schema preflight;
2. catalog/chain digest checks;
3. catalog syntax/order/endpoints;
4. fixed carried-edge layout;
5. child payload verification;
6. declared child digest bindings;
7. top baseline anchor;
8. cross-edge runtime continuity;
9. suite/config/model/design freeze;
10. edge aggregation and chain claim.

For each child call `verify_paired_change_payloads()` exactly once. Convert any `EvidenceBundleError` or `PairedChangeVerificationError` to `EDGE_BINDING_MISMATCH` with fixed field text such as `edge[000003]`; preserve child reason only in a bounded diagnostic attribute/string, never in the stable reason enum or unbounded label.

- [ ] **Step 4: Write RED condition/claim tests**

Assert ordered condition names exactly match the spec. Required outputs:

- complete all-no-regression chain: first eight conditions `TRUE`, `PrefixNoRegression=TRUE`, `BoundaryAttributable=FALSE`, claim `NO_ATTRIBUTABLE_BAD_FOUND`;
- attributable first edge: all ten `TRUE`, boundary index `0`;
- no-regression prefix then attributable edge: all ten `TRUE`, earliest boundary returned;
- clean truncated all-no-regression prefix: structural conditions `TRUE`, `PrefixNoRegression=TRUE`, `BoundaryAttributable=UNKNOWN`, claim `UNRESOLVED`;
- unresolved carried edge: `EveryEdgeVerified=TRUE`, `PrefixNoRegression=UNKNOWN`, causal boundary condition `UNKNOWN`, claim `UNRESOLVED`.

Condition reason text is protocol output and therefore binding for golden vectors. Use exactly:

```text
CatalogBound TRUE: catalog digest and declared range are bound
BaselineAnchored TRUE: first edge baseline matches the chain anchor
EdgesContiguous TRUE: carried edges form a contiguous catalog prefix
VersionsOrdered TRUE: catalog versions are strictly increasing stable releases
SuiteFrozen TRUE: suite identity is frozen across carried edges
ConfigFrozen TRUE: config identity is frozen across carried edges
DesignFrozen TRUE: model and paired-change design are frozen across carried edges
EveryEdgeVerified TRUE: all carried edges passed structural paired-change verification
PrefixNoRegression TRUE: all edges before any attributable boundary are verified no-regression
PrefixNoRegression UNKNOWN: causal prefix contains an unresolved edge
BoundaryAttributable TRUE: terminal edge is an attributable boundary
BoundaryAttributable FALSE: complete range contains no attributable boundary
BoundaryAttributable UNKNOWN: carried prefix does not justify an attributable boundary
```

- [ ] **Step 5: Implement receipt construction and stored-receipt comparison**

Construct edge summaries from recomputed child receipts only. Construct the chain receipt from verified evidence plus those summaries. If `snapshot.stored_receipt` exists and differs by any field, raise `CHAIN_CLAIM_MISMATCH`; never prefer stored conditions, claim, boundary, or digest.

- [ ] **Step 6: Add offline-purity/read-once regression tests**

Patch network/subprocess/Docker/resolver entry points to raise if called. Patch `read_first_bad_package()` to return a prepared snapshot and assert `verify_first_bad()` calls it once. Delete the fixture's source/original-project directory before verification and require the same receipt.

- [ ] **Step 7: Verify Task 4**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_models.py tests/unit/test_first_bad_claims.py tests/unit/test_first_bad_io.py tests/unit/test_first_bad_verify.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad tests/unit/test_first_bad_*.py
git diff --check
```

- [ ] **Step 8: Commit and fresh-review Task 4**

```bash
git add src/qualock/protocols/first_bad/verify.py tests/unit/test_first_bad_verify.py
git commit -m "feat: verify first-bad chains offline"
```

Reviewer must inspect exact verification order, child-error normalization, earliest-boundary logic, truncated-prefix semantics, stored receipt distrust, and offline purity.

### Task 5: Add Offline Rendering and `evidence verify-first-bad`

**Files:**
- Create: `src/qualock/protocols/first_bad/render.py`
- Modify: `src/qualock/cli.py`
- Test: `tests/unit/test_first_bad_cli.py`
- Re-run: `tests/unit/test_evidence_cli.py`, `tests/unit/test_paired_change_cli.py`

**Interfaces:**
- Produces `render_first_bad_receipt(receipt: FirstBadReceiptV1) -> str`.
- Adds `qualock evidence verify-first-bad CHAIN_DIR [--write-receipt]`.
- Read-only is default; existing chain receipt is always checked by `verify_first_bad()`.

- [ ] **Step 1: Write RED renderer tests**

Require deterministic low-tech text containing catalog start/upper, one line per edge, final claim, and boundary only when present. Do not print raw JSON, credentials, absolute source-project paths, or child event content.

Example expected core text:

```text
QuaLock First-Bad Verification
Range: 0.150.0 -> 0.153.0
0.150.0 -> 0.151.0  NO REGRESSION
0.151.0 -> 0.152.0  ATTRIBUTABLE CHANGE
FIRST ATTRIBUTABLE BAD: 0.152.0
```

- [ ] **Step 2: Write RED CLI exit/write tests**

Cover:

- `NO_ATTRIBUTABLE_BAD_FOUND` -> exit `0`;
- `FIRST_ATTRIBUTABLE_BAD` -> exit `2`;
- `UNRESOLVED` -> exit `4`;
- every `FirstBadVerificationError` -> exit `3` with bounded message;
- unexpected error -> exit `1` with stable generic copy;
- `--write-receipt` calls verify first, then create-new writer;
- existing valid receipt is checked automatically and never overwritten.

- [ ] **Step 3: Implement renderer and thin CLI wiring**

Use new CLI-local argument/option constants rather than reusing paired-change `--protocol`. The command accepts one chain directory only. Catch first-bad structural errors alongside existing bounded input errors; do not add first-bad exceptions to unrelated evidence commands.

- [ ] **Step 4: Verify backward-compatible evidence CLI behavior**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_cli.py tests/unit/test_evidence_cli.py tests/unit/test_paired_change_cli.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad/render.py src/qualock/cli.py tests/unit/test_first_bad_cli.py
git diff --check
```

- [ ] **Step 5: Commit and fresh-review Task 5**

```bash
git add src/qualock/protocols/first_bad/render.py src/qualock/cli.py tests/unit/test_first_bad_cli.py
git commit -m "feat: expose first-bad offline verification"
```

Review must confirm exact exit mapping, verify-before-write ordering, no overwrite, literal-safe output, and no regression to `evidence export|verify|verify-claim`.

### Task 6: Add First-Bad Preflight, Capability, and Isolated Workspace Snapshot

**Files:**
- Modify: `src/qualock/agents/orchestration.py`
- Create: `src/qualock/protocols/first_bad/orchestrate.py`
- Test: `tests/unit/test_first_bad_orchestrate.py`
- Re-run: `tests/unit/test_version_bisect_commands.py`, `tests/unit/test_agent_orchestration.py`.

**Interfaces:**
- `OrchestrationCapabilities` gains `first_bad: bool = False`; supported Codex/Claude/Gemini capabilities set it `True`; Antigravity remains `False`.
- Produces `FirstBadPreflight` internal dataclass with agent, trusted baseline lock/state, upper version, frozen catalog tuple, suite/config digests, and model pin.
- Produces `first_bad_preflight(root, upper_spec, *, catalog=None) -> FirstBadPreflight`.
- Produces `_snapshot_project_inputs(root, workspace) -> None` for config/canaries only; it never copies results/work/cache.

- [ ] **Step 1: Write RED capability/preflight tests**

Cover agent mismatch, unsupported Antigravity before catalog call, non-exact/non-stable upper, upper absent from catalog, upper <= baseline, trusted baseline absent from catalog, malformed release entries, duplicate/out-of-order fetched catalogs, and fetched-order range selection. For causal adjacency, unlike legacy bisect, first-bad requires the trusted baseline version to be present in the fetched stable catalog.

- [ ] **Step 2: Implement explicit capability and frozen-catalog preflight**

Use keyword construction for `OrchestrationCapabilities` so adding the field cannot silently reorder positional meaning. Parse stable versions with exact `^(\d+)\.(\d+)\.(\d+)$`; reject duplicate or non-increasing fetched snapshots, then freeze `baseline..upper` in fetched order exactly once without sorting/deduplicating or refetching during the run.

- [ ] **Step 3: Write RED workspace-snapshot tests**

Create a project with config, canaries, baseline, results, work cache, and unrelated files. Snapshot to a private workspace and assert only `.qualock/config.yaml` and `.qualock/canaries/` are reproduced before baseline setup. Reject symlinked config/canary input rather than following it into an unexpected location.

- [ ] **Step 4: Implement minimal trusted project snapshotting**

Create workspace directories with owner-controlled names. Copy only regular config/canary files needed by `load_project()`. Do not copy `.qualock/results`, `.qualock/work`, existing sidecars, scheduler state, GitHub artifacts, or the real baseline by default. First-edge baseline injection happens in Task 7 through parsed/written `BaselineLock`, not raw file copy.

- [ ] **Step 5: Assert real baseline immutability in every preflight/workspace test**

Capture `real_baseline_path.read_bytes()` before preflight/snapshot and require byte-for-byte equality afterward. Add a test that makes the real baseline read-only and still permits preflight/snapshot.

- [ ] **Step 6: Verify Task 6**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_orchestrate.py tests/unit/test_version_bisect_commands.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/agents/orchestration.py src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git diff --check
```

- [ ] **Step 7: Commit and fresh-review Task 6**

```bash
git add src/qualock/agents/orchestration.py src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git commit -m "feat: prepare isolated first-bad scans"
```

Reviewer must verify unsupported agents fail before release discovery, first-bad does not alter legacy bisect capability semantics, frozen catalog is immutable, and workspace snapshotting cannot mutate/copy the real baseline or prior results.

### Task 7: Produce and Verify One Adjacent Edge in Isolation

**Files:**
- Modify: `src/qualock/protocols/first_bad/orchestrate.py`
- Test: `tests/unit/test_first_bad_orchestrate.py`
- Re-run: `tests/integration/test_paired_change_e2e.py`, `tests/unit/test_evidence_export.py`

**Interfaces:**
- Task 1 must expose `verify_paired_change_details(bundle_path: Path, protocol_path: Path) -> VerifiedPairedChangeV1`; existing `verify_paired_change()` returns `details.receipt`.
- Add `FirstBadExecutionDependencies(baseline_executor, check_executor, evidence_exporter)` with production defaults `execute_baseline`, `execute_check`, `export_evidence_bundle` and injectable fakes.
- Add internal `VerifiedFirstBadEdge(record: FirstBadEdgeEvidenceV1, summary: FirstBadEdgeSummaryV1, details: VerifiedPairedChangeV1)`.
- Add `_execute_edge(preflight, index, workspace, edge_dir, expected_baseline_state, deps) -> VerifiedFirstBadEdge`.

- [ ] **Step 1: Write RED first-edge tests**

Require the first edge to canonical-write the parsed trusted real `BaselineLock` into the isolated workspace, never call `baseline_executor`, call `check_executor(workspace, agent@candidate)`, export the returned qualification id, and leave real baseline bytes unchanged.

- [ ] **Step 2: Write RED later-edge continuity tests**

For edge index > 0, require `baseline_executor(workspace, agent@baseline_version)`. Compare the resulting lock's agent name/version/binary SHA/support SHA/model pin to the previous edge candidate `AgentDependencyStateV1`. Any mismatch raises a dedicated orchestration-side `FirstBadBaselineUnresolved` before candidate check/export.

- [ ] **Step 3: Implement baseline-state conversion and edge setup**

Use `write_baseline_lock()` for first-edge trusted lock materialization. Convert a `BaselineLock` to `AgentDependencyStateV1` without resolver/network access: agent fields come from `lock.agent`; model fields come from `lock.model`.

- [ ] **Step 4: Write RED export/normalization/verification tests**

The existing exporter writes companion path `<bundle>.paired-change-v1`. In the private first-bad staging edge, require successful export to `edge_dir/bundle`, require a non-null protocol companion, then rename that owned companion exactly once to `edge_dir/protocol`. Final edge inventory must be only `bundle/` and `protocol/`.

Immediately call `verify_paired_change_details(edge_dir / "bundle", edge_dir / "protocol")`. Do not construct an edge record from the qualification result alone.

- [ ] **Step 5: Implement verified edge-record derivation**

Populate `FirstBadEdgeEvidenceV1` only from recomputed details:

```python
record = FirstBadEdgeEvidenceV1(
    index=index,
    baseline_version=details.evidence.baseline_state.version,
    candidate_version=details.evidence.candidate_state.version,
    baseline_runtime_identity=details.evidence.baseline_state,
    candidate_runtime_identity=details.evidence.candidate_state,
    bundle_manifest_sha256=details.bundle.manifest_sha256,
    protocol_evidence_sha256=details.protocol_evidence_sha256,
)
```

Derive the edge summary from `details.receipt`. Assert its versions equal the frozen catalog edge before returning.

- [ ] **Step 6: Test causal stop classifications without running a full scan**

Use fake receipts for all-no-regression, attributable, and unresolved canary combinations and assert `_execute_edge()` returns the exact Task 2 `EdgeClassification`. Qualification suite `Verdict` must not control this classification.

- [ ] **Step 7: Verify Task 7**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_first_bad_orchestrate.py tests/integration/test_paired_change_e2e.py \
  tests/unit/test_evidence_export.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git diff --check
```

- [ ] **Step 8: Commit and fresh-review Task 7**

```bash
git add src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git commit -m "feat: produce verified first-bad edges"
```

Task 7 must not reopen the paired-change verification design: `verify_paired_change_details()` is a Task 1 interface. Reviewer must trace first/later baseline trust, full runtime identity equality, export companion normalization, and evidence-derived edge records.

### Task 8: Orchestrate the Full Scan and Publish a Self-Contained Package

**Files:**
- Modify: `src/qualock/protocols/first_bad/orchestrate.py`
- Test: `tests/unit/test_first_bad_orchestrate.py`

**Interfaces:**
- Produces `FirstBadOrchestrationOutcome(first_bad_id, agent_name, baseline_version, upper_version, package_path, receipt)`.
- Produces `execute_first_bad(root: Path, upper_spec: str, *, catalog=None, deps=None, first_bad_id=None, on_start=None, on_edge=None) -> FirstBadOrchestrationOutcome`.

- [ ] **Step 1: Write RED scan-stop tests**

Use a four-version fake catalog. Require:

- no-regression on every edge -> run all edges and final `NO_ATTRIBUTABLE_BAD_FOUND`;
- no-regression then attributable -> stop immediately after boundary and never call later edge candidate;
- unresolved edge -> include that edge, stop, and final `UNRESOLVED`;
- later `BaselineUnstableError`/`FirstBadBaselineUnresolved` -> do not skip the version; publish the already-verified no-regression prefix as a cleanly truncated `UNRESOLVED` chain;
- unexpected check/export exception -> no public final package.

- [ ] **Step 2: Implement scan staging with separate ephemeral workspaces**

Create final-package staging under `.qualock/results/.first-bad-tmp-<random>` so publication can be same-filesystem atomic. Create each isolated project workspace with `tempfile.TemporaryDirectory(prefix="qualock-first-bad-edge-")`; only the final edge bundles/protocol companions are copied/exported into package staging.

The frozen catalog is fetched once in preflight and passed through all iterations. Never call release discovery from `_execute_edge()`.

- [ ] **Step 3: Build canonical chain evidence only from verified edges**

Top-level suite/config/model come from trusted preflight and must equal every verified child. `protocol_design_sha256` comes from the first verified child and every later child must match it. Use the full frozen catalog tuple even when the carried edge prefix stops early.

Build with a temporary valid SHA value, then replace it using the self-excluding digest helper:

```python
temp = FirstBadChainEvidenceV1(..., chain_sha256="0" * 64)
evidence = temp.model_copy(update={"chain_sha256": digest_chain_evidence(temp)})
```

- [ ] **Step 4: Verify staged package before publication**

Write canonical `chain-evidence.json` with create-new semantics. Call `verify_first_bad(staging_path)`. Then call `write_first_bad_receipt(staging_path, receipt)` and call `verify_first_bad(staging_path)` again so the stored receipt is itself checked before publication.

A later-baseline instability that produced a clean truncated prefix must therefore verify offline as `UNRESOLVED`; orchestration is not allowed to invent a different receipt.

- [ ] **Step 5: Implement atomic no-overwrite publication**

Final path is `.qualock/results/<first_bad_id>/`, where ids use `first-bad-<UTC>-<random>`. Publish the entire verified staging directory by no-replace rename. On collision/failure, do not delete or overwrite the destination and do not recursively delete an unverified pathname; leave the private staging directory for diagnosis.

- [ ] **Step 6: Write publication/immutability RED-to-GREEN tests**

Assert final inventory contains only `chain-evidence.json`, `chain-receipt.json`, and `edges/`; each edge contains only `bundle/` and `protocol/`; no workspace/temp path appears in JSON; final destination collision is preserved; and the real baseline bytes remain unchanged after every terminal outcome. Add a history-scan regression proving top-level `first-bad-*` and private `.first-bad-tmp-*` result directories are ignored as non-qualification evidence rather than corrupting existing history analysis.

- [ ] **Step 7: Verify Task 8**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_orchestrate.py tests/unit/test_first_bad_verify.py tests/unit/test_first_bad_io.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git diff --check
```

- [ ] **Step 8: Commit and fresh-review Task 8**

```bash
git add src/qualock/protocols/first_bad/orchestrate.py tests/unit/test_first_bad_orchestrate.py
git commit -m "feat: orchestrate first-bad causal scans"
```

Reviewer must inspect stop-on-first-attributable/unresolved semantics, truncated-prefix behavior, one-time catalog freeze, staged-package self-containment, verify-before-publish, no-overwrite publication, and real baseline immutability.

### Task 9: Add the Low-Tech `qualock first-bad` CLI

**Files:**
- Modify: `src/qualock/protocols/first_bad/render.py`
- Modify: `src/qualock/cli.py`
- Test: `tests/unit/test_first_bad_cli.py`
- Re-run: `tests/unit/test_version_bisect_cli.py`

**Interfaces:**
- Adds top-level `qualock first-bad <agent>@<exact-stable-upper>`.
- Uses `execute_first_bad()` only; CLI does not implement scan logic.
- Reuses `FirstBadReceiptV1` claim classes for exit mapping: no-bad `0`, first-attributable `2`, unresolved `4`.

- [ ] **Step 1: Write RED progressive rendering tests**

Require title, baseline/upper once, one line per verified adjacent edge, final package path, and terminal copy that says `FIRST ATTRIBUTABLE BAD`, `No attributable bad found`, or `No first-attributable-bad claim can be made beyond this edge.` as appropriate.

- [ ] **Step 2: Expose orchestration callbacks for progressive output**

Task 8's `execute_first_bad()` must accept optional `on_start(preflight, staging_path)` and `on_edge(FirstBadEdgeSummaryV1)` callbacks. Invoke callbacks only after preflight succeeds / an edge has been durably exported and independently verified. Callback failures propagate as operational failures; they never alter evidence.

- [ ] **Step 3: Write RED CLI exit/error tests**

Cover outcome exits `0/2/4`; preflight/config/baseline-input errors -> `3`; release discovery/unexpected operational errors -> `1`; generated-package verification failure -> `1` with stable generic copy. Assert `qualock bisect` mocks/output/exit tests remain byte-for-byte behaviorally unchanged.

- [ ] **Step 4: Implement thin CLI command and renderer**

Do not add budgets, auto-baseline promotion, cache flags, or publication flags in V1. The command has one positional exact upper spec. It prints the package path only after atomic publication succeeds.

- [ ] **Step 5: Verify Task 9**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_cli.py tests/unit/test_version_bisect_cli.py tests/unit/test_version_bisect_commands.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/protocols/first_bad/render.py src/qualock/cli.py tests/unit/test_first_bad_cli.py
git diff --check
```

- [ ] **Step 6: Commit and fresh-review Task 9**

```bash
git add src/qualock/protocols/first_bad/render.py src/qualock/cli.py tests/unit/test_first_bad_cli.py
git commit -m "feat: add first-bad causal scan cli"
```

Reviewer must confirm CLI is thin, low-tech copy does not overclaim global first-bad, package path is printed only after publication, and legacy bisect remains unchanged.

### Task 10: Add Golden Conformance, Deterministic E2E, and Final Gates

**Files:**
- Create: `tests/fixtures/first_bad_v1/edges/` with only shared canonical edge seeds.
- Create: `tests/fixtures/first_bad_v1/vectors.json` containing all 16 vector definitions and literal expected outputs.
- Create: `tests/unit/test_first_bad_conformance.py`
- Create: `tests/integration/test_first_bad_e2e.py`
- Modify protocol/evidence implementation only if a RED conformance/E2E test exposes a real defect.

**Fixture rule:** Do not duplicate a full Evidence Bundle for every vector. Commit three shared portable edge seeds sufficient for: no-regression `v0->v1`, no-regression `v1->v2`, and attributable `v1->v2`. `vectors.json` declares ordered seed composition, literal chain-evidence payload/expected receipt or error, and explicit test-only mutations. Assembly copies bytes into `tmp_path`; it must not call production first-bad builders/digest helpers to manufacture expected answers.

- [ ] **Step 1: Add the 16 required vector definitions**

Exact vector names:

```text
no-bad-full-range
first-bad-first-edge
first-bad-middle
multiple-canaries-one-attributable
unresolved-prefix
unresolved-boundary
missing-edge
skipped-catalog-release
reordered-or-duplicate-catalog
cross-edge-binary-identity-mismatch
support-identity-mismatch
suite-config-design-drift
tampered-child-bundle
tampered-paired-change-evidence
tampered-chain-receipt
unexpected-inventory-or-symlink
```

- [ ] **Step 2: Write one parametrized golden conformance test**

The assembler must produce the exact portable package layout, then snapshot all regular-file bytes before verification. For valid vectors compare ordered conditions, edge summaries, claim, boundary, and canonical receipt bytes to literal expected data. For structural vectors compare exact `FirstBadVerificationReason`. Re-snapshot after verification and require byte identity.

Also assert:

```python
if receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD:
    assert len(receipt.conditions) == 10
    assert all(item.status is ConditionStatus.TRUE for item in receipt.conditions)
```

For symlink/reparse vectors, build the filesystem mutation at test runtime and skip only when the host cannot express that platform-specific primitive.

- [ ] **Step 3: Run conformance tests RED before any defect fix**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_first_bad_conformance.py -q
```

Any failure that reflects a real spec mismatch gets a focused implementation fix plus its own local commit after RED is captured. Do not edit expected data merely to match current behavior unless the spec itself proves the expectation wrong.

- [ ] **Step 4: Add deterministic local E2E — attributable middle boundary**

Reuse the existing fake qualification infrastructure and actual `execute_baseline`, `execute_check`, `export_evidence_bundle`, first-bad orchestrator, and offline verifier. Use a frozen catalog such as `0.150.0, 0.151.0, 0.152.0, 0.153.0`; make `0.150.0` and `0.151.0` pass, `0.152.0` fail validly, and assert edge `0.151.0 -> 0.152.0` is the first attributable boundary and `0.153.0` is never run.

- [ ] **Step 5: Add deterministic local E2E — no-bad full range**

Use the same frozen design/catalog but make every adjacent candidate valid/pass. Require all required edges, `NO_ATTRIBUTABLE_BAD_FOUND`, no mutation of the real baseline, and a published self-contained package.

- [ ] **Step 6: Prove offline portability after project deletion**

Copy the published package to a path outside the source project, delete the synthetic source project, then call `verify_first_bad(portable_copy)`. Require receipt equality with the orchestration receipt. Patch release discovery/resolver/subprocess/network/provider surfaces to raise during this final offline call.

- [ ] **Step 7: Run all first-bad tests**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_first_bad_*.py tests/integration/test_first_bad_e2e.py -q
```

Expected: PASS, with only explicit platform skips.

- [ ] **Step 8: Run backward-compatibility gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_version_bisect_commands.py tests/unit/test_version_bisect_models.py \
  tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_cli.py \
  tests/unit/test_evidence_verify.py tests/unit/test_evidence_export.py tests/unit/test_evidence_cli.py \
  tests/unit/test_paired_change_*.py tests/integration/test_paired_change_e2e.py \
  tests/integration/test_evidence_bundle_e2e.py -q
```

- [ ] **Step 9: Run fresh repository-wide gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src tests
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock
git diff --check
```

The full pytest gate must be green. Ruff/mypy must either be green or reproduce only debt recorded unchanged at Execution Preflight; compare exact file/diagnostic sets before adjudicating. Do not fix unrelated lint or install PyYAML stubs inside this feature.

- [ ] **Step 10: Commit conformance/E2E evidence**

```bash
git add tests/fixtures/first_bad_v1 tests/unit/test_first_bad_conformance.py \
  tests/integration/test_first_bad_e2e.py
git commit -m "test: certify first-bad protocol conformance"
```

If conformance-driven implementation fixes exist, commit each separately before this test-certification commit with its RED regression test and narrow message.

- [ ] **Step 11: Run native Windows first-bad I/O/conformance coverage**

At minimum run `tests/unit/test_first_bad_io.py` and the non-POSIX-only portion of `tests/unit/test_first_bad_conformance.py` under the available native Windows Python. Record passed/skipped counts and verify no reparse/race regression was silently skipped.

- [ ] **Step 12: Fresh independent whole-branch review**

Review exact implementation base `ff08965..HEAD` against the canonical first-bad spec and this plan. Reviewer must inspect shared payload-verifier refactors, all first-bad production files, shared fixtures/vector expectations, E2E outputs, and verification logs.

Reviewer focus must include:

- `FIRST_ATTRIBUTABLE_BAD` cannot exist without ten `TRUE` chain conditions;
- all prefix edges are recomputed no-regression and the boundary is recomputed attributable;
- runtime identity chaining includes binary/support/model identity, not version only;
- full no-bad claim requires complete catalog coverage;
- clean truncation yields only `UNRESOLVED`;
- stored paired-change/first-bad receipts are never trusted;
- package verification is offline and read-only by default;
- chain child reads cannot be redirected by symlink/reparse/rename races;
- real baseline remains immutable;
- `qualock bisect`, Evidence Bundle V1, and paired-change/v1 semantics remain unchanged;
- public wording is scoped to the exact frozen catalog snapshot, never global first-bad.

Require output ending exactly `CLEAN FOR FIRST-BAD V1` only when zero Critical/Important findings remain.

- [ ] **Step 13: Reproduce every accepted review finding before fixing**

For each Critical/Important finding, add a failing focused regression test first, run it RED, implement the narrow fix, rerun Task 10 Steps 7-11, commit the fix separately, and obtain a fresh reviewer verdict. Do not close a disputed finding by assertion; reproduce or disprove its premise with executable evidence where possible.

- [ ] **Step 14: Record completion evidence and final cleanliness**

Update only the existing SDD ledger if one exists; do not add separate review-output files. Record exact full pytest, first-bad suite, Windows IO/conformance, Ruff/mypy adjudication, and final reviewer verdict. Then require:

```bash
git status --short
```

Expected: clean worktree. Do not update `README.md` or `ROADMAP.md`; do not push/PR/merge/tag/release/publish without separate authorization.

## Completion Evidence

Before declaring local P0 complete, the ledger must contain fresh exact-head evidence for:

- full repository pytest;
- all first-bad unit + E2E + conformance tests;
- native Windows first-bad I/O/conformance coverage;
- backward-compatibility bisect/evidence/paired-change gates;
- Ruff, compileall, mypy, and `git diff --check`, with only exact preflight debt adjudicated;
- self-contained offline re-verification after deleting the synthetic original project;
- fresh independent whole-branch verdict with zero Critical/Important findings and final line `CLEAN FOR FIRST-BAD V1`;
- clean `git status --short`.

Completion does not itself authorize authenticated real-provider scans, evidence publication, README/ROADMAP delivery claims, push, PR, merge, tag, release, or package publication.
