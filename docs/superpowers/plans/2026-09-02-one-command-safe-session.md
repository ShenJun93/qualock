# One-Command Safe Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `qualock start` as one state-aware foreground entrypoint that safely chooses existing setup/protect/watch primitives without silently replacing trusted control state or manual protections.

**Architecture:** Add a small `qualock.project_start` orchestration package. A read-only preparation phase classifies the project as `LOCKED`, `CONFIGURED_UNLOCKED`, or `UNCONFIGURED`; bootstrap paths re-check lock absence immediately before mutation; the CLI renders existing setup/protect/watch outputs and delegates all protection, signing, readiness, and watch behavior to existing subsystems.

**Tech Stack:** Python 3.11+, Typer, Pydantic, pathlib, pytest, existing QuaLock project-setup/project-protection/project-watch packages.

**Spec:** `docs/superpowers/specs/2026-09-02-one-command-safe-session-design.md`

## Global Constraints

- `qualock start` is orchestration only; do not create a second setup, protection, signing, verification, or watch engine.
- Three start states are fixed: `LOCKED`, `CONFIGURED_UNLOCKED`, `UNCONFIGURED`.
- Any directory entry at `.qualock/project.lock`, including malformed bytes, a directory, or a dangling symlink, classifies as `LOCKED`.
- A malformed/unreadable existing `.qualock/config.yaml` is an error; never fall back to fresh setup or overwrite it.
- Existing configured protections are preserved; do not replace them with recommended packs.
- Before any unlocked bootstrap mutation/protection execution, re-check with `lstat` semantics that no `project.lock` directory entry has appeared.
- Existing lock/key integrity failure is terminal for the invocation; never fall back from `LOCKED` to setup/protect.
- `--yes` skips confirmation only. It does not skip readiness, protection runs, signing, watch authentication, or initial watch verification.
- Fresh setup readiness failure and user cancellation remain mutation-free.
- Do not install dependencies or run `uv sync`, `poetry install`, `npm install`, or `npm ci`.
- After bootstrap PASS, enter the existing normal `run_watch`; do not reuse bootstrap PASS as watch's initial authoritative result.
- Preserve existing `setup`, `protect`, `verify`, and `watch` command semantics.
- Project-controlled display names must be rendered with Rich markup disabled.
- No daemon, IDE integration, auto-fix/revert, monorepo fan-out, agent detection, notifications, or background process manager.
- Qualification/baseline/canary execution behavior must have zero functional change.

---

## File map

- Create `src/qualock/project_start/__init__.py` — public orchestration exports only.
- Create `src/qualock/project_start/models.py` — immutable start-state and plan models.
- Create `src/qualock/project_start/commands.py` — read-only preparation, lock-entry guard, and bootstrap apply helpers.
- Create `tests/unit/test_project_start_state.py` — fail-closed state classification and config loading.
- Create `tests/unit/test_project_start_flow.py` — configured/fresh bootstrap ordering, cancellation-side-effect helpers, stale-plan lock guard.
- Modify `src/qualock/cli.py` — add `qualock start`; reuse existing renderers and watch-event adapter.
- Create `tests/unit/test_project_start_cli.py` — end-to-end CLI branch selection/rendering/exits.
- Modify `README.md` — make `qualock start` the simplest safe-session path while retaining manual commands.
- Modify no qualification/baseline/run/evidence engine files except if an existing helper extraction is strictly required and behavior-preserving.

---

### Task 1: Fail-closed start-state classification

**Files:**
- Create: `src/qualock/project_start/__init__.py`
- Create: `src/qualock/project_start/models.py`
- Create: `src/qualock/project_start/commands.py`
- Create: `tests/unit/test_project_start_state.py`

**Interfaces:**
- Consumes: `qualock.project.project_dir`, `qualock.config.io.load_config`, `qualock.config.models.ProjectProtectionConfig`, `qualock.project_setup.models.ProtectionLevel`, `qualock.project_setup.commands.build_setup_plan`.
- Produces:
  - `StartProjectState(str, Enum)` with `LOCKED`, `CONFIGURED_UNLOCKED`, `UNCONFIGURED`.
  - immutable `StartPlan(state, level, setup_plan, configured_protections)`.
  - `StartStateError(ValueError)` for invalid `.qualock` parent-state.
  - `prepare_start(root: Path, level: ProtectionLevel) -> StartPlan`.
  - `assert_bootstrap_lock_absent(root: Path) -> None`.
  - `StartStateChangedError(RuntimeError)` when a lock entry appears after preparation.

- [ ] **Step 1: Write RED state-classification tests**

Create `tests/unit/test_project_start_state.py` with helpers that build `.qualock` states without requiring a valid signed lock.

Cover exact cases:

```python
def test_regular_lock_entry_classifies_locked(tmp_path: Path) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "project.lock").write_bytes(b"not-even-json")
    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)
    assert plan.state is StartProjectState.LOCKED
    assert plan.setup_plan is None
```

```python
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support required")
def test_dangling_lock_symlink_classifies_locked(tmp_path: Path) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "project.lock").symlink_to(qdir / "missing-target")
    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)
    assert plan.state is StartProjectState.LOCKED
```

```python
def test_lock_directory_classifies_locked(tmp_path: Path) -> None:
    path = tmp_path / ".qualock" / "project.lock"
    path.mkdir(parents=True)
    assert prepare_start(tmp_path, ProtectionLevel.RECOMMENDED).state is StartProjectState.LOCKED
```

```python
def test_qualock_parent_file_is_error(tmp_path: Path) -> None:
    (tmp_path / ".qualock").write_text("not a directory", encoding="utf-8")
    with pytest.raises(StartStateError, match="\\.qualock"):
        prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)
```

For unlocked config branches, monkeypatch `build_setup_plan` only for `UNCONFIGURED` so the state test is independent of actual project detection:

```python
def test_existing_protections_classify_configured_unlocked(tmp_path: Path) -> None:
    write_config_with_protection(tmp_path)
    plan = prepare_start(tmp_path, ProtectionLevel.STRONG)
    assert plan.state is StartProjectState.CONFIGURED_UNLOCKED
    assert tuple(p.id for p in plan.configured_protections) == ("tests",)
    assert plan.setup_plan is None
```

```python
def test_missing_config_builds_unconfigured_setup_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = fake_setup_plan()
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", lambda root, level: expected)
    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)
    assert plan.state is StartProjectState.UNCONFIGURED
    assert plan.setup_plan is expected
```

```python
def test_valid_config_without_protections_uses_unconfigured_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_valid_config_without_protections(tmp_path)
    expected = fake_setup_plan()
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", lambda root, level: expected)
    plan = prepare_start(tmp_path, ProtectionLevel.MINIMAL)
    assert plan.state is StartProjectState.UNCONFIGURED
    assert plan.setup_plan is expected
```

Malformed config must propagate `ConfigError` and must prove `build_setup_plan` was not called.

- [ ] **Step 2: Run the state tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_start_state.py
```

Expected: collection/import failure because `qualock.project_start` does not exist.

- [ ] **Step 3: Implement immutable models**

In `src/qualock/project_start/models.py`:

```python
from enum import Enum
from pydantic import BaseModel, ConfigDict
from qualock.config.models import ProjectProtectionConfig
from qualock.project_setup.models import ProtectionLevel, SetupPlan

class StartProjectState(str, Enum):
    LOCKED = "locked"
    CONFIGURED_UNLOCKED = "configured_unlocked"
    UNCONFIGURED = "unconfigured"

class StartPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: StartProjectState
    level: ProtectionLevel
    setup_plan: SetupPlan | None = None
    configured_protections: tuple[ProjectProtectionConfig, ...] = ()
```

If Pydantic rejects the dataclass-typed `SetupPlan` in this project's version, use a frozen dataclass for `StartPlan`; keep the same fields and immutability.

- [ ] **Step 4: Implement conservative directory-entry inspection**

In `commands.py`, use `Path.lstat()` rather than `exists()` for the lock entry:

```python
def _directory_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True
```

Inspect `.qualock` itself with `lstat`. If it exists and is not a directory, raise `StartStateError` without deleting/replacing it.

Decision order is mandatory:

```text
invalid .qualock parent -> error
project.lock directory entry -> LOCKED (do not load config)
no lock -> load existing config if present
valid protections -> CONFIGURED_UNLOCKED
missing/valid-empty config -> build_setup_plan -> UNCONFIGURED
malformed config -> propagate error
```

Do not authenticate or parse `project.lock` in this function.

- [ ] **Step 5: Implement stale-plan lock guard**

```python
class StartStateChangedError(RuntimeError):
    pass

def assert_bootstrap_lock_absent(root: Path) -> None:
    lock_path = project_dir(root) / "project.lock"
    if _directory_entry_exists(lock_path):
        raise StartStateChangedError(
            "QuaLock project protection state changed while preparing this session. "
            "Run qualock start again."
        )
```

The helper must not unlink, parse, or overwrite the newly observed entry.

- [ ] **Step 6: Run state suite GREEN**

Run the Task 1 suite. Expected: all pass.

Also run:

```bash
/tmp/qualock-static-22-final/bin/ruff check src/qualock/project_start tests/unit/test_project_start_state.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/project_start
git diff --check
```

If the static environment no longer exists, create a temporary venv outside the repository with the project's dev/static dependencies; do not modify project dependency metadata merely for local tooling.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/qualock/project_start tests/unit/test_project_start_state.py
git commit -m "feat: classify safe session project state"
```

Reviewer gate: verify no branch authenticates/parses an existing lock before classifying it `LOCKED`, and dangling symlink/directory entries cannot downgrade to fresh setup.

---

### Task 2: Bootstrap orchestration with state-change guard

**Files:**
- Modify: `src/qualock/project_start/commands.py`
- Create: `tests/unit/test_project_start_flow.py`

**Interfaces:**
- Consumes: Task 1 `StartPlan`, `assert_bootstrap_lock_absent`; existing `execute_protect`, `apply_setup_plan`.
- Produces:
  - `StartBootstrapResult` immutable value containing `protect_result: ProjectProtectResult | None` and `bootstrap_performed: bool`.
  - `apply_start_bootstrap(root: Path, plan: StartPlan, *, key_path: Path | None = None) -> StartBootstrapResult`.
- `LOCKED` returns no bootstrap work and never calls setup/protect.
- `CONFIGURED_UNLOCKED` calls exact existing `execute_protect`.
- `UNCONFIGURED` calls exact existing `apply_setup_plan`.
- Both unlocked branches re-check lock absence immediately before those calls.

- [ ] **Step 1: Write RED orchestration tests**

Create `tests/unit/test_project_start_flow.py`.

Pin ordering using call logs:

```python
def test_configured_unlocked_rechecks_lock_before_protect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("qualock.project_start.commands.assert_bootstrap_lock_absent",
                        lambda root: calls.append("guard"))
    monkeypatch.setattr("qualock.project_start.commands.execute_protect",
                        lambda root, key_path=None: calls.append("protect") or pass_protect_result())
    result = apply_start_bootstrap(tmp_path, configured_plan())
    assert calls == ["guard", "protect"]
    assert result.bootstrap_performed is True
```

```python
def test_unconfigured_rechecks_lock_before_apply_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("qualock.project_start.commands.assert_bootstrap_lock_absent", lambda root: calls.append("guard"))
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", lambda root, plan, key_path=None:
                        calls.append("setup") or pass_protect_result())
    apply_start_bootstrap(tmp_path, unconfigured_plan())
    assert calls == ["guard", "setup"]
```

```python
def test_locked_plan_does_no_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qualock.project_start.commands.execute_protect", fail_if_called)
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", fail_if_called)
    result = apply_start_bootstrap(tmp_path, locked_plan())
    assert result.bootstrap_performed is False
    assert result.protect_result is None
```

Add real-filesystem race tests:
- prepare unlocked plan;
- create regular lock / dangling symlink / directory before apply;
- `apply_start_bootstrap` raises `StartStateChangedError`;
- original new entry remains untouched;
- mocked setup/protect call count is zero.

- [ ] **Step 2: Run Task 2 tests to verify RED**

Expected: `apply_start_bootstrap`/result type missing.

- [ ] **Step 3: Implement minimal bootstrap dispatch**

Use exact state branches, no generic fallback:

```python
def apply_start_bootstrap(root: Path, plan: StartPlan, *, key_path: Path | None = None) -> StartBootstrapResult:
    if plan.state is StartProjectState.LOCKED:
        return StartBootstrapResult(protect_result=None, bootstrap_performed=False)

    assert_bootstrap_lock_absent(root)

    if plan.state is StartProjectState.CONFIGURED_UNLOCKED:
        result = execute_protect(root, key_path=key_path)
    elif plan.state is StartProjectState.UNCONFIGURED:
        if plan.setup_plan is None:
            raise ValueError("unconfigured start plan is missing setup plan")
        result = apply_setup_plan(root, plan.setup_plan, key_path=key_path)
    else:
        raise ValueError(f"unsupported start state: {plan.state}")

    return StartBootstrapResult(protect_result=result, bootstrap_performed=True)
```

Do not start watch here; CLI owns bootstrap rendering/confirmation and the transition to foreground watch.

- [ ] **Step 4: Pin PASS/FAIL/INCOMPLETE as returned data, not hidden policy**

The helper must return existing `ProjectProtectResult` unchanged. It must not fabricate PASS or decide whether watch should start; CLI will require both `status is PASS` and `lock_created`.

Tests must assert FAIL/INCOMPLETE values are preserved.

- [ ] **Step 5: Run Task 1+2 suites and static gates**

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/qualock/project_start tests/unit/test_project_start_flow.py
git commit -m "feat: orchestrate safe session bootstrap"
```

Reviewer gate: no unlocked plan can apply after the guard sees a lock entry; no LOCKED plan can call setup/protect.

---

### Task 3: `qualock start` CLI lifecycle

**Files:**
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_project_start_cli.py`

**Interfaces:**
- Consumes: `prepare_start`, `apply_start_bootstrap`, existing `render_setup_plan`, `render_protect_terminal`, `run_project_watch`, `_print_watch_event`.
- Produces CLI command:
  - `qualock start [--level minimal|recommended|strong] [--yes|-y]`
- Does not expose polling/debounce flags.

- [ ] **Step 1: Write RED CLI tests for all three states**

Use `typer.testing.CliRunner` and monkeypatch orchestration/watch functions.

LOCKED:
- no confirmation;
- no setup/protect;
- directly calls watch;
- PASS/FAIL/INCOMPLETE outcomes map `0/2/4`.

```python
def test_start_locked_enters_watch_without_confirmation(monkeypatch):
    monkeypatch.setattr("qualock.cli.prepare_start", lambda root, level: locked_plan())
    monkeypatch.setattr("qualock.cli.run_project_watch", lambda root, on_event: watch_outcome(PASS))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    assert "Protect the current state" not in result.stdout
```

Integrity error from watch must map to exit `4` and prove `apply_start_bootstrap` was never called.

CONFIGURED_UNLOCKED:
- renders exact manual protection names literal-safe;
- asks `"Protect this state and start watching?"`;
- cancellation returns `0`, no bootstrap/watch;
- `--yes` skips prompt only;
- PASS + `lock_created=True` renders protect result then runs normal watch;
- FAIL/INCOMPLETE or `lock_created=False` exits `4`, no watch.

UNCONFIGURED:
- renders existing setup plan;
- `NEEDS_SETUP` exits `4` before prompt/apply/watch;
- READY asks confirmation unless `--yes`;
- apply PASS + lock enters normal watch;
- stale-plan `StartStateChangedError` exits `4`, no watch.

- [ ] **Step 2: Write authoritative transition test after bootstrap**

Prove the initial watch verify remains separate from bootstrap:

- mocked bootstrap returns PASS;
- mocked `run_project_watch` emits a RESULT with FAIL;
- CLI renders the watch FAIL result and exits according to watch outcome, showing bootstrap PASS did not become the watch authoritative result.

At unit level it is enough to prove `run_project_watch` is always invoked after PASS bootstrap and its events/outcome control terminal/exit state.

- [ ] **Step 3: Write fail-closed exception mapping tests**

Pin:
- `ConfigError`, `ProjectProtectionConfigError`, invalid input -> `3`;
- `SetupUnsupportedError` -> `3`;
- `SetupReadinessError`, `ProjectLockIntegrityError`, `StartStateChangedError`, `WatchControlChangedError` -> `4`;
- `ProjectWatchSnapshotError`, `ProjectProtectionError`, unexpected operational `OSError` -> `1`;
- watch missing-lock on a pre-classified LOCKED state preserves existing watch missing-lock invalid-input meaning rather than invoking bootstrap fallback.

Use current CLI's exact exception classes; do not catch broad `Exception`.

- [ ] **Step 4: Implement CLI command with phase ordering**

Pseudo-structure:

```python
@app.command("start")
def start_command(
    level: Annotated[ProtectionLevel, typer.Option("--level", help="Protection level: minimal, recommended, or strong.")] = ProtectionLevel.RECOMMENDED,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip only the bootstrap confirmation.")] = False,
) -> None:
    root = Path.cwd()
    console.print("QuaLock\\n", end="", markup=False)

    plan = prepare_start(root, level)

    if plan.state is StartProjectState.LOCKED:
        outcome = run_project_watch(root, on_event=_print_watch_event)
        _exit_for_watch_outcome(outcome)
        return

    if plan.state is StartProjectState.UNCONFIGURED:
        assert plan.setup_plan is not None
        console.print(render_setup_plan(plan.setup_plan), end="", markup=False)
        if plan.setup_plan.readiness.status is ReadinessStatus.NEEDS_SETUP:
            raise typer.Exit(4)
    else:
        for protection in plan.configured_protections:
            console.print(f"- {protection.name}", markup=False)

    if not yes and not typer.confirm("Protect the current state and start watching?"):
        console.print("Setup cancelled. No files changed.", markup=False)
        return

    bootstrap = apply_start_bootstrap(root, plan)
    assert bootstrap.protect_result is not None
    result = bootstrap.protect_result
    evidence_path = f".qualock/results/{result.operation_id}/"
    console.print(render_protect_terminal(result, evidence_path), end="", markup=False)
    if result.status is not ProtectionStatus.PASS or not result.lock_created:
        raise typer.Exit(4)
    outcome = run_project_watch(root, on_event=_print_watch_event)
    _exit_for_watch_outcome(outcome)
```

Do not literally duplicate all exception mapping if a tiny private CLI helper can preserve existing `watch` behavior. A helper extraction is allowed only if `watch_command` tests prove its outputs/exits remain unchanged.

- [ ] **Step 5: Extract only behavior-preserving CLI helpers if needed**

Likely helpers:
- `_run_watch_cli(root: Path) -> None` or `_exit_for_watch_outcome(outcome: WatchOutcome) -> None`;
- `_render_protect_result(result)`.

If extracting, rerun existing `test_project_watch_cli.py`, `test_project_setup_flow.py`, `test_project_protection_flow.py` immediately.

- [ ] **Step 6: Run CLI-focused GREEN gate**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_project_start_cli.py \
  tests/unit/test_project_start_state.py \
  tests/unit/test_project_start_flow.py \
  tests/unit/test_project_watch_cli.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_protection_flow.py
```

Expected: all pass.

Run ruff/mypy on new package/tests and compare `cli.py` lint findings against `origin/main`; no new lint debt.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/qualock/cli.py tests/unit/test_project_start_cli.py
git commit -m "feat: add qualock start safe session"
```

Reviewer gate: existing locked control never falls back to bootstrap; cancellation/readiness failure do not mutate; PASS bootstrap always transitions through normal watch startup.

---

### Task 4: README and preservation gates

**Files:**
- Modify: `README.md`
- Test existing subsystem suites; no new engine implementation.

**Interfaces:**
- Documentation presents `qualock start` as the simplest foreground safe-session entrypoint.
- Existing `setup`, `protect`, `verify`, `watch` remain documented as explicit/manual controls.

- [ ] **Step 1: Update README onboarding**

At the start of project-protection section, add:

```bash
qualock start
```

Explain in compact language:

```text
existing signed baseline -> watch
existing manual protections without a lock -> confirm, protect, then watch
fresh supported project -> detect/readiness, confirm, protect, then watch
```

Document `--level` and `--yes`, including that `--yes` does not install dependencies or bypass failing checks/integrity.

Explicitly state:
- corrupt/existing lock never auto-repairs or re-baselines;
- manual protections are not replaced by packs;
- a fresh initial watch verification always runs after bootstrap;
- Ctrl+C watch exit semantics stay `0/2/4`.

Retain the detailed `setup`, manual protect/verify, and watch sections below.

- [ ] **Step 2: Run focused preservation suite**

At minimum:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_project_start_state.py \
  tests/unit/test_project_start_flow.py \
  tests/unit/test_project_start_cli.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_packs.py \
  tests/unit/test_project_setup_readiness.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_protection_signing.py \
  tests/unit/test_project_watch_snapshot.py \
  tests/unit/test_project_watch_control.py \
  tests/unit/test_project_watch_engine.py \
  tests/unit/test_project_watch_cli.py
```

If a listed historical filename differs on final main, use the current equivalent test file and record the exact suite in the commit/review notes.

- [ ] **Step 3: Scope audit**

Require zero unexpected functional diff under:
- `src/qualock/qualification/`
- `src/qualock/baseline/`
- `src/qualock/run/`
- evidence/grader/canary execution modules.

Allow only the already-approved orchestration package, CLI wiring, README, tests, and any behavior-preserving project-lock parser helper already present from prior batches.

Run compileall and `git diff --check`.

- [ ] **Step 4: Commit Task 4**

```bash
git add README.md
git commit -m "docs: add one-command safe session"
```

Reviewer gate: README promises exactly the implemented lifecycle and does not imply background operation, dependency installation, auto-repair, or skipped verification.

---

### Task 5: Final verification, independent review, PR, CI, merge

**Files:**
- No planned feature code changes unless a reviewer finds a concrete issue.
- Update tests/docs only through TDD/review findings with explicit commits.

- [ ] **Step 1: Run fresh full repository suite on exact HEAD**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
```

Record exact HEAD and count. Do not rely on a pre-commit run.

- [ ] **Step 2: Run static/final local gates**

Require:
- ruff clean for `src/qualock/project_start` and new start tests;
- mypy `--strict` clean for `src/qualock/project_start`;
- no new `cli.py` lint debt compared with `origin/main`;
- `python -m compileall -q src`;
- `git diff --check origin/main...HEAD`;
- clean tracked working tree;
- scope audit from Task 4.

- [ ] **Step 3: Independent reviewer coverage**

Review exact final diff in bounded groups if necessary:
1. `project_start` production;
2. CLI integration;
3. new start tests;
4. README/spec/plan consistency.

No merge while any reviewer has unresolved Critical/Important/P1/P2 finding. For disputed findings, provide the approved contract and ask the reviewer to trace the actual code path; do not dismiss findings merely because tests pass.

- [ ] **Step 4: Fix findings with TDD and re-run full suite**

Every accepted behavior bug gets a RED test before implementation. Every accepted coverage/docs gap gets a focused test/docs change and reviewer re-check.

- [ ] **Step 5: Push exact final head and open/update PR #25**

PR summary must include:
- `qualock start` state machine;
- no auto-repair/re-baseline of existing lock;
- manual protection preservation;
- no-lock re-check before bootstrap;
- normal watch initial verification after bootstrap;
- final local test/static/reviewer evidence.

- [ ] **Step 6: Exact-head PR CI gate**

Require CI success for Python 3.11, 3.12, 3.13, including existing Docker tmpfs smoke where configured. Verify PR head SHA still equals reviewed/tested head immediately before merge.

- [ ] **Step 7: Squash merge with expected head SHA**

Use squash merge and expected head SHA. Do not tag, release, or publish PyPI in this batch.

- [ ] **Step 8: Post-merge main verification**

Confirm GitHub `main` equals returned merge SHA. Require push-CI success on that exact merge SHA for 3.11/3.12/3.13. Run a fresh local main suite if worker capacity permits; if capacity blocks before execution, report it distinctly from a test failure and use exact-sha post-merge CI as the authoritative execution gate.

## Definition of done

PR #25 is complete only when:

- `qualock start` safely classifies `LOCKED`, `CONFIGURED_UNLOCKED`, or `UNCONFIGURED`;
- existing/corrupt lock entries cannot downgrade to fresh setup;
- malformed config cannot be overwritten through fallback;
- manual protections are preserved;
- unlocked bootstrap re-checks lock absence immediately before mutation;
- cancellation/readiness failure are mutation-free;
- bootstrap requires PASS + signed lock;
- normal watch initial verification runs after bootstrap;
- `--yes` skips confirmation only;
- existing setup/protect/verify/watch behavior is preserved;
- full local exact-head tests/static gates pass;
- independent final review has no unresolved high-priority findings;
- exact-head PR CI and post-merge main CI pass.
