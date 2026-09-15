# Paired Change v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved paired-change/v1 design with TDD while preserving existing qualification and evidence behavior.

**Architecture:** Preserve the current qualification engine and strict Evidence Bundle V1. Add generic identity/order/runtime evidence below a new paired-change protocol layer; write `PairedChangeRunV1` at check time, bind it to the exact exported manifest as `ProtocolEvidenceV1`, then recompute conditions and claims offline.

**Tech Stack:** Python 3.11+, dataclasses, Pydantic v2, pytest, Ruff, mypy, existing canonical JSON and SHA-256 helpers. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-13-paired-change-v1-design.md`

## Global Constraints

- Use red-green-refactor TDD for every production change.
- Keep baseline/evidence schema version 1 and the exact Evidence Bundle V1 inventory.
- Preserve current verdicts, exit codes, GitHub conclusions, release monitor, and legacy bisect semantics.
- Exclude protocol-only canary metadata from legacy canary and suite fingerprints.
- Missing causal evidence maps to `UNKNOWN` and therefore cannot yield attribution.
- P0 only certifies complete deterministic comparisons with all required attempts valid.
- No authenticated provider run is required.
- Do not install dependencies or materialize packages.
- Do not push, create a PR, merge, tag, release, or publish unless the user separately authorizes it.
- Do not implement `first-bad/v1`, compatibility ranges/registry, component isolation, probabilistic causal statistics, signatures/standards exporters, or dashboards in this P0.

---

### Task 1: Bind Runtime Support Identity Generically

**Files:** `src/qualock/commands.py`, `src/qualock/github_pr/commands.py`, `src/qualock/evidence/export.py`, `src/qualock/evidence/verify.py`; tests in `test_commands.py`, `test_github_pr_commands.py`, `test_evidence_export.py`, `test_evidence_verify.py`.

**Interfaces:** Reuse `agent_support_fingerprint(binary) -> str | None`. Runtime support is trusted only when the resolved fingerprint exactly equals the locked/trusted value. `(None, None)` is valid; any null/non-null or digest mismatch is stale. Keep Gemini's stronger non-null invariant.

- [ ] **Step 1: Write failing Codex baseline/check tests.** Replace `test_codex_check_accepts_legacy_missing_support_fingerprint` with a rejection test and add a new-baseline test:

```python
def test_codex_baseline_writes_support_fingerprint(...):
    lock = execute_baseline(...)
    assert lock.agent.support_sha256 == agent_support_fingerprint(resolved_codex)


def test_codex_check_rejects_legacy_missing_support_before_candidate_resolution(...):
    with pytest.raises(BaselineStaleError, match="support fingerprint"):
        execute_check(...)
    assert resolver.candidate_resolved is False
```

Also retain a Claude `None/None` acceptance vector.

- [ ] **Step 2: Run the focused command tests and observe the Codex cases fail.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_commands.py -k 'support' -q
```
- [ ] **Step 3: Generalize baseline/check support binding.** In `execute_baseline()` always compute `support_sha256 = agent_support_fingerprint(binary)`. In `execute_check()` use:

```python
observed_support = agent_support_fingerprint(baseline_binary)
if observed_support != lock.agent.support_sha256:
    raise BaselineStaleError("baseline support fingerprint changed or missing")
```

Keep main-binary verification first and candidate resolution after baseline identity verification.

- [ ] **Step 4: Add generic GitHub candidate tests and implementation.** Every non-null proposed `support_sha256` must be lowercase 64-hex. After candidate resolution compare `agent_support_fingerprint(resolved)` directly to trusted `candidate.support_sha256`; Codex missing/wrong helper identity rejects, Claude `None/None` remains valid.

- [ ] **Step 5: Make evidence export and verification mirror support exactly.** Replace the current conditional baseline-support comparison with direct equality between lock, provenance, and manifest. Keep the existing Gemini schema rule requiring non-null support.

- [ ] **Step 6: Run the focused identity suite.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_commands.py tests/unit/test_github_pr_commands.py \
  tests/unit/test_evidence_provenance.py tests/unit/test_evidence_export.py \
  tests/unit/test_evidence_verify.py -q
```

- [ ] **Step 7: Commit.**

```bash
git add src/qualock/commands.py src/qualock/github_pr/commands.py src/qualock/evidence/export.py src/qualock/evidence/verify.py tests/unit/test_commands.py tests/unit/test_github_pr_commands.py tests/unit/test_evidence_export.py tests/unit/test_evidence_verify.py
git commit -m "fix: bind runtime support identity generically"
```

### Task 2: Balance Paired Qualification Order

**Files:** `src/qualock/run/schedule.py`; tests in `test_schedule.py`, budget/token-budget tests, and `tests/integration/test_fake_qualification.py`.

**Interfaces:** Keep `paired_schedule(...) -> tuple[RunSlot, ...]`. Seed only the first pair orientation from `qualification_id + canary_id`, then alternate `AB/BA/AB...` or `BA/AB/BA...`.

- [ ] **Step 1: Add failing scheduler properties.** Assert adjacent A/B slots, orientation alternation, exact balance for even repetitions, count difference <= 1 for odd repetitions, determinism, and qualification-ID variation.

```python
def orientations(slots):
    return [(slots[i].side, slots[i + 1].side) for i in range(0, len(slots), 2)]
```

- [ ] **Step 2: Verify red.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_schedule.py -q
```

- [ ] **Step 3: Implement one hash seed plus alternating orientation.** Remove the repetition number from the seed hash and invert first/second side on every even repetition.

- [ ] **Step 4: Verify scheduler and budgets.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_schedule.py tests/unit/test_budgeted_qualification.py tests/unit/test_token_budgeted_qualification.py tests/integration/test_fake_qualification.py -q
```

- [ ] **Step 5: Commit.**

```bash
git add src/qualock/run/schedule.py tests/unit/test_schedule.py tests/unit/test_budgeted_qualification.py tests/unit/test_token_budgeted_qualification.py tests/integration/test_fake_qualification.py
git commit -m "refactor: balance paired qualification order"
```

### Task 3: Add Protocol-Only Canary Metadata Without Staling Baselines

**Files:** `src/qualock/canary/models.py`, `src/qualock/project.py`, `tests/unit/test_canary_models.py`, `tests/unit/test_canary_loader.py`, and `tests/unit/test_project_fingerprint.py`.

**Interfaces:** Add optional `CanarySpec.paired_change`. It contains unique non-empty `material_dimensions` from the nine approved strings and positive `max_pair_gap_ms`. Define `MaterialDimensionName` locally in `canary/models.py` as a `Literal` over the nine approved values; lower-level canary code must not import the paired-change protocol package.

```python
class PairedChangeCanarySpec(BaseModel):
    material_dimensions: tuple[MaterialDimensionName, ...] = Field(min_length=1)
    max_pair_gap_ms: int = Field(gt=0)
```

- [ ] **Step 1: Add model/loader red tests.** Cover explicit metadata, legacy absence, empty dimensions, duplicate dimensions, unknown dimension, and non-positive gap.

- [ ] **Step 2: Run model/loader tests and observe failure.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_canary_models.py tests/unit/test_canary_loader.py -q
```

- [ ] **Step 3: Implement the optional typed block and a validator rejecting duplicate dimensions.**

- [ ] **Step 4: Add a fingerprint red test.** Create otherwise-identical canaries with and without `paired_change`; require equal `canary_fingerprint()` and `suite_fingerprint()`.

- [ ] **Step 5: Preserve legacy fingerprints.** In `_canary_fingerprint_payload()` remove only the protocol field before hashing:

```python
item = canary.model_dump(mode="json")
item.pop("paired_change", None)
```

- [ ] **Step 6: Run canary/fingerprint tests and commit.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_canary_models.py tests/unit/test_canary_loader.py tests/unit/test_project_fingerprint.py -q
git add src/qualock/canary/models.py src/qualock/project.py tests/unit/test_canary_models.py tests/unit/test_canary_loader.py tests/unit/test_project_fingerprint.py
git commit -m "feat: add paired-change canary metadata"
```

### Task 4: Capture Generic Attempt Control Context

**Files:** `src/qualock/run/models.py`, `src/qualock/run/executor.py`, `src/qualock/run/backend.py`, `src/qualock/run/host_backend.py`; tests in Docker/host backend files, fake qualification integration, and storage/report regression tests.

**Interfaces:** Keep legacy `QualificationBackend.run_attempt(...) -> AttemptResult`. Add an optional runtime-checkable `ProtocolAwareQualificationBackend` with `control_profiles(canary)` and `run_attempt_with_context(...) -> AttemptExecution`. Extend `QualificationExecutor.run(..., trace_sink: list[AttemptRunTrace] | None = None, trace_design_sha256: str | None = None)`. The generic name keeps the run layer protocol-neutral while allowing a prospectively frozen design digest to be bound before the first attempt.

```python
@dataclass(frozen=True)
class AttemptControlProfiles:
    preparation_sha256: str | None
    isolation_sha256: str | None
    resource_sha256: str | None
    runtime_sha256: str | None

@dataclass(frozen=True)
class AttemptControlContext:
    profiles: AttemptControlProfiles
    isolation_instance_sha256: str | None

@dataclass(frozen=True)
class AttemptExecution:
    result: AttemptResult
    context: AttemptControlContext
```

`AttemptRunTrace` additionally stores canary/side/repetition, `trace_design_sha256`, monotonic start/finish offsets, `events_sha256`, and the control context. `QualificationExecutor.run()` captures one monotonic epoch before scheduling and stores offsets relative to that epoch. Trace data must not be added to `QualificationResult` or legacy report JSON.

- [ ] **Step 1: Add a protocol-aware fake backend red test.** Pass a fixed `trace_design_sha256` before execution and require every trace to retain it, plus exact side/repetition, non-negative monotonic offsets, correct `sha256(events_jsonl.encode())`, and preserved control context. Also prove `trace_sink=None` with no design digest still calls only legacy `run_attempt()`.

- [ ] **Step 2: Run fake qualification tests and observe missing-interface failure.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/integration/test_fake_qualification.py -q
```

- [ ] **Step 3: Add the generic dataclasses/protocol and instrument the executor.** Use `time.monotonic_ns()`; if the backend is not protocol-aware, record timing/events with all control fields `None` instead of inventing values.
- [ ] **Step 4: Add Docker control-profile red tests and implementation.** Hash canonical descriptors, never host paths. Use procedure IDs such as `docker-prepare-v1`, `docker-fresh-container-v1`, and a resource descriptor containing timeout plus explicit null CPU/memory limits. The per-attempt isolation digest may hash the generated container name.

Refactor Docker execution so legacy and contextual paths share one private implementation:

```python
def run_attempt(...):
    return self._run_attempt_execution(...).result

def run_attempt_with_context(...):
    return self._run_attempt_execution(...)
```

- [ ] **Step 5: Add Linux-host profile red tests and implementation.** Hash deterministic prepare/isolation/resource descriptors. Hash the temporary attempt-root identity into `isolation_instance_sha256`, but never persist its absolute path. Set `runtime_sha256=None` until exact host runtime identity is genuinely captured.

- [ ] **Step 6: Prove legacy artifacts stay unchanged.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_docker_backend.py tests/unit/test_host_backend.py \
  tests/integration/test_fake_qualification.py tests/unit/test_storage.py \
  tests/unit/test_report.py -q
```

Expected: PASS; legacy report/qualification JSON contains no trace/control fields.

- [ ] **Step 7: Commit.**

```bash
git add src/qualock/run/models.py src/qualock/run/executor.py src/qualock/run/backend.py src/qualock/run/host_backend.py tests/unit/test_docker_backend.py tests/unit/test_host_backend.py tests/integration/test_fake_qualification.py tests/unit/test_storage.py tests/unit/test_report.py
git commit -m "feat: capture qualification control context"
```

### Task 5: Define Strict Paired-Change v1 Models and Canonical Digests

**Files:** create `src/qualock/protocols/__init__.py`, `src/qualock/protocols/paired_change/__init__.py`, `models.py`, `fingerprint.py`, `design.py`; create `tests/unit/test_paired_change_models.py` and `test_paired_change_design.py`.

**Interfaces:** All protocol Pydantic models use `ConfigDict(frozen=True, extra="forbid")`. SHA fields are lowercase 64-hex. Closed enums include nine material dimensions, ten condition types, `TRUE/FALSE/UNKNOWN`, and `NO_REGRESSION_OBSERVED/ATTRIBUTABLE_CHANGESET/UNRESOLVED`.

Core models created in this task:

```text
AgentDependencyStateV1
ChangeSetV1
ProtocolDesignV1 / CanaryProtocolDesignV1
AttemptProtocolContextV1 / PairEvidenceV1 / CanaryRunEvidenceV1
PairedChangeRunV1
ProtocolEvidenceV1
ProtocolConditionV1 / CanaryClaimV1 / ClaimReceiptV1
```

Schema anchors: `ProtocolDesignV1` includes `protocol_id`, `protocol_digest`, `suite_sha256`, `config_sha256`, repetitions, order/lifecycle policy, and per-canary declarations/control digests. `PairedChangeRunV1` includes `protocol_design` plus `protocol_design_sha256` but never `evidence_manifest_sha256`. `ProtocolEvidenceV1` adds the exact `evidence_manifest_sha256`. `ClaimReceiptV1` binds protocol/evidence/state/change-set digests, ordered qualification conditions, ordered canary claims, and verifier name/version; it excludes timestamps, UUIDs, hostnames, and absolute paths.

- [ ] **Step 1: Write strict schema red tests.** Reject unknown fields, unknown enums, malformed SHA values, duplicate canary IDs, duplicate repetition IDs, side/repetition inconsistency, and a `PairedChangeRunV1` containing `evidence_manifest_sha256`.

- [ ] **Step 2: Run the model tests and observe import/model failures.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_models.py -q
```

- [ ] **Step 3: Implement minimal strict schemas.** Keep human prose out of canonical condition content; condition fields are `type`, `status`, `reason`, and optional `evidence_sha256`.

- [ ] **Step 4: Add state/change-set digest red tests.** Build baseline/candidate states with binary/support/model changes and require deterministic canonical digests plus a structurally derived changed-dimension set.

- [ ] **Step 5: Implement `build_agent_dependency_state()` and `derive_changeset()`.** The builder consumes resolved `AgentBinary` plus the existing model pin values. `derive_changeset()` never accepts a user assertion about what changed.

- [ ] **Step 6: Define the versioned protocol descriptor and design digest.** `paired-change/v1` descriptor binds required condition names, `alternating-v1` order policy, `FRESH` lifecycle, and v1 claim classes. `ProtocolDesignV1` binds `suite_sha256`, `config_sha256`, repetitions, and each canary's fingerprint, protocol declaration, temporal gap, and expected control-profile digests.

- [ ] **Step 7: Run model/design tests and commit.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_models.py tests/unit/test_paired_change_design.py -q
git add src/qualock/protocols tests/unit/test_paired_change_models.py tests/unit/test_paired_change_design.py
git commit -m "feat: define paired-change protocol models"
```

### Task 6: Persist `PairedChangeRunV1` at Check Time

**Files:** create `src/qualock/protocols/paired_change/run_sidecar.py`; modify `src/qualock/commands.py`; test `tests/unit/test_paired_change_run_sidecar.py` and `tests/unit/test_commands.py`.

**Interfaces:** `build_protocol_design(...) -> ProtocolDesignV1` is called before the first attempt. `QualificationExecutor.run()` receives a trace sink plus the design digest. After normal qualification/provenance artifacts exist, `build_paired_change_run(...) -> PairedChangeRunV1` creates the prospective sidecar; `read_paired_change_run(path) -> PairedChangeRunV1` performs bounded strict parsing; and `write_paired_change_run(path, value) -> Path` writes canonical JSON with create-new semantics.

- [ ] **Step 1: Add sidecar builder/writer red tests.** Require deterministic bytes, exact `qualification_id`, state/change-set digests, frozen design digest, pair contexts bound to `events_sha256`, and explicit absence of `evidence_manifest_sha256`.

- [ ] **Step 2: Add a legacy-canary vector.** When `paired_change` metadata is absent, the sidecar still records the experiment but the canary design has no material declaration; do not synthesize an empty tuple that could later pass controls.

- [ ] **Step 3: Run sidecar tests and observe missing implementation.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_run_sidecar.py -q
```

- [ ] **Step 4: Implement sidecar build/read/write.** Pair trace records by `(canary_id, repetition)` and reject duplicate/missing sides structurally. Use existing canonical JSON helpers and bounded reads.

- [ ] **Step 5: Integrate `execute_check()`.** After backend creation and before the first attempt, build protocol design using suite/config fingerprints, canary declarations, and `ProtocolAwareQualificationBackend.control_profiles()` when available. Compute `protocol_design_sha256`, then pass both a fresh trace list and that digest as `trace_design_sha256` into the executor. After writing legacy qualification artifacts and evidence provenance, write `.qualock/results/<qid>/paired-change-run-v1.json`.

If a custom backend is not protocol-aware, expected/applied profile fields remain unavailable; check still succeeds, but later causal conditions become `UNKNOWN`.

- [ ] **Step 6: Add command integration tests.** Assert new checks write the sidecar; existing result/verdict/report assertions stay unchanged; custom legacy backend creates a sidecar with unavailable controls rather than breaking qualification.

- [ ] **Step 7: Verify focused command/storage tests and commit.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_run_sidecar.py tests/unit/test_commands.py tests/unit/test_storage.py -q
git add src/qualock/protocols/paired_change/run_sidecar.py src/qualock/commands.py tests/unit/test_paired_change_run_sidecar.py tests/unit/test_commands.py
git commit -m "feat: persist paired-change run evidence"
```

### Task 7: Materialize Manifest-Bound Protocol Evidence During Export

**Files:** create `src/qualock/protocols/paired_change/materialize.py`; modify `src/qualock/evidence/export.py`; test `tests/unit/test_paired_change_materialize.py` and `tests/unit/test_evidence_export.py`.

**Interfaces:** `materialize_protocol_evidence(run: PairedChangeRunV1, bundle: VerifiedEvidenceBundle) -> ProtocolEvidenceV1` derives the portable overlay from a validated run sidecar plus the just-created verified Evidence Bundle V1, using `bundle.manifest_sha256` as the exact binding. A legacy qualification with no sidecar exports only the V1 bundle. A protocol-enabled export writes companion directory `<BUNDLE>.paired-change-v1/` containing only `protocol-evidence.json` at this stage.

- [ ] **Step 1: Add materialization red tests.** Require matching qualification ID, candidate/baseline identity, canary fingerprints, repetition layout, `events_sha256`, run order, and exact manifest digest. Mismatches raise a stable protocol materialization error rather than creating portable evidence.

- [ ] **Step 2: Add export lifecycle red tests.** Cover: legacy export creates no companion; paired-change export keeps the exact old bundle path/inventory; companion contains protocol evidence; `evidence_manifest_sha256 == exported.manifest_sha256`; pre-existing companion path rejects safely.

- [ ] **Step 3: Run tests and observe failure.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_materialize.py tests/unit/test_evidence_export.py -q
```

- [ ] **Step 4: Implement pure materialization.** Reuse the already projected public report/provenance/manifest data from export; do not reload current project state inside `materialize.py`. Cross-bind each run-sidecar attempt to the public attempt by canary/side/repetition and `events_sha256`.

- [ ] **Step 5: Integrate export without touching strict bundle inventory.** Build and validate companion bytes before publication. Publish the existing bundle at `destination`; publish the companion separately only when a sidecar exists. If companion publication fails after bundle publication, remove the newly-owned bundle/companion best-effort and return an export error rather than reporting partial causal export success.

Extend `ExportedEvidenceBundle` with `protocol_path: Path | None = None`; preserve existing fields and render behavior.

- [ ] **Step 6: Verify bundle backward compatibility and determinism.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_materialize.py tests/unit/test_evidence_export.py tests/unit/test_evidence_verify.py tests/unit/test_evidence_bundle_io.py -q
```

- [ ] **Step 7: Commit.**

```bash
git add src/qualock/protocols/paired_change/materialize.py src/qualock/evidence/export.py tests/unit/test_paired_change_materialize.py tests/unit/test_evidence_export.py
git commit -m "feat: bind protocol evidence to exported bundles"
```

### Task 8: Evaluate the Ten Validity Conditions and Derive Claims

**Files:** create `src/qualock/protocols/paired_change/conditions.py` and `claims.py`; create `tests/unit/test_paired_change_conditions.py` and `test_paired_change_claims.py`.

**Interfaces:** `evaluate_qualification_conditions(bundle, evidence) -> tuple[ProtocolConditionV1, ...]` returns exactly `EvidenceBound`, `DesignFrozen`, and `StateBound` in protocol order. `evaluate_canary_conditions(bundle, evidence, canary_id) -> tuple[ProtocolConditionV1, ...]` returns exactly `PreparationEquivalent`, `BaselineStable`, `AttemptsComplete`, `AttemptsIsolated`, `OrderValid`, `TemporalPairValid`, and `MaterialDimensionsControlled`. `derive_canary_claim(...) -> CanaryClaimV1` is a pure deterministic transition.

- [ ] **Step 1: Write a table-driven red test for every gate.** Each gate gets structurally meaningful `TRUE`, `FALSE`, and `UNKNOWN` vectors. Use the stable reasons from the design spec: `EvidenceVerified/EvidenceMismatch/EvidenceUnavailable`, `DesignVerified/DesignChanged/DesignFreezeUnavailable`, `StateVerified/StateMismatch/StateIdentityIncomplete`, `PreparationVerified/PreparationMismatch/PreparationUnverified`, `BaselineStable/BaselineViolation/BaselineEvidenceIncomplete`, `AttemptsComplete/AttemptMissing/AttemptInvalid/AttemptLayoutUnknown`, `IsolationVerified/IsolationReuseDetected/IsolationUnverified`, `OrderVerified/PairLayoutInvalid/OrderImbalanced/OrderUnknown`, `TemporalPairVerified/TemporalGapExceeded/TemporalEvidenceMissing`, and `MaterialControlsVerified/UnexpectedMaterialChange/RequiredDimensionOpaque`.

- [ ] **Step 2: Run the condition tests and confirm they fail before implementation.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_paired_change_conditions.py -q
```

- [ ] **Step 3: Implement gate evaluators as pure functions.** Do not read files, clocks, environment, Docker, source checkout, or provider state. `StateBound` requires bundle/protocol baseline and candidate dependency identities to agree. `DesignFrozen` requires the recomputed design digest, suite/config fingerprints, canary fingerprints, repetition policy, and every attempt's prospective design binding to agree. `PreparationEquivalent` requires the same prepared workload identity plus matching expected/applied preparation profile. `BaselineStable` is `TRUE` only when every required baseline attempt is valid and successful. `AttemptsComplete` is `TRUE` only with the exact expected layout and all required attempts valid. `AttemptsIsolated` requires distinct fresh isolation-instance digests. Missing required evidence maps to `UNKNOWN`; contradictory but structurally valid evidence maps to `FALSE`; malformed artifacts remain verifier errors handled in Task 9.

- [ ] **Step 4: Make `OrderValid` derive orientation from the bound public run order.** Require adjacent A/B pair slots, one side each per repetition, strict alternation, and orientation-count difference at most one. Never trust a producer-supplied validity boolean.

- [ ] **Step 5: Make `TemporalPairValid` compute `start(second) - finish(first)` from monotonic offsets and the frozen per-canary `max_pair_gap_ms`.** Missing offsets are `UNKNOWN`; negative/overlapping layout inconsistent with scheduler semantics is `FALSE` with `PairLayoutInvalid`.
- [ ] **Step 6: Implement `MaterialDimensionsControlled` against the derived change-set.** For every declared material dimension: allow it if it is in the derived treatment change-set; otherwise require verified equality of the bound baseline/candidate control value. A known unexpected delta is `FALSE`; an opaque/missing required dimension is `UNKNOWN`. A missing declaration is `UNKNOWN`, never an empty set.

- [ ] **Step 7: Write exhaustive claim-transition red tests.** Cover all-gates-true baseline-pass/candidate-pass → `NO_REGRESSION_OBSERVED`; all-gates-true baseline-pass/candidate-violate → `ATTRIBUTABLE_CHANGESET`; any required `FALSE` or `UNKNOWN` → `UNRESOLVED`; unstable/incomplete baseline → `UNRESOLVED`.

- [ ] **Step 8: Implement the pure claim transition and canonical condition ordering.** Qualification-wide non-`TRUE` conditions force every canary claim to `UNRESOLVED`. Per-canary conditions affect only that canary.

- [ ] **Step 9: Verify focused tests and commit.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_paired_change_conditions.py tests/unit/test_paired_change_claims.py -q
git add src/qualock/protocols/paired_change/conditions.py src/qualock/protocols/paired_change/claims.py tests/unit/test_paired_change_conditions.py tests/unit/test_paired_change_claims.py
git commit -m "feat: evaluate paired-change causal conditions"
```

### Task 9: Build the Secure Offline Verifier and Recomputable Receipt

**Files:** create `src/qualock/protocols/paired_change/io.py`, `verify.py`, and `render.py`; modify `src/qualock/cli.py`; create `tests/unit/test_paired_change_io.py`, `test_paired_change_verify.py`, and `test_paired_change_cli.py`.

**Interfaces:** `read_protocol_evidence(protocol_path: Path) -> ProtocolEvidenceV1` and `read_claim_receipt(protocol_path: Path) -> ClaimReceiptV1 | None` use bounded no-follow reads of fixed companion filenames. `verify_paired_change(bundle_path: Path, protocol_path: Path) -> ClaimReceiptV1` first calls existing `verify_evidence_bundle(bundle_path)`, then recomputes all conditions/claims/receipt and automatically compares `protocol_path / "claim-receipt.json"` when present. `write_claim_receipt(protocol_path: Path, receipt: ClaimReceiptV1) -> Path` writes that fixed filename with canonical create-new semantics.
- [ ] **Step 1: Write untrusted-filesystem red tests.** Reject a protocol directory symlink, symlinked/non-regular protocol files, oversized files, missing `protocol-evidence.json`, unexpected filenames when a stored receipt is being checked, malformed UTF-8/JSON, and replacement races detectable from opened-handle metadata. Mirror the Evidence Bundle V1 no-follow/bounded-read posture rather than using `Path.read_text()` directly.

- [ ] **Step 2: Define stable structural verification errors.** Add `PairedChangeVerificationReason` with exactly `MALFORMED_PROTOCOL_EVIDENCE`, `UNSUPPORTED_PROTOCOL`, `EVIDENCE_BINDING_MISMATCH`, `STATE_BINDING_MISMATCH`, `PAIR_LAYOUT_MISMATCH`, `DIGEST_MISMATCH`, `MALFORMED_RECEIPT`, and `CLAIM_MISMATCH`; add `PairedChangeVerificationError(reason, field)`.

- [ ] **Step 3: Implement bounded no-follow protocol I/O.** Allow fixed names `protocol-evidence.json` and optional `claim-receipt.json`; canonical writes use `canonical_json_file_bytes()`. Do not import or weaken Evidence Bundle V1's fixed inventory.

- [ ] **Step 4: Write verifier red tests for each structural error category.** Include wrong manifest digest, wrong qualification ID, state digest contradiction, duplicate/impossible pair layout, protocol/design digest tamper, malformed receipt, and a manually upgraded stored claim.

- [ ] **Step 5: Implement `verify_paired_change()`.** Sequence is: verify V1 bundle → parse portable protocol evidence → verify protocol/schema/digests/bindings → evaluate conditions → derive claims → construct canonical `ClaimReceiptV1` → compare the stored companion receipt when present. Structural errors never become `UNRESOLVED`.

- [ ] **Step 6: Prove offline purity and determinism.** Tests monkeypatch network/subprocess/Docker/resolver entry points to raise if called; delete the original project checkout fixture before verification; run verification twice and require equal model dumps and equal canonical receipt bytes.

- [ ] **Step 7: Add minimal CLI presentation without changing workflow verdict semantics.** Add:

```text
qualock evidence verify-claim BUNDLE --protocol PROTOCOL_DIR [--write-receipt]
```

Without `--write-receipt`, the command is read-only and automatically checks an existing companion `claim-receipt.json`. With `--write-receipt`, it first verifies/recomputes, then writes `PROTOCOL_DIR/claim-receipt.json` using create-new semantics; an existing receipt is never overwritten. A structurally valid receipt exits `0` for all claim classes, including `UNRESOLVED`, because attribution is evidence rather than an organizational allow/deny decision. Stable verification/input errors exit `3`; unexpected internal failures exit `1`. Output lists qualification ID and each `canary_id: CLAIM_CLASS` and never changes existing `evidence verify` behavior.
- [ ] **Step 8: Run verifier/CLI tests plus existing evidence CLI tests.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_paired_change_io.py tests/unit/test_paired_change_verify.py \
  tests/unit/test_paired_change_cli.py tests/unit/test_evidence_cli.py tests/unit/test_evidence_verify.py -q
```

Expected: PASS; existing `qualock evidence verify` exit/output behavior remains unchanged.

- [ ] **Step 9: Commit.**

```bash
git add src/qualock/protocols/paired_change/io.py src/qualock/protocols/paired_change/verify.py src/qualock/protocols/paired_change/render.py src/qualock/cli.py tests/unit/test_paired_change_io.py tests/unit/test_paired_change_verify.py tests/unit/test_paired_change_cli.py
git commit -m "feat: verify paired-change claims offline"
```

### Task 10: Add Conformance Vectors, End-to-End Proof, and Final Regression Gates

**Files:** create `tests/fixtures/paired_change_v1/` fixture directories, `tests/unit/test_paired_change_conformance.py`, and `tests/integration/test_paired_change_e2e.py`; modify only protocol/evidence implementation files if these tests expose a real conformance defect.

**Interfaces:** Each golden vector contains immutable canonical bundle/protocol input files plus `expected.json` containing ordered condition statuses/reasons and claim classes. Fixtures never contain provider credentials, absolute local paths, or network dependencies.

- [ ] **Step 1: Create the minimum golden vector set from the design spec.** Include `attributable-clean`, `no-regression-clean`, `unstable-baseline`, `missing-codex-support`, `preparation-unknown`, `isolation-reuse`, `order-imbalanced`, `temporal-gap`, `unexpected-material-change`, `opaque-material-dimension`, `manifest-binding-mismatch`, and `tampered-receipt`.

- [ ] **Step 2: Write one parametrized conformance test.** For valid epistemic vectors, compare the verifier's ordered conditions/reasons/claims to `expected.json`. For structurally invalid vectors, compare the exact `PairedChangeVerificationReason`. Assert no adversarial vector yields `ATTRIBUTABLE_CHANGESET` unless all ten required invariants are `TRUE`.
- [ ] **Step 3: Add a synthetic/local deterministic end-to-end regression test.** Reuse fake qualification infrastructure: baseline attempts are valid/pass, candidate attempts are valid/fail, protocol-aware fake backend exposes fixed verified control profiles and fresh isolation IDs. Run check-time construction → write sidecar → export Evidence Bundle V1 → materialize companion → `verify_paired_change()`. Assert all ten conditions are `TRUE` and the canary claim is `ATTRIBUTABLE_CHANGESET`.

- [ ] **Step 4: Add the no-regression twin.** Keep the same frozen design/controls but make both arms valid/pass; require `NO_REGRESSION_OBSERVED`. Do not use an authenticated agent/provider run.

- [ ] **Step 5: Re-run backward-compatibility suites that cover changed substrate.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_schedule.py tests/unit/test_commands.py tests/unit/test_github_pr_commands.py \
  tests/unit/test_docker_backend.py tests/unit/test_host_backend.py \
  tests/unit/test_evidence_provenance.py tests/unit/test_evidence_export.py \
  tests/unit/test_evidence_verify.py tests/unit/test_evidence_bundle_models.py \
  tests/unit/test_evidence_bundle_io.py tests/unit/test_budgeted_qualification.py \
  tests/unit/test_token_budgeted_qualification.py tests/integration/test_fake_qualification.py \
  tests/integration/test_evidence_bundle_e2e.py -q
```

Expected: PASS; legacy Evidence Bundle V1 fixtures still verify and current `PASS/WARN/BLOCK/INCOMPLETE` semantics are unchanged.

- [ ] **Step 6: Run all paired-change tests.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_paired_change_*.py tests/integration/test_paired_change_e2e.py -q
```

Expected: PASS.
- [ ] **Step 7: Run fresh full-suite and static verification.**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src tests
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock
```

Expected: all commands exit `0`. Do not substitute a prior run or a partial suite for this gate.

- [ ] **Step 8: Commit the conformance/e2e vectors.**

```bash
git add tests/fixtures/paired_change_v1 tests/unit/test_paired_change_conformance.py tests/integration/test_paired_change_e2e.py
git commit -m "test: certify paired-change protocol conformance"
```

- [ ] **Step 9: Run a fresh independent review against the design spec and this plan.** Reviewer must inspect the complete diff and verification output, with special attention to fail-closed UNKNOWN semantics, legacy bundle/fingerprint compatibility, untrusted filesystem handling, and whether any stored validity/claim boolean is trusted rather than recomputed. Any Critical or Important finding blocks closure.

- [ ] **Step 10: If review requires changes, reproduce each finding with a failing test before fixing it.** Re-run the focused test, full paired-change suite, full repository suite, Ruff, and mypy after the fix, then obtain a fresh reviewer verdict. Commit review-driven fixes separately with a narrowly scoped message.

## Completion Evidence

Before declaring P0 implemented, record fresh output for: full `pytest`, Ruff, mypy, the paired-change end-to-end test, and the independent reviewer verdict. Confirm `git status --short` is clean. Do not push, create a PR, merge, tag, release, publish, install dependencies, or run authenticated provider qualifications unless the user separately authorizes that action.
