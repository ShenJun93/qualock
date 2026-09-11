# Batch #45 — Reproducible Regression Evidence Design

**Date:** 2026-09-10
**Base:** `main@1d586cda71144d11df88885cdafb86300c616e93`
**Status:** Verifier delivered; case study pending

## Purpose

Close the remaining v0.1 proof gap: publish one reproducible real-world coding-agent upgrade regression or meaningful behavioral difference, backed by evidence that a third party can verify offline without trusting README prose or rerunning a model.

Batch #45 has two ordered phases:

1. deliver a deterministic, portable, offline-verifiable evidence bundle contract and CLI;
2. use that verifier to publish one case study that satisfies the roadmap evidence requirement.

The verifier is the prerequisite. Case-study discovery may begin only after the evidence contract is implemented and reviewed.

## Goals

- Export an existing completed qualification into a deterministic directory safe to publish.
- Verify that directory offline and read-only without the original project checkout.
- Bind qualification result, baseline identity, model identity, suite/config fingerprints, source SHAs, prepared image digests, run order, and included files under SHA-256.
- Recompute the quality verdict from bundled structured evidence using existing pure policy logic rather than trusting a stored verdict string.
- Detect tampering, missing files, extra files, path tricks, inconsistent identities, and incomplete provenance fail-closed.
- Preserve hidden grader secrecy, credentials, auth state, and private project content.
- Produce one narrowly scoped case study showing a reproducible regression or meaningful behavioral difference.
## Non-goals

- No authenticated provider run as part of verifier implementation or review.
- No archive format, signing service, transparency log, hosted verifier, dashboard, or remote attestation.
- No claim that a bundle proves the model service itself was immutable or independently pinned.
- No public export of hidden grader source, raw agent transcripts, raw `events_jsonl`, repository secrets, credential files, auth tokens, browser state, arbitrary worktree files, or untracked project content.
- No changes to qualification policy, source materialization, agent adapters, release monitor, scheduler, bisect, GitHub PR qualification, pricing policy, or history semantics.
- No schema bump to existing baseline, monitor, bisect, PR, scheduler, or local qualification artifacts.

## Threat model

The verifier assumes the bundle directory may be attacker-controlled. It must treat every path and JSON field as untrusted input and must not execute anything from the bundle.

It must defend against:

- modified or substituted evidence files;
- manifest/file mismatch, missing files, and unmanifested payloads;
- absolute paths, `..`, path separator tricks, duplicate normalized paths, symlinks, FIFOs/devices, and directory escape;
- contradictory qualification IDs, versions, verdicts, run orders, canary identities, or baseline/model pins;
- malformed SHA-256 values and Gemini runtime-support identity downgrade;
- a stored verdict that does not match the bundled per-canary evidence;
- accidental publication of raw event streams or credential-bearing material.

The verifier does not prove that the original provider returned truthful model identity, that a container registry still serves an old digest, or that the historical source repository remains online. It proves internal integrity and consistency of the exported QuaLock evidence and its declared provenance.
## CLI contract

Two new subcommands are in scope:

```text
qualock evidence export <qualification-id> --out <directory>
qualock evidence verify <directory>
```

`export` reads one completed local check result, its evidence provenance sidecar, and trusted project metadata. It must not resolve agents, contact registries/providers, run Docker, execute project commands, or mutate the source qualification. The output directory must not already exist; partial export uses a sibling temporary directory and atomic rename on success.

`verify` accepts only a directory. It performs no network access, no subprocess execution, no imports from bundle-controlled paths, no Docker work, and no writes inside the bundle. Verification does not require a QuaLock project checkout.

Success output identifies the qualification ID, agent/version transition, recomputed verdict, bundle schema, and manifest digest. Failure output gives one bounded actionable reason and no secret-bearing payload dump.

Exit semantics:

- `0`: bundle verified successfully;
- `3`: invalid input, malformed bundle, integrity/provenance mismatch, or unsupported evidence schema;
- existing CLI framework errors remain unchanged for parser/internal failures.

A verified `BLOCK`, `WARN`, or `INCOMPLETE` evidence bundle still exits `0`: verification success means the bundle's contents match its manifest hashes, its declared provenance is internally consistent, and its stored quality result matches recomputation; it does not authenticate the publisher/origin and does not mean the candidate passed.
## Prospective local provenance sidecar

Current `report.json` does not persist the resolved candidate executable SHA-256 or Gemini candidate support-tree fingerprint. Batch #45 therefore adds a new evidence-only local sidecar, `.qualock/results/<qualification-id>/evidence-provenance.json`, schema version `1`, written for new `qualock check` results after successful resolution/execution and before the result is considered exportable. Existing artifact schemas are unchanged.

The sidecar records only publication-safe structured provenance: qualification ID; `run_qualock_version` (the exact `qualock.__version__` executing the check); canonical baseline-lock SHA-256; baseline and candidate resolved agent identities `{name, version, binary_sha256, support_sha256}`; model pin; suite/config fingerprints; canary ID/critical/repository URL SHA-256/base SHA/full-definition fingerprint; prepared-image digest; expected repetitions; and the canonical run-order digest. It contains no raw repository locator, raw events, prompts, commands, grader paths/content, environment, or credentials.

For each canary, `repository_url_sha256 = sha256_canonical(canary.repository.url)` binds the exact configured repository locator without copying that locator into the prospective sidecar. This prevents a credential-bearing or otherwise private repository string from being persisted as publication metadata before export.

The sidecar must be derived from the exact resolved binaries and loaded canaries used by that check, not reconstructed later. Gemini requires support fingerprints on both baseline and candidate identities. For non-Gemini agents the existing nullable support field remains valid. Sidecar write failure must not mutate the already-computed quality verdict or delete its local report artifacts, but the `qualock check` command fails with the existing command/precondition exit path (`3`) and must not render a successful safety recommendation. This prevents an apparently successful check from being mistaken for portable evidence when provenance persistence failed.

Legacy qualification directories without this sidecar remain valid for report/history/pricing, but `qualock evidence export` rejects them as `provenance unavailable`. The September 1 historical evidence remains documentary legacy evidence unless rerun under Batch #45 tooling.

## Bundle layout

V1 is a deterministic directory, not tar/zip:

```text
<bundle>/
  manifest.json
  report.json
  qualification.json
  baseline.lock
  provenance.json
  canaries.json
  pricing.json          # optional, only when a valid sidecar already exists
```

No README or arbitrary prose is part of the verified payload in V1. Case-study prose lives beside the bundle in `docs/evidence/...`, not inside it. This keeps the inventory exact: every regular file in the bundle except `manifest.json` must appear exactly once in the manifest inventory, and no additional file is allowed.

`report.json` is a normalized public evidence projection, not a byte copy of the local report. Each public attempt contains exactly `side`, `repetition`, `success`, `valid`, `duration_ms`, normalized `usage` (`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `observed`), and `events_sha256 = sha256(raw_events_jsonl.encode("utf-8")).hexdigest()`. Raw `events_jsonl`, free-form `invalid_reason`, and `protected_path_violations` are never exported because the latter two can contain exception/path text and are not qualification-policy inputs. Execution-level aggregate counts and policy verdict/reason fields may be present only in their normalized policy-derived form and are independently recomputed by the verifier.

`qualification.json` is normalized from the existing local metadata and must agree with the report on qualification ID, versions, run order, verdict, budget/completeness values, attempt count, and observed-token summary.

`baseline.lock` is a normalized copy of the trusted baseline lock used by the check. `provenance.json` is the normalized public copy of the run-time evidence sidecar and binds both baseline and candidate resolved executable/runtime identities. `canaries.json` is a public metadata projection containing canary ID, critical flag, a publication-safe declared repository URL, `repository_url_sha256`, declared base SHA, prepared image digest, and a fingerprint binding the original canary definition. It must not contain setup commands, task text, grader paths/content, protected source, or hidden grader material.

A V1 public repository URL is intentionally narrow: it is at most 2048 UTF-8 bytes; has scheme `https` or `http`; has a non-empty host; has no username/password userinfo, query, fragment, or ASCII control character. Local paths, `file:` URLs, SSH/SCP locators, and any URL with userinfo/query/fragment are rejected for export with the fixed `unsafe_repository_url` reason. Export never sanitizes or rewrites a repository locator. The public exact URL must hash to the persisted `repository_url_sha256`, so privacy filtering cannot silently change provenance.

`pricing.json` is optional advisory provenance. If present it is hash-bound and structurally validated, but pricing can never change or invalidate a correctly recomputed quality verdict except when the pricing file itself is malformed or contradicts the qualification identity.
## Manifest schema v1

`manifest.json` is canonical JSON: UTF-8, object keys sorted recursively, no insignificant whitespace, one trailing newline. Its own digest is reported by the verifier but is not self-listed in `files`.

Required top-level fields:

- `schema_version: 1`;
- `created_at`: export timestamp in UTC ISO-8601;
- `run_qualock_version`: exact QuaLock version recorded prospectively by the check provenance;
- `exporter_qualock_version`: exact `qualock.__version__` performing this export; it may differ from both `run_qualock_version` and the baseline lock's historical `qualock_version`;
- `qualification_id`;
- `baseline_version`, `candidate_version`, and stored `verdict`;
- `agents`: `{baseline, candidate}`, each `{name, version, binary_sha256, support_sha256}` matching `provenance.json`; baseline also matches the baseline lock, and Gemini requires lowercase 64-hex support digests on both identities while legacy non-Gemini null is allowed;
- `model`: `{id, snapshot, reasoning_effort}` matching the baseline lock;
- `baseline_lock_sha256` over canonical normalized baseline-lock JSON, plus `suite_sha256` and `config_sha256`;
- `run_order_sha256` over the canonical run-order value;
- `completeness`: attempts expected/used, budget fields, and whether every configured canary has complete paired evidence;
- `canaries`: sorted public provenance records keyed by canary ID;
- `files`: sorted relative POSIX paths mapped to `{sha256, size_bytes}`.

Each canary manifest record includes: `critical`, publication-safe repository URL, `repository_url_sha256`, exact 40-hex `base_sha`, canary-definition fingerprint, prepared-image digest, expected repetitions, baseline valid/success counts, candidate valid/success counts, and stored canary verdict.

Every SHA-256 field is lowercase 64-hex. Prepared image digests retain their `sha256:<64hex>` form. Unknown fields are rejected in V1 so an old verifier cannot silently ignore new security semantics. Exact V1 input bounds are: `manifest.json` 2 MiB; `report.json` 32 MiB; `qualification.json` 4 MiB; `baseline.lock` 4 MiB; `provenance.json` 8 MiB; `canaries.json` 8 MiB; `pricing.json` 32 MiB; at most 4096 canaries; at most 8192 attempts in one execution; repository URLs at most 2048 UTF-8 bytes; and other qualification/canary/model/version/reason-like text fields at most 4096 UTF-8 bytes.
## Export algorithm

Export is intentionally narrower than general project backup.

1. Resolve `.qualock/results/<qualification-id>/` beneath the current project and require a completed check containing `report.json`, `qualification.json`, and `evidence-provenance.json`; baseline-only and legacy sidecar-less runs are rejected.
2. Parse all three files with strict structural validation and require their IDs, versions, verdict, run order, budgets, and attempt accounting to agree.
3. Load the current `.qualock/baseline.lock` and current configured canary definitions only to establish trusted identity/provenance. Require suite/config fingerprints to match the lock exactly before export.
4. Require the report baseline version to equal the lock agent version; require sidecar baseline identity to match the lock exactly and sidecar candidate name/version to match the requested candidate. Validate both executable digests; for Gemini require valid support digests for both sides.
5. Build public `canaries.json` from loaded canary metadata. Hash the full canonical canary definition for binding; require each exact current repository URL to hash to the sidecar's `repository_url_sha256`; then require that exact URL to satisfy the V1 publication-safe URL rule before copying it. Never sanitize or rewrite an unsafe locator; fail export instead.
6. Match every report execution to exactly one current canary; require critical flags and prepared-image digests to be internally consistent and require paired attempt counts to agree with configured repetitions or explicit incomplete-budget semantics.
7. Normalize `report.json` using the exact public-attempt whitelist above: hash then discard raw event streams; omit `invalid_reason` and `protected_path_violations`; preserve only policy inputs, non-negative duration/usage accounting, and normalized policy-derived aggregate/verdict fields.
8. Normalize the sidecar as `provenance.json` plus the other payload files, optionally copy a valid existing pricing sidecar, compute the exact file inventory, then write canonical `manifest.json` last.
9. Verify the temporary bundle with the same verifier implementation before atomically renaming it to the requested output directory.

Export never fabricates missing historical provenance. If current project metadata no longer matches the qualification/baseline identity closely enough to produce a complete bundle, export fails and instructs the operator that the historical result is not portable under V1.
## Verify algorithm

Verification is a pure local validation pipeline:

1. `lstat` the bundle root and every directory entry without following symlinks. Reject symlinks, non-regular files, nested directories in V1, absolute/empty/dot paths, `..`, backslashes, duplicate normalized names, and any file not allowed by the fixed V1 layout.
2. Parse `manifest.json` strictly, require schema `1`, reject unknown fields, validate bounded string/list sizes, and validate all digest/path syntax before opening inventory targets.
3. Require the manifest inventory to equal the actual payload file set exactly; stream-hash every payload and verify both SHA-256 and byte size.
4. Parse the normalized report, qualification metadata, baseline lock, provenance sidecar copy, canary metadata, and optional pricing sidecar with strict schemas.
5. Recompute `baseline_lock_sha256`, run-order digest, canary-definition/public metadata bindings, and cross-file qualification/version/agent/model/suite/config identities.
6. Require provenance baseline/candidate executable identities to match manifest and versions; baseline identity also matches the lock. Enforce Gemini support identity shape/presence for both sides; non-Gemini compatibility follows the existing nullable support contract.
7. Validate each execution: one known canary, matching critical flag, one prepared-image digest, unique side/repetition slots, bounded repetitions, and aggregate success/valid counts derived from attempts rather than trusted aggregate fields.
8. Rebuild the inputs to the existing pure qualification policy from those derived aggregates and recompute every canary verdict and overall verdict. Stored report/qualification/manifest verdicts must all equal the recomputed value.
9. Validate completeness independently from stored booleans: missing paired slots are allowed only when the report is genuinely budget-stopped/incomplete under existing semantics; a bundle may never present incomplete evidence as PASS/BLOCK.
10. If `pricing.json` exists, validate its hash/schema/qualification identity independently. Pricing remains advisory and is not an input to step 8.

The verifier returns a structured internal result so terminal rendering and tests do not need to parse prose. It must stop on the first deterministic validation failure category and must never echo raw file contents.
## Error model

Evidence-domain failures use a dedicated `EvidenceBundleError` carrying a stable reason category plus a human-readable message. CLI converts it to the existing command/precondition exit path (`3`). Initial V1 categories include malformed manifest, unsafe path, unsafe repository URL, inventory mismatch, digest mismatch, malformed payload, identity mismatch, invalid Gemini support identity, canary provenance mismatch, attempt-layout mismatch, completeness mismatch, and verdict mismatch.

Error messages identify a logical file/field but never interpolate raw event text, credential-like values, or arbitrary JSON payloads.

## Security and privacy invariants

- Export reads only known QuaLock artifacts and parsed canary/config/baseline metadata through existing loaders; it never recursively copies the project or result directory.
- Raw `events_jsonl` is hashed then discarded from the public projection; free-form `invalid_reason` and `protected_path_violations` are discarded rather than redacted or copied.
- No prompt, task text, setup command, grader command/path/source, protected source snapshot, environment, auth file, token, browser state, or model credential is exported.
- Prospective provenance stores only `repository_url_sha256`, never the raw repository locator. Export includes the exact locator only after the fixed V1 publication-safe URL check succeeds and its hash matches the sidecar; unsafe locators fail rather than being sanitized.
- The output path cannot be inside the source qualification directory.
- Verification never accepts a symlink/reparse entry and never consumes bytes from an opened object that was not obtained through a no-follow handle path. On POSIX, payloads are opened relative to an already-opened directory descriptor with `O_NOFOLLOW` and accepted only after `fstat()` confirms a regular file. On Windows, payloads use `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT`, reject `FILE_ATTRIBUTE_REPARSE_POINT`, and validate the opened final path against the separately opened root handle plus exact filename. If the required primitive is unavailable, verification fails closed with `unsafe_path`; there is no link-following fallback.
- JSON parsing is bounded by the exact V1 file/collection/string limits above before or during decode/model validation to avoid trivial memory abuse.
- Canonical JSON and hashing use the existing evidence fingerprint utilities where compatible; one canonicalization implementation is authoritative.
- Verification is independent of current wall-clock time and network state.
- Existing local evidence files are never rewritten in place.

## Compatibility

The evidence manifest and evidence-provenance sidecar are new independent schemas at version `1`. Existing baseline/report/qualification/history/pricing schemas remain unchanged and existing local qualification artifacts remain readable by current history/pricing/report code.

The exporter may adapt current local report schema into the public projection, but the public projection has its own strict internal model. Future bundle versions must increment `manifest.schema_version`; V1 verifier rejects unknown versions rather than guessing forward compatibility.
## Case-study phase

Case-study work is deliberately separated from verifier implementation.

Discovery may use public release notes, issue trackers, changelogs, and existing QuaLock canary history to rank plausible stable version pairs. Selection criteria favor a small, explainable behavioral surface with a deterministic historical repository SHA and grader, not a broad benchmark or subjective score.

Before any authenticated provider execution, record:

- selected agent and exact stable baseline/candidate versions;
- explicit model identifier/reasoning configuration;
- canary repository URLs and exact historical base SHAs;
- expected observable difference and why public evidence suggests the pair is plausible;
- repetition count, integrity policy, and estimated run budget;
- the exact QuaLock commit used for execution.

Authenticated execution is a separate shared-effect boundary and requires explicit operator authorization. No discovery result authorizes model spend.

A case study satisfies Batch #45 only when repeated evidence shows either a QuaLock quality `BLOCK`/`WARN` attributable to the selected upgrade under the suite, or a clearly defined meaningful behavioral difference that is visible in structured attempt outcomes and reproducible under the documented recipe. A PASS-only no-difference run does not close the roadmap item.

Publication must state the exact scope: it is evidence about the selected versions, model/configuration, repositories, historical SHAs, canaries, and qualification window. It must not claim general agent superiority, universal regression, provider causality, or model-service immutability.
## Required TDD acceptance criteria

1. New checks persist an evidence-provenance sidecar containing exact baseline/candidate executable identities and safe run provenance; legacy results without it remain valid locally but export fails closed.
2. Export rejects unknown, baseline-only, malformed, sidecar-less, or stale-project qualifications without creating the destination.
3. Export creates the exact flat V1 file set and refuses an existing destination.
4. Export performs no resolver, network, Docker, provider, or project-command work under fakes that fail if called.
5. Public report contains only the fixed public-attempt whitelist; raw `events_jsonl`, free-form `invalid_reason`, and `protected_path_violations` are absent, while event digests are stable and correct.
6. Public canary metadata contains base SHA/publication-safe repository URL plus its provenance hash/critical/prepared-image/fingerprint data but no task/setup/grader/protected-source content; credential-bearing, local-path, SSH/SCP, query, fragment, and other non-V1 repository locators fail export without destination creation.
7. Manifest JSON is deterministic/canonical and file inventory ordering is stable.
8. Manifest binds exact agent/model/baseline/suite/config/run-order identities and exact payload hashes/sizes.
9. Gemini bundles require valid `support_sha256`; legacy Codex/Claude/Antigravity baseline compatibility remains unchanged where export is otherwise valid.
10. Verify succeeds from a copied standalone directory after the original project is removed.
11. Verify performs zero network/subprocess/Docker/project execution and zero writes to the bundle.
12. Verify rejects symlinks, non-regular files, nested paths, traversal spellings, duplicate normalized paths, extras, missing files, and unsafe inventory paths.
13. Single-byte tampering of every required payload class is detected by hash verification.
14. Cross-file qualification ID/version/agent/model/suite/config/run-order inconsistencies fail closed.
15. Canary source SHA, critical flag, prepared-image digest, repetition layout, and aggregate-count inconsistencies fail closed.
16. Stored per-canary or overall verdict tampering is caught by recomputing policy from attempt evidence.
17. Incomplete paired evidence cannot verify as PASS/BLOCK; valid budget-stopped INCOMPLETE evidence remains representable.
18. Optional pricing sidecar tampering fails bundle integrity, but pricing values never alter recomputed quality verdict.
19. Error rendering never dumps raw JSON/events and uses stable categories with CLI exit `3`.
20. Existing qualification/history/pricing/report/baseline tests remain green with no schema drift.
21. A fixture bundle generated twice from identical source evidence is byte-identical except the explicitly controlled export timestamp; tests pin timestamp to prove deterministic output.

Case-study acceptance adds a separate evidence/review checklist after implementation and never weakens these verifier requirements.
## Implementation sequence

Implementation should be split into independently reviewable tasks in this order:

1. prospective `evidence-provenance.json` persistence and stable per-canary fingerprint extraction;
2. strict evidence-bundle/public-payload models, canonical serialization, and bounded no-follow path/inventory validation;
3. standalone offline verifier with cross-file binding, completeness checks, and verdict recomputation through the existing pure qualification policy;
4. normalized public report/canary projection plus deterministic exporter that self-verifies before atomic publication;
5. `qualock evidence export` / `qualock evidence verify` CLI wiring and standalone export-copy-delete-project-verify E2E;
6. whole-verifier local/security/CI/delivery gates;
7. public case-study candidate discovery and an exact no-run execution runbook, performed only after verifier delivery.

Tasks 1–5 are normal local implementation work and must follow strict RED-before-production TDD with a fresh independent reviewer per task. Task 6 is verification/delivery gating only. Task 7 is research/planning only; authenticated provider execution remains a later candidate-specific plan with its own explicit operator authorization boundary.

## Review and CI gates

Before any implementation push:

- focused evidence/export/verify tests pass;
- full repository pytest passes;
- changed-file Ruff, compileall, `git diff --check`, and strict mypy pass except exact established dependency-stub debt;
- protected-scope proof shows no unintended changes to qualification policy, agent adapters, source materialization, scheduler, monitor, bisect, GitHub PR, pricing, or history logic;
- bundle security review reports zero Critical/Important findings;
- a fresh whole-implementation review approves the exact implementation head.

Push/PR requires explicit operator authorization. Hosted Linux and Windows CI must be green on the exact implementation head before delivery wording may be committed. Delivery-doc push and merge each require their own authorization, following the existing QuaLock workflow.

The case-study phase repeats local verification for the exact execution/export tooling commit and requires a separate explicit authorization before authenticated provider traffic. Publishing case-study evidence, updating the roadmap item, tag/release, or package publish are distinct shared effects and are not implied by authorization to run the experiment.
## Design rationale

A flat deterministic directory is preferred to an archive because it makes path safety, review diffs, hashing, fixture construction, and offline inspection simpler. Archive transport can be added later outside the verifier trust boundary.

The bundle intentionally exports a public projection instead of copying local `report.json` byte-for-byte. Local reports retain raw event streams for forensic use; a public proof needs quality-policy inputs and cryptographic bindings, not potentially sensitive model transcripts. This is a privacy boundary, not an evidence downgrade: the verifier recomputes verdicts from structured attempt outcomes and binds discarded raw event streams by digest.

The exporter requires current baseline/config/canary identity to still match because legacy result directories do not independently persist every provenance field required for a trustworthy portable bundle. Batch #45 closes the runtime-identity gap prospectively with `evidence-provenance.json`, but V1 still fails closed for legacy results rather than inventing missing history.

## Merge-ready definition

The verifier implementation is merge-ready when an untrusted standalone V1 bundle can be verified offline with deterministic path/hash/cross-file/policy checks, public export demonstrably excludes sensitive material, existing QuaLock behavior remains unchanged, all local/hosted gates are green, and final review has zero Critical/Important findings.

Batch #45 as a roadmap milestone is complete only after the separately authorized case-study phase publishes one verified bundle and narrowly scoped write-up demonstrating a reproducible real-world upgrade regression or meaningful behavioral difference. Verifier delivery alone is necessary but not sufficient to mark the roadmap proof item delivered.
