# Selected-Source Execution V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `qualock target-check SIGNAL --context CONTEXT` path that executes exactly the fresh Change Targeting V0 selected sources, persists targeted-only evidence, and never widens the selected set.

**Architecture:** Keep Change Targeting V0 planner semantics untouched. Add a new `qualock.targeted_execution` package for preflight/gating, targeted artifact models/storage, orchestration, and rendering; extract one private runtime execution core from `qualock.commands` so normal `check` and targeted execution share resolver/backend/paired-attempt semantics without sharing result namespaces or claims.

**Tech Stack:** Python 3.14, Pydantic v2, Typer 0.27, existing QuaLock qualification executor/backends, pytest, Ruff, MyPy.

**Spec:** `docs/superpowers/specs/2026-09-18-selected-source-execution-v1-design.md`

## Global Constraints

- No dependency installation.
- No authenticated provider qualification is required for V1 correctness.
- TDD is mandatory for every implementation/fix task.
- Every task/fix receives a fresh independent review; unresolved Critical or Important findings block progress.
- Do not repair unrelated Ruff/MyPy debt.
- Do not change Change Targeting V0 schemas or `target-change` exit semantics.
- Do not change ordinary `check`, monitor/scheduler, PR qualification, bisect, evidence-export, paired-change verification, or first-bad semantics.
- No source widening, fallback, replacement, or adaptive replanning after selection.
- No resolver/backend/credential/provider/runtime work before a fresh READY assessment with non-empty selected sources.
- Full current project suite/config freshness is required before planner disposition is used by `target-check`.
- Targeted artifacts live only under `.qualock/results/targeted/<qualification_id>/` and use targeted-only filenames.
- Targeted PASS/WARN is never rendered or persisted as a full-suite update-safety recommendation.
- Raw `TargetContextV0.facts` values are never persisted or printed by targeted execution.
- No push, PR, merge, tag, release, or publish unless explicitly authorized by the user.

## File Structure

- Create `src/qualock/targeted_execution/__init__.py` — inert package boundary.
- Create `src/qualock/targeted_execution/models.py` — targeted receipt/wrapper models and strict invariants.
- Create `src/qualock/targeted_execution/storage.py` — targeted directory creation, targeted report/qualification bytes, exact byte hashes, receipt writer.
- Create `src/qualock/targeted_execution/planning.py` — local identity/freshness preflight, planner recomputation, exact selected-canary resolution.
- Create `src/qualock/targeted_execution/commands.py` — READY-only execution orchestration and targeted evidence persistence.
- Create `src/qualock/targeted_execution/render.py` — scoped terminal rendering; no low-tech full-suite safety language.
- Modify `src/qualock/commands.py` — extract private qualification execution core used by existing full-suite `execute_check()` and targeted orchestration.
- Modify `src/qualock/cli.py` — add command-local `target-check` CLI and exact exit mapping.
- Create `tests/unit/test_targeted_execution_models.py`.
- Create `tests/unit/test_targeted_execution_storage.py`.
- Create `tests/unit/test_targeted_execution_planning.py`.
- Create `tests/unit/test_targeted_execution_commands.py`.
- Create `tests/unit/test_targeted_execution_cli.py`.
- Create `tests/integration/test_targeted_execution_managed_shell.py`.
- Modify existing `tests/unit/test_commands.py` only for full-suite regression coverage required by the private core extraction.

---

### Task 1: Targeted Artifact Models and Isolated Storage

**Files:**
- Create: `src/qualock/targeted_execution/__init__.py`
- Create: `src/qualock/targeted_execution/models.py`
- Create: `src/qualock/targeted_execution/storage.py`
- Create: `tests/unit/test_targeted_execution_models.py`
- Create: `tests/unit/test_targeted_execution_storage.py`

**Interfaces:**
- Consumes: `CoverageAssessmentV0`, `QualificationResult`, `Verdict`, `sha256_canonical()`, existing technical report encoding.
- Produces:
  - `TargetedExecutionError(ValueError)`
  - `TargetedQualificationV1`
  - `TargetedReportV1`
  - `TargetedArtifactHashesV1`
  - `TargetedRunV1`
  - `targeted_results_dir(root: Path) -> Path`
  - `write_targeted_qualification_artifacts(base_dir: Path, *, result: QualificationResult, assessment: CoverageAssessmentV0, selected_sources: tuple[str, ...], agent_display_name: str) -> Path`
  - `sha256_file(path: Path) -> str`
  - `write_targeted_run(path: Path, value: TargetedRunV1) -> Path`

- [ ] **Step 1: Write strict model RED tests**

Create tests that construct a READY assessment and assert:
- protocol ID is exactly `selected-source-execution/v1`;
- `scope == "targeted"`;
- receipt selected sources exactly equal assessment selected sources;
- selected sources are non-empty/unique/sorted;
- attempted sources are a sorted subset;
- all required digests are lowercase 64-hex;
- receipt rejects non-READY assessment;
- receipt rejects selected-source mismatch;
- receipt rejects extra fields.

Use a helper like:

```python
def ready_assessment() -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.READY,
        relevant_contracts=("command.execution",),
        selected_sources=("probe-a",),
    )
```

Expected receipt construction:

```python
TargetedRunV1(
    schema_version=1,
    protocol_id="selected-source-execution/v1",
    qualification_id="target-check-q1",
    assessment=ready_assessment(),
    assessment_sha256="d" * 64,
    project_suite_sha256="e" * 64,
    selected_suite_sha256="f" * 64,
    config_sha256="1" * 64,
    baseline_lock_sha256="2" * 64,
    selected_sources=("probe-a",),
    attempted_sources=("probe-a",),
    qualification_verdict=Verdict.PASS,
    run_order_sha256="3" * 64,
    artifacts=TargetedArtifactHashesV1(
        targeted_report_json="4" * 64,
        targeted_qualification_json="5" * 64,
        targeted_evidence_provenance_v1_json="6" * 64,
        targeted_paired_change_run_v1_json="7" * 64,
    ),
)
```

- [ ] **Step 2: Run model tests and preserve RED**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_targeted_execution_models.py -q
```

Expected: collection/import failure because `qualock.targeted_execution.models` does not exist.

- [ ] **Step 3: Implement minimal strict models**

Use `ConfigDict(extra="forbid", frozen=True)` on receipt models. Define hash fields with:

```python
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
```

Require exact artifact fields instead of an open dictionary so unknown/missing filenames fail closed.

In `TargetedRunV1._validate_invariants()`:
- require READY;
- require selected sources equal assessment selected sources;
- require non-empty, sorted, unique selected sources;
- require attempted sources sorted/unique and subset of selected sources.

Keep `src/qualock/targeted_execution/__init__.py` inert:

```python
__all__: tuple[str, ...] = ()
```

- [ ] **Step 4: Write storage RED tests before implementation**

Tests must prove:
- output root is `<base>/targeted/<qid>`;
- files are named exactly:
  - `targeted-report.md`
  - `targeted-report.json`
  - `targeted-qualification.json`;
- JSON has `scope: "targeted"`, selected sources, assessment digest fields, and no `SAFE TO UPDATE`;
- markdown contains `selected sources only` and no full-suite safe-update copy;
- `sha256_file()` hashes exact bytes;
- an existing qualification directory is refused without overwrite;
- unsafe qualification IDs are rejected before directory creation: empty string, `.`, `..`, path separators, or any value for which `Path(qid).name != qid`;
- targeted writers never create ordinary `report.json` or `qualification.json`.

The JSON wrapper shape is:

```python
{
    "schema_version": 1,
    "scope": "targeted",
    "selected_sources": list(selected_sources),
    "assessment": assessment.model_dump(mode="json"),
    "result": render_json(result),
}
```

The targeted qualification wrapper must include `qualification_id`, versions, verdict, run order, budgets/usage, selected sources, and `scope="targeted"`.

- [ ] **Step 5: Run storage tests and preserve RED**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_targeted_execution_storage.py -q
```

Expected: import/missing-function failures.

- [ ] **Step 6: Implement isolated targeted storage**

Validate the qualification ID as one safe directory name before any path join:

```python
qid = result.qualification_id
if (
    not qid
    or Path(qid).name != qid
    or qid in {".", ".."}
    or "/" in qid
    or "\\" in qid
):
    raise TargetedExecutionError("unsafe targeted qualification_id")
```

Then create the directory with no-overwrite semantics:

```python
root = base_dir / "targeted" / qid
root.mkdir(parents=True, exist_ok=False)
```

Render markdown with an explicit banner and the existing technical table data, not the safety-summary renderer.

Write JSON deterministically with sorted keys and newline termination. `write_targeted_run()` must use exclusive create (`"xb"`) and canonical JSON bytes plus newline.

Do not write `pricing.json` in this task.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest   tests/unit/test_targeted_execution_models.py   tests/unit/test_targeted_execution_storage.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check   src/qualock/targeted_execution/models.py   src/qualock/targeted_execution/storage.py   tests/unit/test_targeted_execution_models.py   tests/unit/test_targeted_execution_storage.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports   src/qualock/targeted_execution/models.py   src/qualock/targeted_execution/storage.py
git diff --check
```

Expected: targeted tests pass; Ruff/MyPy/diff-check clean.

- [ ] **Step 8: Fresh independent Task 1 review**

Reviewer must explicitly inspect:
- targeted/full-suite filename separation;
- receipt READY/selection invariants;
- exact-byte hashing;
- no raw target context persistence;
- no open-ended artifact filename map.

Critical/Important findings enter a TDD fix loop before commit.

- [ ] **Step 9: Commit Task 1**

```bash
git add   src/qualock/targeted_execution/__init__.py   src/qualock/targeted_execution/models.py   src/qualock/targeted_execution/storage.py   tests/unit/test_targeted_execution_models.py   tests/unit/test_targeted_execution_storage.py
git diff --cached --check
git commit -m "feat: model targeted execution artifacts"
```

---

### Task 2: Local Preflight, READY Gate, and Exact Source Resolution

**Files:**
- Create: `src/qualock/targeted_execution/planning.py`
- Create: `tests/unit/test_targeted_execution_planning.py`

**Interfaces:**
- Consumes: V0 loaders/planner, `coverage_declarations()`, `load_project()`, `read_baseline_lock()`, `assert_suite_fresh()`, project/config/suite fingerprints, and `TargetedExecutionError` from Task 1.
- Produces:
  - `TargetedExecutionNotReady(Exception)` with `assessment: CoverageAssessmentV0`
  - `TargetedExecutionPlan` frozen dataclass containing signal, assessment, config, lock, all canaries, selected canaries, and four precomputed digests.
  - `prepare_targeted_execution(root: Path, signal_path: Path, context_path: Path) -> TargetedExecutionPlan`

- [ ] **Step 1: Write preflight-order RED tests**

Use monkeypatch call recording to require this order:

```text
load signal
load context
load project
read baseline lock
validate config/lock/signal identity
assert full suite/config freshness
coverage_declarations
assess_change
resolve selected IDs
```

For signal/config agent mismatch and signal/baseline version mismatch, monkeypatch `assess_change` to raise `AssertionError`; the expected command error must occur first.

For stale suite/config, monkeypatch resolver/backend factories to forbidden callables; they must never run.

- [ ] **Step 2: Write planner-disposition RED tests**

Tests:
- V0 INCOMPLETE raises `TargetedExecutionNotReady` carrying the exact assessment.
- V0 NOT_APPLICABLE raises the same typed exception.
- READY yields exact selected-canary tuple in planner lexical order.
- unrelated critical canary is absent.
- missing selected ID raises `TargetedExecutionError`.
- READY empty source set is rejected defensively even if a malformed sentinel assessment is injected below Pydantic validation.

- [ ] **Step 3: Run planning tests and preserve RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_targeted_execution_planning.py -q
```

Expected: import failure for `qualock.targeted_execution.planning`.

- [ ] **Step 4: Implement preflight without runtime/provider imports**

`planning.py` must not import Docker/host runners, credential selectors, resolver factories, or provider clients.

Use:

```python
signal = load_change_signal(signal_path)
context = load_target_context(context_path)
config, loaded_canaries = load_project(root)
canaries = tuple(loaded_canaries)
lock = read_baseline_lock(project_dir(root) / "baseline.lock")
```

Validate signal/config/lock agent identity and baseline version before `assess_change()`.

Then:

```python
project_suite_sha256 = suite_fingerprint(canaries)
config_sha256 = config_fingerprint(config)
assert_suite_fresh(lock, project_suite_sha256, config_sha256)
assessment = assess_change(signal, context, coverage_declarations(canaries))
```

If assessment is non-READY, raise `TargetedExecutionNotReady(assessment)`.

Resolve selected IDs from one exact map, preserve the planner's lexical source order, and compute:

```python
canaries_by_id = {canary.id: canary for canary in canaries}
try:
    selected_canaries = tuple(
        canaries_by_id[source_id]
        for source_id in assessment.selected_sources
    )
except KeyError as exc:
    missing_source_id = str(exc.args[0])
    raise TargetedExecutionError(
        f"selected source is missing from project: {missing_source_id}"
    ) from exc

if not selected_canaries:
    raise TargetedExecutionError(
        "READY assessment must select at least one source"
    )

selected_suite_sha256 = suite_fingerprint(selected_canaries)
baseline_lock_sha256 = sha256_canonical(lock.model_dump(mode="json"))
```

Import `TargetedExecutionError` from the Task 1 targeted-execution model/error boundary; do not define a second exception class in `planning.py`.

Do not retain raw `context` in `TargetedExecutionPlan`; only the assessment contains its digest.

- [ ] **Step 5: Historical no-coverage gate test**

Add a test using the bundled Click sentinel semantics:
- managed-shell signal/context;
- no explicit compatible coverage;
- expected `INCOMPLETE/COVERAGE_GAP`;
- no runtime/provider dependency imported or called.

- [ ] **Step 6: Verify Task 2**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest   tests/unit/test_targeted_execution_planning.py   tests/unit/test_change_targeting_commands.py   tests/unit/test_change_targeting_planner.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check   src/qualock/targeted_execution/planning.py   tests/unit/test_targeted_execution_planning.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports   src/qualock/targeted_execution/planning.py
git diff --check
```

- [ ] **Step 7: Fresh independent Task 2 review**

Reviewer must verify:
- local identity/freshness happens before planner disposition;
- no resolver/backend/provider imports in planning module;
- exact selected set only;
- defensive non-empty READY gate;
- raw context absent from returned plan.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/qualock/targeted_execution/planning.py tests/unit/test_targeted_execution_planning.py
git diff --cached --check
git commit -m "feat: gate targeted execution plans"
```

---

### Task 3: Extract a Shared Private Qualification Execution Core

**Files:**
- Modify: `src/qualock/commands.py:289-410`
- Modify: `tests/unit/test_commands.py`

**Interfaces:**
- Consumes: existing resolver/backend factories, `QualificationExecutor`, protocol-design and trace primitives.
- Produces inside `qualock.commands`:
  - private frozen dataclass `_QualificationCoreRun`;
  - private function `_execute_qualification_core(*, root: Path, config: QualockConfig, lock: BaselineLock, agent_name: str, candidate_version: str, execution_canaries: Sequence[CanarySpec], qualification_id: str, resolver: Resolver | None, backend: QualificationBackend | None, max_attempts: int | None, max_tokens: int | None) -> _QualificationCoreRun`.
- `_QualificationCoreRun` carries baseline/candidate binaries, backend, protocol design/digest, trace, result, start/end timestamps.

- [ ] **Step 1: Freeze existing full-check behavior with RED-capable characterization tests**

Before refactoring, add tests around `execute_check()` that capture:
- exact canary order sent to executor;
- full project suite SHA in paired-change protocol design;
- normal result directory remains `.qualock/results/<check-id>/`;
- filenames remain `report.md`, `report.json`, `qualification.json`, `evidence-provenance.json`, `paired-change-run-v1.json`;
- existing pricing sidecar behavior remains best effort;
- baseline binary/support fingerprint failures remain unchanged.

Use existing fake resolver/backend fixtures in `test_commands.py`; do not rewrite provider adapters.

- [ ] **Step 2: Run characterization tests before refactor**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_commands.py -q
```

Expected: PASS before refactor. Record this as pre-refactor behavioral baseline, not as TDD RED.

- [ ] **Step 3: Write new private-core unit test that fails**

Monkeypatch storage writers to forbidden and call `_execute_qualification_core()`.

Assert it:
- performs runtime resolution and paired qualification;
- does not write report/provenance/paired/pricing artifacts itself;
- uses only `execution_canaries`;
- preserves protocol-aware trace behavior.

Expected: FAIL because private core does not exist.

- [ ] **Step 4: Extract minimal private core**

Move only runtime execution logic from `execute_check()`:
- resolver selection;
- baseline binary fingerprint/support validation;
- candidate resolution;
- backend construction;
- selected protocol-design construction;
- `QualificationExecutor.run()`;
- trace collection;
- run start/end timestamps.

Keep all ordinary artifact writing in `execute_check()`.

Then rewrite `execute_check()` to:
1. keep its current validation/full-suite freshness logic;
2. call `_execute_qualification_core(root=root, config=config, lock=lock, agent_name=agent_name, candidate_version=candidate_version, execution_canaries=canaries, qualification_id=qid, resolver=resolver, backend=backend, max_attempts=max_attempts, max_tokens=max_tokens)`;
3. write the same standard artifacts using returned values.

Do not change public signatures.

Use this concrete core shape:

```python
@dataclass(frozen=True)
class _QualificationCoreRun:
    baseline_binary: AgentBinary
    candidate_binary: AgentBinary
    backend: QualificationBackend
    protocol_design: ProtocolDesignV1
    protocol_design_sha256: str
    trace: tuple[AttemptRunTrace, ...]
    result: QualificationResult
    run_started_at: datetime
    run_finished_at: datetime
```

Inside `_execute_qualification_core()`, preserve the existing sequence:

```python
resolver = resolver or _default_resolver(agent_name)
baseline_binary = resolver.resolve(lock.agent.version)
if baseline_binary.sha256 != lock.agent.binary_sha256:
    raise BaselineStaleError("baseline binary fingerprint changed")
if agent_name == "gemini" and lock.agent.support_sha256 is None:
    raise BaselineStaleError("baseline support fingerprint missing")
if agent_support_fingerprint(baseline_binary) != lock.agent.support_sha256:
    raise BaselineStaleError("baseline support fingerprint changed or missing")

candidate_binary = resolver.resolve(candidate_version)
backend = backend or _default_backend(root, config, agent_name)
control_profiles = (
    {canary.id: backend.control_profiles(canary) for canary in execution_canaries}
    if isinstance(backend, ProtocolAwareQualificationBackend)
    else None
)
protocol_design = build_protocol_design(
    suite_sha256=suite_fingerprint(execution_canaries),
    config_sha256=config_fingerprint(config),
    repetitions=config.qualification.repetitions,
    canaries=execution_canaries,
    control_profiles=control_profiles,
)
protocol_design_sha256 = digest_model(protocol_design)
trace: list[AttemptRunTrace] = []
run_started_at = datetime.now(UTC)
result = QualificationExecutor(
    backend=backend,
    repetitions=config.qualification.repetitions,
).run(
    baseline_binary,
    candidate_binary,
    execution_canaries,
    qualification_id=qualification_id,
    max_attempts=max_attempts,
    max_tokens=max_tokens,
    trace_sink=trace,
    trace_design_sha256=protocol_design_sha256,
)
run_finished_at = datetime.now(UTC)
```

Return those exact values in `_QualificationCoreRun`. Existing `execute_check()` then persists its ordinary artifacts exactly as before.

- [ ] **Step 5: Prove full-check regression compatibility**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest   tests/unit/test_commands.py   tests/unit/test_cli.py   tests/unit/test_evidence_provenance.py   tests/unit/test_paired_change_run_sidecar.py -q
```

Expected: all pass with no changed snapshots/paths/exit semantics.

- [ ] **Step 6: Static verification**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check   src/qualock/commands.py tests/unit/test_commands.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports src/qualock/commands.py
git diff --check
```

- [ ] **Step 7: Fresh independent Task 3 review**

Reviewer focuses on:
- no change to ordinary check semantics;
- core performs no persistence;
- execution subset controls protocol design/executor input exactly;
- baseline runtime fingerprint/support rules unchanged;
- no new public API.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/qualock/commands.py tests/unit/test_commands.py
git diff --cached --check
git commit -m "refactor: share qualification execution core"
```

---

### Task 4: READY-Only Targeted Qualification Orchestration and Receipt Binding

**Files:**
- Create: `src/qualock/targeted_execution/commands.py`
- Create: `tests/unit/test_targeted_execution_commands.py`
- Modify only if a Task 4 RED test proves the Task 1 contract insufficient: `src/qualock/targeted_execution/models.py`
- Modify only if a Task 4 RED test proves the Task 1 contract insufficient: `src/qualock/targeted_execution/storage.py`

**Interfaces:**
- Consumes: `prepare_targeted_execution()`, `_execute_qualification_core()`, Task 1 `TargetedRunV1`, `TargetedArtifactHashesV1`, `write_targeted_qualification_artifacts()`, `sha256_file()`, `write_targeted_run()`, existing provenance and paired-change builders/writers, pricing helpers.

**Task 1 contract reused unchanged by default:**
- `TargetedRunV1` already owns every required receipt field and validates READY, selected-source equality, non-empty/sorted/unique selected sources, attempted-source subset invariants, and strict 64-hex digests.
- `TargetedArtifactHashesV1` already exposes exactly the four required targeted JSON companion hashes.
- `write_targeted_qualification_artifacts()` already creates `results/targeted/<qualification_id>/` with no-overwrite semantics and writes only `targeted-report.md`, `targeted-report.json`, and `targeted-qualification.json`.
- `sha256_file()` hashes exact persisted bytes.
- `write_targeted_run()` writes canonical receipt bytes with exclusive-create semantics.

Task 4 should not redesign those APIs. If a Task 4 RED test demonstrates that one binding cannot be expressed with the Task 1 contract, make the smallest TDD change to the relevant Task 1 file, rerun Task 1 tests, and include that file in the Task 4 review/commit.
- Produces:

```python
@dataclass(frozen=True)
class TargetedExecutionOutcome:
    agent_name: str
    assessment: CoverageAssessmentV0
    result: QualificationResult
    result_dir: Path
    receipt: TargetedRunV1
```

- Function signature: `execute_targeted_check(root: Path, signal_path: Path, context_path: Path, *, resolver: Resolver | None = None, backend: QualificationBackend | None = None, qualification_id: str | None = None, max_attempts: int | None = None, max_tokens: int | None = None) -> TargetedExecutionOutcome`.

- [ ] **Step 1: Write exact-selection RED tests**

Create a project with:
- `probe-a` and `probe-b` selected;
- `alternative-c` with compatible coverage but not in deterministic minimal set;
- unrelated critical `critical-d`.

Fake backend records `prepare()` and attempt calls.

Assert only selected source IDs execute, in planner selected-source order. No unrelated critical/fallback source may appear.

- [ ] **Step 2: Write no-fallback RED tests**

Cases:
- backend `prepare(probe-a)` raises;
- attempts are invalid;
- attempt budget skips a selected source;
- token budget skips a selected source.

Assertions:
- alternative compatible source is never prepared;
- selected source tuple in receipt does not change;
- budget-generated result is INCOMPLETE;
- `attempted_sources` equals sorted selected execution IDs having non-empty attempts;
- no replacement source appears.

- [ ] **Step 3: Write targeted-evidence RED tests**

Assert a completed run writes only under `.qualock/results/targeted/<qid>/`:
- targeted report/qualification wrappers;
- targeted evidence provenance;
- `targeted-paired-change-run-v1.json`;
- receipt last;
- optional `pricing.json`.

Assert:
- every targeted artifact is under exactly `.qualock/results/targeted/<qid>/`; no targeted file is written elsewhere under `.qualock/results/`;
- standard `report.json`, `qualification.json`, `evidence-provenance.json`, `paired-change-run-v1.json` do not exist;
- provenance contains only selected canaries;
- targeted paired-change design uses selected-suite SHA;
- receipt project-suite SHA is full project fingerprint;
- receipt selected-suite SHA is selected subset fingerprint;
- `assessment_sha256 == sha256(canonical_assessment_bytes(assessment)).hexdigest()`;
- `run_order_sha256 == sha256_canonical(result.run_order)`;
- baseline lock/config hashes match exact preflight values;
- required artifact SHA values equal exact file bytes;
- `TargetedArtifactHashesV1` has exactly the four required targeted JSON digest fields and cannot include `pricing.json`;
- raw target context values are absent from every targeted artifact.

Add a late-required-evidence-failure case: make the targeted provenance or targeted paired-change writer fail after the targeted report directory/files exist. Assert `execute_targeted_check()` raises `TargetedExecutionError`, `targeted-run-v1.json` is absent, no `TargetedExecutionOutcome` is returned, and the partial targeted directory is allowed to remain. This locks the approved non-transactional V1 behavior without treating the partial directory as success.

- [ ] **Step 4: Run command tests and preserve RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_targeted_execution_commands.py -q
```

Expected: import failure for targeted execution command.

- [ ] **Step 5: Implement READY-only orchestration**

First call:

```python
plan = prepare_targeted_execution(root, signal_path, context_path)
```

No runtime resolution may precede that return. Pass `resolver`, `backend`, `max_attempts`, and `max_tokens` through unchanged to `_execute_qualification_core()`. When `resolver` or `backend` is `None`, Task 3's shared core must reuse the existing ordinary-check `_default_resolver()` / `_default_backend()` behavior; do not invent a targeted-only stub/default. Budget enforcement likewise remains owned by the shared core plus existing `QualificationExecutor` semantics.

Generate the default ID with the existing private `qualock.commands._qualification_id("target-check")`; do not introduce a second ID-format helper.

Call:

```python
core = _execute_qualification_core(
    root=root,
    config=plan.config,
    lock=plan.lock,
    agent_name=plan.signal.agent,
    candidate_version=plan.signal.candidate_version,
    execution_canaries=plan.selected_canaries,
    qualification_id=qid,
    resolver=resolver,
    backend=backend,
    max_attempts=max_attempts,
    max_tokens=max_tokens,
)
```

Persist targeted report wrappers first, then selected-only provenance and targeted-named paired-change run. Pricing remains best effort.

Compute:

```python
attempted_sources = tuple(sorted({
    execution.canary_id
    for execution in core.result.executions
    if execution.attempts
}))
```

Build the receipt directly from `plan.assessment`, `plan.assessment.selected_sources`, and the just-computed `attempted_sources`; do not re-read or reconstruct those values from persisted files. `TargetedRunV1` re-validates READY, exact selected-source equality, non-empty/sorted/unique selected sources, and attempted-source subset invariants. Then write the receipt last with the Task 1 `write_targeted_run()` API, whose contract is exclusive create (`"xb"`) and must never overwrite an existing receipt.

Use the concrete persistence sequence:

```python
result_dir = write_targeted_qualification_artifacts(
    project_dir(root) / "results",
    result=core.result,
    assessment=plan.assessment,
    selected_sources=plan.assessment.selected_sources,
    agent_display_name=agent_display_name(plan.signal.agent),
)
provenance = build_evidence_provenance(
    lock=plan.lock,
    baseline_binary=core.baseline_binary,
    candidate_binary=core.candidate_binary,
    config=plan.config,
    canaries=plan.selected_canaries,
    result=core.result,
)
write_evidence_provenance(
    result_dir / "targeted-evidence-provenance-v1.json",
    provenance,
)
paired = build_paired_change_run(
    protocol_design=core.protocol_design,
    protocol_design_sha256=core.protocol_design_sha256,
    qualification_id=core.result.qualification_id,
    baseline_state=build_agent_dependency_state(core.baseline_binary, plan.lock.model),
    candidate_state=build_agent_dependency_state(core.candidate_binary, plan.lock.model),
    result=core.result,
    trace=core.trace,
)
write_paired_change_run(
    result_dir / "targeted-paired-change-run-v1.json",
    paired,
)
```

Then build the receipt with the exact values that just drove execution:

```python
receipt = TargetedRunV1(
    schema_version=1,
    protocol_id="selected-source-execution/v1",
    qualification_id=core.result.qualification_id,
    assessment=plan.assessment,
    assessment_sha256=hashlib.sha256(
        canonical_assessment_bytes(plan.assessment)
    ).hexdigest(),
    project_suite_sha256=plan.project_suite_sha256,
    selected_suite_sha256=plan.selected_suite_sha256,
    config_sha256=plan.config_sha256,
    baseline_lock_sha256=plan.baseline_lock_sha256,
    selected_sources=plan.assessment.selected_sources,
    attempted_sources=attempted_sources,
    qualification_verdict=core.result.verdict,
    run_order_sha256=sha256_canonical(core.result.run_order),
    artifacts=TargetedArtifactHashesV1(
        targeted_report_json=sha256_file(result_dir / "targeted-report.json"),
        targeted_qualification_json=sha256_file(
            result_dir / "targeted-qualification.json"
        ),
        targeted_evidence_provenance_v1_json=sha256_file(
            result_dir / "targeted-evidence-provenance-v1.json"
        ),
        targeted_paired_change_run_v1_json=sha256_file(
            result_dir / "targeted-paired-change-run-v1.json"
        ),
    ),
)
write_targeted_run(result_dir / "targeted-run-v1.json", receipt)
```

Return `TargetedExecutionOutcome(agent_name=plan.signal.agent, assessment=plan.assessment, result=core.result, result_dir=result_dir, receipt=receipt)` so the CLI and receipt use the same validated assessment instance.

If required provenance/paired-change/receipt writing fails, raise `TargetedExecutionError`; do not report success. Do not add rollback/transactional publication in V1: the approved spec explicitly permits a partially written targeted directory after a late evidence failure, provided the command never reports that run as successful.

- [ ] **Step 6: Keep optional pricing outside required receipt hashes**

A pricing failure must not invalidate the required receipt. Reuse the existing private `_write_pricing_sidecar_best_effort(result_dir, plan.config, core.result, core.run_started_at, core.run_finished_at)` helper from `qualock.commands`; it already catches pricing-generation/write failures at the advisory boundary. If `pricing.json` exists, it remains an advisory sidecar; V1 required artifact hashes contain only the four specified required targeted JSON companions.

Test both pricing success and forced pricing failure, and assert the receipt's `TargetedArtifactHashesV1` remains exactly the four required non-pricing fields.

- [ ] **Step 7: Prove history/cost/export isolation at storage level**

Use real current scanners:
- `scan_results(.qualock/results)` must not load the targeted nested run;
- pricing scan over returned history must not see targeted pricing;
- `export_evidence_bundle(root, "target-check-q1", dest)` must fail because there is no direct results child with that ID;
- unsafe qualification ID `"targeted/target-check-q1"` must be rejected by export path validation.

- [ ] **Step 8: Verify Task 4**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_targeted_execution_commands.py \
  tests/unit/test_targeted_execution_models.py \
  tests/unit/test_targeted_execution_storage.py \
  tests/unit/test_commands.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/targeted_execution \
  tests/unit/test_targeted_execution_commands.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports \
  src/qualock/targeted_execution
git diff --check
```

Also run the repository's actual history/evidence modules containing `scan_results` and `export_evidence_bundle`; use their real filenames discovered at implementation time rather than creating duplicate compatibility tests.

- [ ] **Step 9: Fresh independent Task 4 review**

Reviewer must inspect:
- provider work cannot happen before READY;
- exact selected source set only;
- no fallback after failure/budget conditions;
- targeted-only filenames;
- full vs selected suite digests;
- exact receipt byte hashes;
- raw-context privacy;
- nested scanner/export isolation.

- [ ] **Step 10: Commit Task 4**

```bash
git add \
  src/qualock/targeted_execution/commands.py \
  tests/unit/test_targeted_execution_commands.py

# Only if a Task 4 RED test required a reviewed TDD extension of the Task 1 contract:
git add src/qualock/targeted_execution/models.py src/qualock/targeted_execution/storage.py

git diff --cached --check
git commit -m "feat: execute targeted qualification sources"
```

---

### Task 5: Targeted Terminal Rendering and Exact CLI Exit Contract

**Files:**
- Create: `src/qualock/targeted_execution/render.py`
- Create: `tests/unit/test_targeted_execution_cli.py`
- Modify: `src/qualock/cli.py`

**Interfaces:**
- Consumes: `execute_targeted_check()`, `TargetedExecutionNotReady`, `TargetedExecutionOutcome`, V0 assessment renderer, qualification Verdict.
- Produces:
  - `render_targeted_execution_not_started(assessment: CoverageAssessmentV0 | None, diagnostic: str | None = None) -> str`
  - `render_targeted_qualification(outcome: TargetedExecutionOutcome, *, agent_display_name: str) -> str`
  - Typer command `target-check`.

- [ ] **Step 1: Write CLI parser/exit RED tests**

Required matrix:
- missing `--context` -> 3;
- unknown option -> 3;
- invalid signal/config -> 3;
- stale/mismatched baseline preflight -> 3 + `Targeted execution: NOT STARTED`;
- planner INCOMPLETE -> 4 + assessment + NOT STARTED;
- planner NOT_APPLICABLE -> 5 + assessment + NOT STARTED;
- executed PASS -> 0;
- executed WARN -> 0;
- executed BLOCK -> 2;
- executed INCOMPLETE -> 6;
- unexpected error -> 1 with bounded generic diagnostic and no exception detail.

Use the existing `_InputErrorExit3TyperCommand` for command-local parser errors.

- [ ] **Step 2: Write rendering/privacy RED tests**

PASS output must contain:
- `QuaLock Targeted Qualification`;
- `Scope: selected sources only; not a full-suite update-safety verdict.`;
- selected source IDs;
- technical per-canary results;
- targeted result directory.

It must not contain:
- `SAFE TO UPDATE`;
- raw context values;
- full-suite protected-workflow safety summary.

Non-start output must end with:

```text
Targeted execution: NOT STARTED
```

- [ ] **Step 3: Write provider-gate CLI RED tests**

For INCOMPLETE and NOT_APPLICABLE end-to-end project fixtures, monkeypatch:
- default resolver;
- default backend;
- Docker runner;
- Linux host runner;
- Claude/Gemini credential selectors;
- `httpx.get` and `httpx.Client`.

All forbidden callables raise `AssertionError`; CLI must still exit 4/5 correctly.

Also provide a URL in `source.ref` and prove it is never dereferenced.

- [ ] **Step 4: Run CLI tests and preserve RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_targeted_execution_cli.py -q
```

Expected: missing render/command implementation.

- [ ] **Step 5: Implement targeted renderers**

Renderer may reuse the V0 assessment text and existing technical qualification table concepts, but it must be plain/scoped and must not call `_render_safety_result()`.

Do not accept raw `TargetContextV0` as a renderer argument.

Use a deterministic plain-text composition:

```python
def render_targeted_qualification(
    outcome: TargetedExecutionOutcome,
    *,
    agent_display_name: str,
) -> str:
    technical = render_terminal(
        outcome.result,
        agent_display_name=agent_display_name,
    )
    selected = ", ".join(outcome.assessment.selected_sources)
    return (
        "QuaLock Targeted Qualification\n\n"
        "Scope: selected sources only; not a full-suite update-safety verdict.\n"
        f"Selected sources: {selected}\n"
        f"Result directory: {outcome.result_dir}\n\n"
        + technical
    )
```

`render_targeted_execution_not_started()` may prepend `render_coverage_assessment(assessment)` when an assessment exists, but its final line must be exactly `Targeted execution: NOT STARTED`.

- [ ] **Step 6: Add `target-check` to CLI**

Add typed `Path` argument/option defaults at module scope following existing Ruff/Typer conventions.

Command behavior:
1. validate positive budgets;
2. call targeted orchestration;
3. map preflight/input errors to 3 and print NOT STARTED;
4. catch `TargetedExecutionNotReady`, render its assessment, map INCOMPLETE->4 / NOT_APPLICABLE->5;
5. bound unexpected errors to `targeted execution failed` without raw exception text;
6. render completed targeted result;
7. map BLOCK->2 / INCOMPLETE->6 / PASS|WARN->0.

Do not change `target-change`.

Use this exception/exit structure:

```python
try:
    outcome = execute_targeted_check(
        Path.cwd(),
        signal,
        context,
        max_attempts=max_attempts,
        max_tokens=max_tokens,
    )
except TargetedExecutionNotReady as exc:
    console.print(render_targeted_execution_not_started(exc.assessment), end="", markup=False)
    if exc.assessment.status is AssessmentStatus.INCOMPLETE:
        raise typer.Exit(4) from exc
    raise typer.Exit(5) from exc
except (
    ChangeTargetingInputError,
    ConfigError,
    CanaryLoadError,
    CommandError,
    TargetedExecutionError,
    BaselineStaleError,
    FileNotFoundError,
) as exc:
    console.print(str(exc), markup=False)
    console.print("Targeted execution: NOT STARTED", markup=False)
    raise typer.Exit(3) from exc
except Exception as exc:  # noqa: BLE001 - bounded CLI operational boundary
    console.print("targeted execution failed", markup=False)
    raise typer.Exit(1) from exc

console.print(
    render_targeted_qualification(
        outcome,
        agent_display_name=agent_display_name(outcome.agent_name),
    ),
    end="",
    markup=False,
)
if outcome.result.verdict is Verdict.BLOCK:
    raise typer.Exit(2)
if outcome.result.verdict is Verdict.INCOMPLETE:
    raise typer.Exit(6)
```

`TargetedExecutionOutcome.agent_name` is the already validated signal/config agent name from the execution plan; do not re-read or infer it from terminal text.

- [ ] **Step 7: Verify Task 5**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_targeted_execution_cli.py \
  tests/unit/test_change_targeting_cli.py \
  tests/unit/test_cli.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/cli.py \
  src/qualock/targeted_execution/render.py \
  tests/unit/test_targeted_execution_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports \
  src/qualock/cli.py \
  src/qualock/targeted_execution/render.py
git diff --check
```

- [ ] **Step 8: Fresh independent Task 5 review**

Reviewer explicitly checks:
- exit 4 vs 6 pre/post execution distinction;
- exit 5 NOT_APPLICABLE;
- no safety-summary/full-suite claim reuse;
- bounded operational errors;
- NOT STARTED banner for every pre-execution abort;
- V0 `target-change` parser/exits unchanged.

- [ ] **Step 9: Commit Task 5**

```bash
git add \
  src/qualock/cli.py \
  src/qualock/targeted_execution/render.py \
  tests/unit/test_targeted_execution_cli.py
git diff --cached --check
git commit -m "feat: expose targeted qualification cli"
```

---

### Task 6: Historical Managed-Shell Acceptance and Whole-Branch Verification

**Files:**
- Create: `tests/integration/test_targeted_execution_managed_shell.py`
- Modify only if a proven bug requires TDD fix: files from Tasks 1-5.

**Interfaces:**
- Consumes complete V1 public/local command behavior.
- Produces no new production API unless acceptance exposes a spec violation.

- [ ] **Step 1: Add legacy coverage-gap acceptance**

Use the real bundled Click sentinel and managed-shell signal/context.

Preflight requires a valid local baseline fixture, but resolver/backend/provider are forbidden.

Call the targeted orchestration with a resolver and backend test double whose every method raises `AssertionError("execution boundary crossed")`.

Assert:
- `TargetedExecutionNotReady.assessment.status is INCOMPLETE`;
- uncovered reason is exactly `COVERAGE_GAP`;
- neither forbidden resolver nor forbidden backend is touched;
- `.qualock/results/targeted/` does not exist after the failure.

The CLI exit-4 mapping is already pinned in Task 5; this integration acceptance proves the deeper no-runtime boundary directly.

- [ ] **Step 2: Add dedicated explicit-probe acceptance with fake runtime**

Create a temporary project source `managed-shell-registration-probe` declaring:

```yaml
coverage:
  - contract_id: command.execution
    context_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
```

Use a fake resolver and a recording fake qualification backend, not authenticated provider access.

Add an unrelated target-context fact with a unique sentinel raw value such as `"DO-NOT-PERSIST-RAW-CONTEXT"`. The signal must not require that fact, so it has no reason to appear in the normalized assessment.

The backend must record every canary ID passed to `prepare()` and every `run_attempt()`. After `execute_targeted_check(...)`, assert all of the following directly:

```python
assert outcome.assessment.selected_sources == (
    "managed-shell-registration-probe",
)
assert tuple(item.canary_id for item in outcome.result.executions) == (
    "managed-shell-registration-probe",
)
assert backend.prepared_canary_ids == [
    "managed-shell-registration-probe",
]
assert set(backend.attempt_canary_ids) == {
    "managed-shell-registration-probe",
}
assert "click-sentinel" not in backend.prepared_canary_ids
assert "click-sentinel" not in backend.attempt_canary_ids
assert outcome.receipt.selected_sources == (
    "managed-shell-registration-probe",
)
assert outcome.result_dir.parent.name == "targeted"

sentinel = "DO-NOT-PERSIST-RAW-CONTEXT"
for artifact in outcome.result_dir.iterdir():
    if artifact.is_file():
        assert sentinel not in artifact.read_text(errors="ignore")
```

This proves the exact selected source executed, the legacy Click canary did not run, and unrelated raw context values were not persisted, without patching planner selection or Click/Typer internals.

- [ ] **Step 3: Add no-integration regression guards**

Monkeypatch `qualock.targeted_execution.commands.execute_targeted_check` to a forbidden callable that raises `AssertionError`, then exercise the existing independent entry points:
- `qualock.release_monitor.commands.execute_monitor`;
- `qualock.github_pr.commands.qualify_prepared_pr`;
- `qualock.version_bisect.commands.execute_bisect`.

Use each subsystem's existing unit fixtures/fake dependencies so the call reaches its normal orchestration path. The forbidden targeted-execution callable must remain untouched, proving these flows do not integrate V1.

Run existing first-bad/paired-change verification tests to prove no algorithm/schema coupling.

- [ ] **Step 4: Run complete targeted suite**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_targeted_execution_models.py \
  tests/unit/test_targeted_execution_storage.py \
  tests/unit/test_targeted_execution_planning.py \
  tests/unit/test_targeted_execution_commands.py \
  tests/unit/test_targeted_execution_cli.py \
  tests/integration/test_targeted_execution_managed_shell.py \
  tests/unit/test_change_targeting_models.py \
  tests/unit/test_change_targeting_canonical.py \
  tests/unit/test_change_targeting_matching.py \
  tests/unit/test_change_targeting_planner.py \
  tests/unit/test_change_targeting_io.py \
  tests/unit/test_change_targeting_commands.py \
  tests/unit/test_change_targeting_cli.py -q
```

Expected: all pass.

- [ ] **Step 5: Run full repository tests**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
```

Expected: no regression from branch baseline of **2766 passed, 52 skipped**, plus the new V1 tests.

- [ ] **Step 6: Run scoped and repository static checks**

First, V1/changed paths must be fully clean:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/targeted_execution \
  src/qualock/commands.py \
  src/qualock/cli.py \
  tests/unit/test_targeted_execution_models.py \
  tests/unit/test_targeted_execution_storage.py \
  tests/unit/test_targeted_execution_planning.py \
  tests/unit/test_targeted_execution_commands.py \
  tests/unit/test_targeted_execution_cli.py \
  tests/integration/test_targeted_execution_managed_shell.py

/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports src/qualock
git diff --check
```

Then run raw repository checks for debt classification:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check src tests
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock
```

Do not fix unrelated pre-existing findings. Record whether any finding is newly introduced by V1.

- [ ] **Step 7: Fresh whole-branch independent review**

Review from spec commit `7316030ea0dd01a5379f4772b85b6b8c656b644c` through branch HEAD.

The reviewer must explicitly adjudicate:
1. fail-open execution before READY;
2. local identity/freshness ordering;
3. empty READY defense;
4. source widening/fallback;
5. full vs targeted claim confusion;
6. targeted artifact filename/schema isolation;
7. history/cost/evidence-export leakage;
8. project-suite vs selected-suite digest binding;
9. receipt exact-byte hashes and canonical assessment/run-order hashes;
10. raw-context privacy;
11. pre-spend vs post-spend exit codes;
12. ordinary check/target-change/monitor/PR/bisect/paired/first-bad regression.

Critical/Important findings block closure and require a TDD fix + scoped re-review.

- [ ] **Step 8: Commit historical acceptance**

Only after review is clean:

```bash
git add tests/integration/test_targeted_execution_managed_shell.py
git diff --cached --check
git commit -m "test: lock selected-source execution boundaries"
```

If Task 6 required a production fix, include only the reviewed fix files in this commit or make a separate scoped fix commit before the acceptance commit.

- [ ] **Step 9: Final completion gate**

Run on exact final HEAD:

```bash
git status --porcelain
git log --oneline --max-count=8
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/targeted_execution src/qualock/commands.py src/qualock/cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy --ignore-missing-imports src/qualock
git diff --check
```

Required:
- clean worktree;
- no unresolved Critical/Important review findings;
- full tests pass;
- changed/V1 paths lint clean;
- no new real MyPy errors;
- no push/PR/merge/tag/release without explicit permission.
