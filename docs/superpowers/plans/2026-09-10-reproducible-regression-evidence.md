# Reproducible Regression Evidence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deterministic, portable evidence bundle that can be exported from a completed QuaLock check and verified later from a standalone directory without network access, provider credentials, Docker, project execution, or trust in stored verdict fields.

**Architecture:** Persist exact run-time provenance prospectively at `qualock check`, then build a closed flat-directory bundle with strict typed payloads and bounded no-follow I/O. The offline verifier derives aggregates and reuses the existing pure qualification policy; the exporter constructs only publication-safe projections and must self-verify its temporary bundle before atomic publication. Case-study discovery/execution remains a separate phase after verifier delivery.

**Tech Stack:** Python 3.11+, Pydantic, Typer, `pathlib`, SHA-256/canonical JSON, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-10-reproducible-regression-evidence-design.md`

**Date:** 2026-09-10
**Batch:** #45
**Branch:** `feat/reproducible-regression-evidence`
**Base:** `1d586cda71144d11df88885cdafb86300c616e93`

The implementation phase ends with the verifier merge-ready. The case-study discovery phase then selects one concrete stable-version pair and writes a separately reviewed execution runbook. Authenticated provider execution and public evidence publication remain later explicit shared-effect boundaries.

## Global Constraints

- Use `/home/pacmap/qualock-easy/.venv/bin/python` for Python and pytest.
- Use `/home/pacmap/qualock-easy/.venv/bin/ruff` for Ruff.
- TDD is mandatory for production tasks: observe the intended RED before writing production code.
- Fresh implementer per task; fresh independent reviewer after each task.
- Reviewer is read-only. Controller does not edit production code while a reviewer owns the task.
- Critical/Important findings block task completion. Minor may be deferred only with an explicit reviewer ruling that the spec/security contract is unaffected.
- No dependency install, provider-authenticated run, push, PR, merge, tag, release, or publish without the matching explicit operator authorization.

---

## Protected scope

Unless a task below explicitly names a file, these areas are protected from semantic changes:

- `src/qualock/qualification/policy.py` — verifier must reuse it, not modify it;
- `src/qualock/run/`, agent adapters/resolvers, and source materialization;
- release monitor, scheduler, version bisect, and GitHub PR orchestration;
- pricing/history analysis semantics;
- baseline/config/canary schema versions;
- dependency metadata (`pyproject.toml` and lock/requirements files).

Allowed shared files are narrowly limited to `src/qualock/project.py`, `src/qualock/commands.py`, `src/qualock/cli.py`, and the evidence package/tests named by the owning task.

## Task 1 — Persist prospective evidence provenance

**Files**

- Modify: `src/qualock/project.py`
- Create: `src/qualock/evidence/provenance.py`
- Modify: `src/qualock/commands.py`
- Create: `tests/unit/test_evidence_provenance.py`
- Modify: `tests/unit/test_project_fingerprint.py`
- Modify: `tests/unit/test_commands.py`
- Modify: `tests/unit/test_cli.py`

**Produces:** a strict schema-v1 `evidence-provenance.json` for every new completed check, containing the exact resolved baseline and candidate runtime identities plus safe run provenance.
- [ ] **Step 1.1 — RED: stable canary fingerprint extraction**

Add tests proving a new `canary_fingerprint(canary: CanarySpec) -> str` is machine-path independent, changes when any full canary definition field or grader patch bytes change, and does not change the existing `suite_fingerprint()` value for identical inputs.

Required `project.py` interfaces are `_canary_fingerprint_payload(canary: CanarySpec) -> dict[str, object]` and `canary_fingerprint(canary: CanarySpec) -> str`. The payload helper must implement exactly the normalization currently embedded in `suite_fingerprint()`; the public helper returns `sha256_canonical(_canary_fingerprint_payload(canary))`.

Refactor `suite_fingerprint()` to hash the sorted list of these normalized payloads, not a list of individual digest strings. This preserves the existing suite SHA contract exactly.

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_project_fingerprint.py
```

Expected RED: missing helper. Expected GREEN: all fingerprint tests pass and the pre-existing suite tests remain value-equivalent.

- [ ] **Step 1.2 — RED: strict provenance models and canonical I/O**

In `provenance.py`, define frozen `extra="forbid"` Pydantic models for `RuntimeAgentIdentity`, `ProvenanceModelPin`, `CanaryRuntimeProvenance`, and `EvidenceProvenance` (`schema_version: Literal[1]`). `EvidenceProvenance` contains `run_qualock_version`, populated from the exact `qualock.__version__` executing `execute_check`; it is distinct from the baseline lock's historical `qualock_version`. Validate lowercase 64-hex SHA-256 fields, exact 40-hex source SHAs, positive repetitions, and ``sha256:` followed by exactly 64 lowercase hex characters` prepared-image digests. `CanaryRuntimeProvenance` stores `repository_url_sha256`, never the raw repository locator. Gemini requires a non-null valid support digest for both runtime identities; other agents may use null.
Required helpers are `build_evidence_provenance(*, lock: BaselineLock, baseline_binary: AgentBinary, candidate_binary: AgentBinary, config: QualockConfig, canaries: Sequence[CanarySpec], result: QualificationResult) -> EvidenceProvenance`, `write_evidence_provenance(path: Path, value: EvidenceProvenance) -> Path`, and `read_evidence_provenance(path: Path, *, max_bytes: int = 1_048_576) -> EvidenceProvenance`.

`build_evidence_provenance` must set `run_qualock_version = qualock.__version__`, compute `baseline_lock_sha256` from canonical parsed lock data, use `agent_support_fingerprint()` for runtime support identity, reuse `canary_fingerprint()`, set each `repository_url_sha256 = sha256_canonical(canary.repository.url)`, and compute `run_order_sha256` with `sha256_canonical(result.run_order)`. It must not require `run_qualock_version == lock.qualock_version`; the baseline may have been created by an earlier QuaLock build. It copies no raw repository locator, prompt/task/setup/grader/env/event text.

`write_evidence_provenance` must create `evidence-provenance.json` exactly once, canonical JSON plus trailing newline, and refuse overwrite. Read errors collapse to fixed provenance-domain messages without raw payloads.

Tests must include extra fields, malformed hashes, Gemini missing support, non-Gemini null support, deterministic bytes, overwrite refusal, bounded read, exact repository-URL hashing, `run_qualock_version` captured from the current package while differing safely from `lock.qualock_version`, and a static forbidden-key/value sentinel scan proving a credential-bearing repository URL is absent from sidecar bytes.

Run RED then GREEN:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_evidence_provenance.py tests/unit/test_project_fingerprint.py
```

- [ ] **Step 1.3 — RED: make provenance persistence mandatory for `execute_check`**

Add command tests proving `execute_check()` writes report artifacts first, then provenance using the exact already-resolved baseline/candidate binaries, then pricing best-effort. A provenance write/build failure preserves the existing report directory but raises `CommandError`; pricing is not attempted after that failure. No second resolver call is permitted.
Add CLI regression proving a provenance `CommandError` maps to exit `3` and prints no `SAFE TO KEEP`, `DON'T KEEP`, `CHECK COULD NOT FINISH`, technical verdict, or success recommendation. Existing BLOCK/INCOMPLETE/check output remains unchanged when provenance succeeds.

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_provenance.py \
  tests/unit/test_project_fingerprint.py \
  tests/unit/test_commands.py \
  tests/unit/test_cli.py \
  tests/unit/test_storage.py
```

Then:

```bash
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/project.py src/qualock/evidence/provenance.py src/qualock/commands.py tests/unit/test_evidence_provenance.py tests/unit/test_project_fingerprint.py tests/unit/test_commands.py tests/unit/test_cli.py
git diff --check
```

**Review gate:** independent reviewer must confirm no resolver re-run, exact candidate identity capture, Gemini support enforcement, no sensitive provenance field, suite fingerprint compatibility, report-preservation/error ordering, and no pricing-policy change. Require C0/I0.

**Commit:** `feat: persist qualification evidence provenance`

## Task 2 — Define the strict bundle and filesystem contract

**Files**

- Create: `src/qualock/evidence/bundle_models.py`
- Create: `src/qualock/evidence/bundle_io.py`
- Create: `tests/unit/test_evidence_bundle_models.py`
- Create: `tests/unit/test_evidence_bundle_io.py`

This task owns parsing, canonical bytes, file bounds, and untrusted filesystem traversal only. It does not verify qualification semantics yet.
- [ ] **Step 2.1 — RED: strict public models**

Define frozen `extra="forbid"` models for `PublicUsage`, `PublicAttempt`, `PublicExecution`, `PublicReport`, `PublicQualification`, strict bundle-local mirrors of baseline agent/model/canary-stability plus `PublicBaselineLock`, `PublicCanary`, `PublicCanaries`, `BundleCompleteness`, `BundleFileRecord`, `EvidenceManifest`, and `VerifiedEvidenceBundle`. `PublicAttempt` has exactly `side`, `repetition`, `success`, `valid`, `duration_ms`, `usage`, and `events_sha256`; it has no `invalid_reason`, `protected_path_violations`, or raw events field. `PublicUsage` has exactly the six canonical `Usage` fields and rejects booleans/negative integers. Reuse Task 1 `RuntimeAgentIdentity` for provenance/manifest agent identity rather than defining conflicting semantics. `EvidenceManifest` requires distinct `run_qualock_version` and `exporter_qualock_version`; `PublicBaselineLock.qualock_version` remains the baseline-creation version, and no equality among those three values is implied. Also expose `publication_safe_repository_url(value: str) -> str`: accept only at most 2048 UTF-8 bytes, `http`/`https`, non-empty host, no username/password userinfo, query, fragment, or ASCII control character; reject local paths, `file:`, SSH/SCP locators, and every other form with `UNSAFE_REPOSITORY_URL`. Return the exact input unchanged on success; never sanitize.

Define:

```python
class EvidenceBundleReason(str, Enum):
    MALFORMED_MANIFEST = "malformed_manifest"
    UNSAFE_PATH = "unsafe_path"
    UNSAFE_REPOSITORY_URL = "unsafe_repository_url"
    INVENTORY_MISMATCH = "inventory_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED_PAYLOAD = "malformed_payload"
    IDENTITY_MISMATCH = "identity_mismatch"
    INVALID_GEMINI_SUPPORT = "invalid_gemini_support"
    CANARY_PROVENANCE_MISMATCH = "canary_provenance_mismatch"
    ATTEMPT_LAYOUT_MISMATCH = "attempt_layout_mismatch"
    COMPLETENESS_MISMATCH = "completeness_mismatch"
    VERDICT_MISMATCH = "verdict_mismatch"

class EvidenceBundleError(ValueError):
    reason: EvidenceBundleReason
```

Error messages may name only a fixed logical file/field label; never interpolate raw JSON, event content, or arbitrary bundle strings.

Pin fixed payload names to `report.json`, `qualification.json`, `baseline.lock`, `provenance.json`, `canaries.json`, with optional `pricing.json`; `manifest.json` is required but not self-inventoried. File caps are exact: manifest 2 MiB; report 32 MiB; qualification 4 MiB; baseline 4 MiB; provenance 8 MiB; canaries 8 MiB; pricing 32 MiB. Bound qualification/canary/model/version/reason-like text fields to 4096 UTF-8 bytes, repository URLs to 2048 bytes, canary collections to 4096 records, and attempts per execution to 8192. Reject unknown schema/fields, bool-as-int, negative counters, invalid enums/hashes, and over-bound values before semantic verification.

- [ ] **Step 2.2 — RED: safe flat directory inspection and bounded I/O**

In `bundle_io.py`, implement the exact helpers `canonical_json_file_bytes(value: object) -> bytes`, `inspect_bundle_files(root: Path) -> tuple[str, ...]`, `read_bounded_regular_file(root: Path, name: str, *, max_bytes: int) -> bytes`, and `sha256_regular_file(root: Path, name: str, *, max_bytes: int) -> tuple[str, int]`.
`inspect_bundle_files()` must `lstat` without following links, reject a symlink root/entry, FIFO/device/socket, nested directory, absolute/dot/parent/backslash spelling, and any filename outside the fixed V1 set. Directory listing and returned names are lexical/deterministic.

`read_bounded_regular_file()` must use handle-based no-follow I/O; `Path.read_*`/plain `open()` is forbidden for untrusted bundle payloads. On POSIX, open the validated root as a directory descriptor and open each fixed filename relative to it with `O_NOFOLLOW`, then require `fstat()` regular-file type before reading. On Windows, use `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT`, reject `FILE_ATTRIBUTE_REPARSE_POINT`, compare `GetFinalPathNameByHandleW` for the opened payload against the separately opened/validated root handle plus exact filename, then read through the owned handle. If the required no-follow/handle-validation primitive is unavailable, fail closed with `UNSAFE_PATH`; do not fall back to a link-following open. Enforce the byte cap from trusted handle metadata and while reading.

Tests include missing root, root-as-file/link, payload symlink, FIFO (POSIX-gated), nested directory, extra file, backslash filename on POSIX, each exact per-file cap boundary, over-bound collection/text fields, replacement-to-link race, and exact canonical JSON bytes. Model tests separately prove `baseline.lock` nested extras are rejected and that `PublicAttempt` rejects `invalid_reason`, `protected_path_violations`, and raw event fields.

Run RED/GREEN:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_bundle_models.py tests/unit/test_evidence_bundle_io.py
```

Then Ruff + `git diff --check` on owned files.

**Review gate:** security reviewer checks path containment, no link following, bounded reads, closed schemas, stable reason categories, no arbitrary payload echo, and Windows/POSIX portability. Require C0/I0.

**Commit:** `feat: add strict evidence bundle contract`

## Task 3 — Verify standalone bundles offline

**Files**

- Create: `src/qualock/evidence/verify.py`
- Create: `tests/unit/evidence_bundle_fixtures.py`
- Create: `tests/unit/test_evidence_verify.py`
- Modify: `src/qualock/pricing/sidecar.py` — extract the pure parser only; no pricing semantic change

This task starts from manually constructed valid fixture bundles. It must not depend on the exporter.
- [ ] **Step 3.1 — RED: inventory and cross-file identity verification**

Define exactly one public verifier entry point: `verify_evidence_bundle(root: Path) -> VerifiedEvidenceBundle`.

Order is binding: inspect filesystem → strict manifest → exact actual/inventory equality → hash/size every payload → strict payload parse → cross-file identities → attempt/policy semantics → optional pricing.

Recompute and compare: manifest digest for display, normalized baseline-lock SHA, qualification ID, baseline/candidate versions, `manifest.run_qualock_version == provenance.run_qualock_version`, both runtime identities, model pin, suite/config digests, run-order digest, canary IDs, publication-safe repository URLs and `sha256_canonical(repository_url)` bindings, source SHAs, critical flags, canary fingerprints, and prepared-image digests. Validate `manifest.exporter_qualock_version` structurally but do not require it to equal the run version or `baseline.lock.qualock_version`. Do not consult current project state.

Gemini baseline and candidate identities both require valid support SHA; non-Gemini null support follows the existing runtime identity contract.

- [ ] **Step 3.2 — RED: derive attempt aggregates and recompute policy**

Never trust stored execution aggregates. For each execution, derive baseline/candidate repetition sets and valid/success counts from public attempts. Require unique `(side, repetition)` slots, sides exactly `baseline|candidate`, repetition within `1..expected_repetitions`, and attempt fields structurally valid.

Build existing `CanaryAggregate` objects and call only the existing pure `qualify_canary` and `qualify_suite` policy functions.

Require recomputed per-canary verdict/reason/stability and overall verdict/reasons to match public report, qualification metadata, and manifest. `qualification/policy.py` must remain byte-unchanged.

- [ ] **Step 3.3 — RED: completeness and optional pricing**

Recompute completeness independently. A canary with missing paired slots must force overall `INCOMPLETE`; incomplete evidence must never verify as `PASS` or `BLOCK`. Budget fields must match report and qualification metadata and be compatible with the recorded stopped schedule; do not infer skipped attempts as successes or failures.
If `pricing.json` is present, refactor `pricing/sidecar.py` only enough to expose the existing body validation as a pure, no-I/O entry point used by both history loading and the bundle verifier. The exact interface is `parse_pricing_sidecar_payload(loaded: LoadedReport, payload: object) -> PricingSidecar`. It validates the top-level object, schema/version, qualification ID, provider/model/rate semantics, and `usage_detail_trust` identities against `loaded`; it performs no file read, clock, network, catalog resolution, or mutation. `_load_sidecar()` remains responsible only for local file read/JSON decode and mapping parse failures to the existing fixed pricing-load reasons, then delegates to this function. The verifier constructs `LoadedReport` from its already-validated public report attempts and the bundle root solely to reuse those identity checks; `qualification_dir` is not a trust input.

Existing pricing loader tests must prove byte-for-byte-equivalent accepted/rejected semantics and unchanged `PricingLoadFailure.reason` values before/after the refactor. Bundle verification checks its own file hash plus pricing schema and qualification ID; pricing values never enter `qualify_canary()` or `qualify_suite()`.

Add a structural no-side-effect test that monkeypatches `socket.socket.connect`, `subprocess.run/Popen`, `run_process`, and Docker entry points to raise, then verifies a valid copied fixture. Also snapshot bundle bytes/mtimes before and after verification and require no write.

Tamper matrix must cover every required payload class, manifest fields, aggregate/verdict manipulation, cross-file rebind, unsafe filesystem shapes, and optional pricing. Error assertions use only fixed reason categories and bounded logical labels.

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_bundle_models.py \
  tests/unit/test_evidence_bundle_io.py \
  tests/unit/test_evidence_verify.py \
  tests/unit/test_policy.py \
  tests/unit/test_pricing_sidecar_loader.py
```

**Review gate:** independent security reviewer checks pure offline behavior, no stored-verdict trust, existing policy reuse, complete identity binding, completeness fail-closed semantics, optional pricing isolation, and fixed-safe errors. Require C0/I0.

**Commit:** `feat: verify evidence bundles offline`

## Task 4 — Export deterministic portable bundles

**Files**

- Create: `src/qualock/evidence/export.py`
- Create: `tests/unit/test_evidence_export.py`
- Reuse Task 3 fixture helpers; no new public format.
- [ ] **Step 4.1 — RED: strict source qualification loading**

Define frozen `ExportedEvidenceBundle` with fields `path: Path`, `qualification_id: str`, and `manifest_sha256: str`. Define the public exporter as `export_evidence_bundle(root: Path, qualification_id: str, destination: Path, *, created_at: datetime | None = None) -> ExportedEvidenceBundle`.

Validate `qualification_id` as one safe existing result-directory name, not an arbitrary path. Require `report.json`, `qualification.json`, and `evidence-provenance.json`; reject baseline-only/legacy/malformed source results. Load current project config/canaries and current baseline lock through existing loaders, then require exact suite/config freshness plus equality with the persisted provenance sidecar. Export never resolves an agent or invokes a backend.

Tests must install fail-if-called sentinels on resolver, release source, network, Docker, subprocess/project execution paths and still export successfully from a valid synthetic project.

- [ ] **Step 4.2 — RED: safe public projections**

Construct `report.json` field-by-field, never by copying an arbitrary local report object. Each attempt emits exactly `side`, `repetition`, `success`, `valid`, `duration_ms`, the six canonical `usage` fields, and `events_sha256 = sha256(events_jsonl.encode("utf-8")).hexdigest()`. Never emit raw `events_jsonl`, `invalid_reason`, or `protected_path_violations`; the latter two may contain exception/path text and are not policy inputs. Execution aggregate/verdict/reason fields are emitted only after deriving them from the public attempts through the existing qualification policy, so arbitrary local report reason text is never copied.

Construct `canaries.json` from current loaded canaries plus persisted provenance. For each canary, require `sha256_canonical(canary.repository.url)` to equal persisted `repository_url_sha256`, then call `publication_safe_repository_url()` on that exact string. Copy only ID, critical flag, the unchanged approved repository URL, `repository_url_sha256`, base SHA, canary fingerprint, prepared digest, and expected repetitions. Unsafe repository locators fail export before destination creation; never sanitize or substitute them. Add sentinel tests with a userinfo/query-token repository URL plus secrets in task/setup/grader path/grader command/protected paths and assert none appear anywhere in output bundle bytes.

Normalize `baseline.lock`, `provenance.json`, and `qualification.json` through strict typed models. Include `pricing.json` only when an existing sidecar parses successfully with Task 3's pure parser. An absent or malformed local pricing sidecar is omitted because pricing is advisory; it must not block export of otherwise valid quality evidence.
- [ ] **Step 4.3 — RED: canonical manifest, self-verification, and atomic publish**

Write payload files in a sibling temporary directory. Hash exact canonical payload bytes and build the manifest with sorted inventory, exact byte sizes, `run_qualock_version` copied unchanged from persisted provenance, `exporter_qualock_version = qualock.__version__` captured once by the exporter, both runtime identities, model pin, lock/suite/config/run-order digests, derived completeness, and sorted canary records. Do not require either value to equal `baseline.lock.qualock_version`. Write `manifest.json` last.

Call `verify_evidence_bundle(temp_dir)` before publication. Only after verification succeeds may `os.replace`/same-filesystem rename publish the whole directory. On any failure, remove only the owned temporary directory and leave destination absent. Refuse an existing destination and reject a destination inside the source qualification directory.

With a pinned `created_at`, exporting the same source twice to different destinations must produce byte-identical files. With no injected time, use one UTC-aware timestamp captured once per export.

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_export.py tests/unit/test_evidence_verify.py \
  tests/unit/test_evidence_provenance.py tests/unit/test_project_fingerprint.py
```

Then Ruff + `git diff --check`.

**Review gate:** reviewer verifies no arbitrary file copying, no raw transcript/hidden-grader leakage, no resolver/network/process path, exact current-vs-persisted provenance binding, deterministic canonical output, verifier-before-rename ordering, and cleanup containment. Require C0/I0.

**Commit:** `feat: export portable evidence bundles`

## Task 5 — Public evidence CLI and standalone E2E

**Files**

- Modify: `src/qualock/cli.py`
- Create: `src/qualock/evidence/render.py`
- Create: `tests/unit/test_evidence_cli.py`
- Create: `tests/integration/test_evidence_bundle_e2e.py`

- [ ] **Step 5.1 — RED: exactly two public evidence commands**

Add one `evidence_app` Typer group and register it as `qualock evidence`. Expose exactly `export` and `verify`; no hidden mutation/run/network commands. The CLI contract is `qualock evidence export QUALIFICATION_ID --out DIRECTORY` and `qualock evidence verify DIRECTORY`. The command functions are `evidence_export_command(qualification_id: str, out: Path) -> None` and `evidence_verify_command(bundle: Path) -> None`; `--out` is a required Typer option.

`export` calls only `export_evidence_bundle(Path.cwd(), qualification_id, out)`. `verify` calls only `verify_evidence_bundle(bundle)`. Both render bounded literal-safe output from `evidence/render.py`.

`EvidenceBundleError`, provenance-domain input failures, missing required source artifacts, stale-project export identity, and destination conflicts map to exit `3`. Unexpected operational/programmer failures use the existing generic exit `1` style without raw exception leakage. A successfully verified bundle exits `0` regardless of stored quality verdict.

Tests pin help visibility, option names, exit mapping, literal-safe dynamic values, BLOCK/WARN/INCOMPLETE verify exit zero, and absence of unrelated CLI surface changes.

- [ ] **Step 5.2 — RED: standalone export-copy-delete-project-verify**

The integration test must create a synthetic check result through production artifact/provenance writers without provider access, export it, copy the bundle to a new directory, delete the source QuaLock project, then verify the copied directory successfully.

Add mutation variants that alter one byte in each required payload class and confirm verification fails. Add an offline guard that fails any socket/process/Docker invocation during verify. Snapshot all bundle bytes and mtimes before/after verify and require exact preservation.

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_cli.py \
  tests/integration/test_evidence_bundle_e2e.py \
  tests/unit/test_evidence_provenance.py \
  tests/unit/test_evidence_bundle_models.py \
  tests/unit/test_evidence_bundle_io.py \
  tests/unit/test_evidence_verify.py \
  tests/unit/test_evidence_export.py \
  tests/unit/test_cli.py
```
Then run changed-file Ruff and `git diff --check`.

**Review gate:** independent reviewer checks exactly two public commands, exit semantics, safe rendering, standalone verification, zero side effects during verify, tamper detection, and no regression to existing check/monitor/report CLI behavior. Require C0/I0.

**Commit:** `feat: add evidence export and verify CLI`

## Task 6 — Whole verifier gates and delivery boundary

No planned production change belongs to this task. Any defect found here returns to a fresh scoped TDD fix owner and scoped re-review before repeating the gates.

- [ ] **Step 6.1 — Focused and full local verification**

Run the complete evidence suite plus direct regressions:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_evidence_provenance.py \
  tests/unit/test_evidence_bundle_models.py \
  tests/unit/test_evidence_bundle_io.py \
  tests/unit/test_evidence_verify.py \
  tests/unit/test_evidence_export.py \
  tests/unit/test_evidence_cli.py \
  tests/integration/test_evidence_bundle_e2e.py \
  tests/unit/test_project_fingerprint.py \
  tests/unit/test_storage.py tests/unit/test_policy.py \
  tests/unit/test_commands.py tests/unit/test_cli.py \
  tests/unit/test_history_loader.py tests/unit/test_pricing_sidecar_loader.py

/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
```

Then run:

```bash
BASE=1d586cda71144d11df88885cdafb86300c616e93
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
git diff --name-only -z "$BASE"..HEAD -- '*.py' | xargs -0 -r /home/pacmap/qualock-easy/.venv/bin/ruff check
git diff --name-only -z "$BASE"..HEAD -- 'src/**/*.py' | xargs -0 -r /home/pacmap/qualock-easy/.venv/bin/python -m mypy
git diff --check "$BASE"..HEAD
```
Mypy acceptance is diagnostic-set based: no new diagnostics beyond the repository's established three PyYAML `import-untyped` findings in `config/io.py`, `canary/loader.py`, and `project_setup/config.py`.

- [ ] **Step 6.2 — Protected-scope and schema proof**

Require zero semantic diff in qualification policy, run backends, agent adapters/resolvers, source materialization, release monitor, scheduler, bisect, GitHub PR workflows, and pricing/history analysis except the explicitly allowed pure pricing-parser refactor from Task 3. Require `pyproject.toml` and dependency lock/requirements files unchanged.

Assert existing schemas remain baseline `1`, config `1`, canary `1`, monitor `1`, bisect `2`, PR `2`; new provenance and evidence manifest schemas are independently `1`.

Run a static secret/privacy scan over the provenance sidecar, public-bundle builders, and fixture output for credential variable names, sentinel secret values, raw `events_jsonl`, raw unsafe repository locators, grader content, task text, setup commands, and protected paths. Require tests proving `repository_url_sha256` binds the exact configured locator and that only the unchanged publication-safe URL can appear in `canaries.json`/manifest.

- [ ] **Step 6.3 — Fresh whole-verifier security review**

Freeze exact implementation SHA and prepare one base-to-head review package. Fresh high-effort reviewer must audit the canonical spec and all Tasks 1–5 with explicit focus on:

- no fabricated or re-resolved runtime identity;
- prospective sidecar failure ordering;
- hidden grader/transcript/credential non-publication;
- untrusted filesystem/symlink/race containment;
- canonical deterministic bytes and inventory closure;
- policy recomputation rather than stored-verdict trust;
- incomplete/budget semantics;
- standalone zero-network/zero-process/zero-write verification;
- backward compatibility and no existing schema/policy drift.

Require `Critical: 0` and `Important: 0`. Record every Minor disposition.

- [ ] **Step 6.4 — Hard stop before verifier PR shared effects**

Do not push until explicit operator authorization. After authorized push, require local SHA = remote branch SHA = PR head SHA and hosted CI green on Linux Python 3.11/3.12/3.13 plus Windows.

Only after implementation-head CI is green may delivery docs be updated locally: README documents `qualock evidence export|verify`; the canonical spec status becomes `Verifier delivered; case study pending`. The ROADMAP v0.1 regression-evidence item remains **not delivered** until a qualifying real case study is published.
Rerun focused/full/static gates on the docs-inclusive head. Pushing the docs commit is a separate explicit shared-effect boundary. After docs-head CI is green, perform exactly one final whole-branch review covering implementation plus truthful docs. Then hard-stop for explicit verifier-PR merge authorization. No tag/release/package publish accompanies that merge.

**Verifier milestone:** merged verifier functionality is necessary but does not by itself complete Batch #45 or the ROADMAP proof item.

## Task 7 — Discover one reproducible case-study candidate and freeze its runbook

**Precondition:** Task 6 verifier PR is merged to `main`. Start a fresh case-study discovery worktree from that merged main. Discovery itself is read-only/public-web research plus documentation; it performs no authenticated model run.

**Output file:** `docs/evidence/2026-09-10-regression-candidate-runbook.md`

- [ ] **Step 7.1 — Research candidate version pairs**

Research current public release notes, changelogs, issue trackers, and existing QuaLock evidence for Codex, Claude Code, and Gemini CLI. Consider only exact stable version pairs accepted by QuaLock. Prefer an existing bundled canary when the release change plausibly affects it; do not invent a new canary merely to force a difference.

Rank candidates using this fixed order:

1. direct public evidence of a behavioral/tooling change that maps to a deterministic code task;
2. availability of exact historical repository SHA and hidden grader already representable by QuaLock;
3. narrow attribution surface and low run cost;
4. smallest version gap;
5. lexical agent/version tuple as deterministic tie-break.

Record the top three with cited public evidence, reject reasons, and select exactly one highest-ranked candidate. Do not treat release-note claims as observed regression evidence.

- [ ] **Step 7.2 — Freeze the no-run execution contract**

The runbook must record before any provider call:

- selected agent and exact stable baseline/candidate versions;
- exact model ID/snapshot when available and reasoning configuration;
- canary IDs, repository URLs, exact historical base SHAs, and why each is relevant;
- expected observable difference stated as a falsifiable hypothesis;
- repetition count and integrity policy;
- expected attempt count and a conservative token/runtime budget derived from existing QuaLock evidence where available;
- exact merged QuaLock commit to execute;
- commands for baseline, check, evidence export, evidence verify, and post-run source-isolation audit;
- success criterion matching the canonical spec: repeated `BLOCK`/`WARN` or a defined structured meaningful difference; PASS-only no-difference does not close the milestone.
- [ ] **Step 7.3 — Review and provider-authorization boundary**

Independently review the runbook for source relevance, version stability, reproducibility, budget realism, and claim discipline. Public-source citations must support only candidate plausibility, not the final behavioral claim.

Then stop. Authenticated provider execution requires a new explicit operator authorization after the selected candidate, exact command sequence, and budget are visible. On authorization, write a short candidate-specific execution plan before spending provider quota; if a new canary is required, it gets its own approved bounded design and TDD review before any real run.

No automatic fallback to the second/third candidate is permitted after a no-difference run. Each additional provider experiment requires its own stated evidence rationale and operator authorization.

**Commit:** `docs: plan reproducible regression case study`

## Task ownership and review matrix

| Task | Primary surface | Required review focus |
| --- | --- | --- |
| 1 | provenance persistence | exact runtime identity, privacy, mandatory failure ordering |
| 2 | bundle models/filesystem | closed schemas, path/race/bounds security |
| 3 | offline verifier | cross-file binding, recomputed policy, zero side effects |
| 4 | exporter | redaction, deterministic bytes, self-verify-before-rename |
| 5 | CLI/E2E | exit contract, literal-safe output, standalone verification |
| 6 | whole verifier | full security/regression/docs/CI gate |
| 7 | case-study discovery | evidence quality, budget, reproducibility, claim scope |

## SDD execution setup

Before Task 1 implementation, create `.superpowers/sdd/2026-09-10-reproducible-regression-evidence/` with `progress.md` and one brief per task. These execution artifacts remain local/ignored unless repository policy explicitly tracks them.

For every production task:

1. fresh implementer reads only canonical spec + owning task brief + current code;
2. implementer writes tests and captures genuine RED before production changes;
3. implementer makes the smallest GREEN change and runs owning regressions/static gates;
4. fresh read-only reviewer receives exact task-base→task-head diff and evidence;
5. C/I findings enter a fresh scoped fix loop; re-review only the finding and changed range;
6. ledger task complete only after C0/I0.

## Plan completion condition

This implementation plan is complete when Tasks 1–6 make the portable verifier merge-ready and Task 7 produces a reviewed, exact candidate runbook without executing a provider. Batch #45 itself is complete only after a later explicitly authorized candidate-specific execution produces and publishes a verified qualifying case study under the canonical spec.
