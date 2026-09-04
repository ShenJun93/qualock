# Budgeted Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in attempt-count budget to local `qualock check` so users can cap model invocations without weakening QuaLock's fail-closed qualification semantics.

**Architecture:** `QualificationExecutor` owns deterministic whole-canary budget planning because it already owns paired scheduling and backend invocation. `execute_check()` exposes the budget as an optional keyword-only API, while the CLI adds `--max-attempts` only to local checks and preserves the old unlimited call shape when the flag is omitted. Skipped canaries are materialized as explicit `INCOMPLETE` executions/comparisons, so incomplete evidence can never become PASS/WARN/BLOCK.

**Tech Stack:** Python 3.12+, Typer, dataclasses, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-04-budgeted-qualification-design.md`

## Global Constraints

- `--max-attempts` is opt-in and applies only to local `qualock check`.
- A supplied budget must be an integer greater than zero.
- One complete canary costs `repetitions * 2` model attempts: baseline plus candidate.
- Never start a canary unless its complete paired/interleaved schedule fits in the remaining budget.
- Never call `prepare()` or `run_attempt()` for a budget-skipped canary.
- Execution is constrained only when `max_attempts < len(suite) * repetitions * 2`.
- Unconstrained execution (`None`, equal-to-full, or above-full budget) preserves the existing suite order and schedule.
- Constrained execution uses stable critical-first priority, preserving original order within critical and non-critical classes.
- Returned `QualificationResult.executions` and suite comparisons remain in original suite order.
- `QualificationResult.run_order` records only actual model attempts and therefore reflects critical-first order when constrained.
- A skipped canary has no attempts, zero success/valid counters, `Verdict.INCOMPLETE`, `baseline_stable=False`, and a deterministic budget reason containing `max_attempts` and the full-canary attempt requirement.
- A skipped canary uses `prepared_image_digest=""` because preparation is intentionally not performed.
- Any skipped configured canary forces the final suite verdict to `INCOMPLETE` through the unchanged policy precedence `INCOMPLETE > BLOCK > WARN > PASS`.
- No new fields are added to `QualificationResult`, `CanaryExecution`, `AttemptResult`, config, baseline lock, or canary schemas.
- Baseline creation, release monitor, version bisect, scheduler, GitHub PR qualification, and existing callers remain unlimited.
- No dollar-denominated or token-denominated budget logic is introduced.
- No changes to agent adapters, resolvers, Docker, graders, sandboxing, credentials, or qualification policy.
- Unlimited deterministic outputs must remain byte-identical to base `e60b5d113e0a56fc88e850c6d54234d6cd96c3b3`.

## File Structure

- Create `tests/unit/test_budgeted_qualification.py`: focused executor budget planning, whole-canary execution, skip semantics, ordering, and verdict-precedence tests.
- Modify `src/qualock/run/executor.py`: optional budget parameter, stable constrained priority, skipped execution/comparison construction, and original-order restoration.
- Modify `tests/unit/test_commands.py`: library-facing `execute_check()` budget forwarding and invalid-budget validation tests.
- Modify `src/qualock/commands.py`: keyword-only `max_attempts`, fail-fast validation, and forwarding into the executor.
- Modify `tests/unit/test_cli.py`: CLI forwarding, invalid values, default two-argument compatibility, and incomplete exit-code tests.
- Modify `src/qualock/cli.py`: `--max-attempts`, validation through the existing user-error path, and old arity preservation when omitted.
- Modify `README.md`: low-tech attempt-budget usage and explicit warning that budget-limited output is intentionally incomplete.
- Modify `ROADMAP.md`: mark attempt-budget cost control delivered without expanding into historical ranking or provider pricing.
- No production report/storage file changes are planned; existing renderers should consume explicit skipped `CanaryExecution` values unchanged.

---

### Task 1: Lock Whole-Canary Budget Semantics in the Executor

**Files:**
- Create: `tests/unit/test_budgeted_qualification.py`
- Modify: `src/qualock/run/executor.py`

**Interfaces:**
- Consumes: existing `QualificationBackend`, `paired_schedule()`, `qualify_canary()`, `qualify_suite()`, `CanaryComparison`, `CanaryExecution`, `CanaryAggregate`, and `Verdict`.
- Produces: `QualificationExecutor.run(..., qualification_id: str, max_attempts: int | None = None) -> QualificationResult`.
- Produces: deterministic budget-skip reason format: `INCOMPLETE: skipped by attempt budget (max_attempts=<N>, complete_canary_attempts=<M>)`.
- Produces: skipped `CanaryExecution.prepared_image_digest == ""`.

- [ ] **Step 1: Create focused test helpers and the unconstrained compatibility tests**

Create `tests/unit/test_budgeted_qualification.py` with explicit helpers rather than reusing integration-test internals:

```python
from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.executor import QualificationExecutor
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side, paired_schedule


class RecordingBackend:
    def __init__(self, *, failing_candidates: set[str] | None = None) -> None:
        self.failing_candidates = failing_candidates or set()
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedImage:
        self.prepared.append(canary.id)
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedImage,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        success = side is Side.BASELINE or canary.id not in self.failing_candidates
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=True,
            duration_ms=10,
            usage=Usage(input_tokens=1, output_tokens=1),
        )


def make_canary(tmp_path: Path, canary_id: str, *, critical: bool) -> CanarySpec:
    patch = tmp_path / f"{canary_id}.patch"
    patch.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": canary_id,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(patch), "command": ["pytest -q"]},
            "constraints": {"protected_paths": []},
            "critical": critical,
        }
    )


def binaries() -> tuple[AgentBinary, AgentBinary]:
    return (
        AgentBinary("codex", "0.150.0", Path("/baseline"), "sha-baseline"),
        AgentBinary("codex", "0.151.0", Path("/candidate"), "sha-candidate"),
    )


@pytest.mark.parametrize("max_attempts", [None, 18, 19])
def test_unconstrained_budget_preserves_existing_order(
    tmp_path: Path, max_attempts: int | None
) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-fixed",
        max_attempts=max_attempts,
    )

    expected_calls = [
        (canary.id, slot.side.value, slot.repetition)
        for canary in suite
        for slot in paired_schedule(canary.id, 3, "q-fixed")
    ]
    assert backend.calls == expected_calls
    assert [item.canary_id for item in result.executions] == [item.id for item in suite]
    assert result.run_order == tuple(expected_calls)
```

- [ ] **Step 2: Run the new unconstrained tests and verify RED**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_budgeted_qualification.py::test_unconstrained_budget_preserves_existing_order -q
```

Expected: FAIL because `QualificationExecutor.run()` does not accept `max_attempts`.

- [ ] **Step 3: Add constrained-order, no-partial-canary, zero-run, and verdict-precedence tests**

Append tests that cover the approved spec as one coherent executor contract:

```python
def test_constrained_budget_runs_critical_first_but_returns_original_order(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-budget",
        max_attempts=6,
    )

    expected_critical_calls = [
        ("critical", slot.side.value, slot.repetition)
        for slot in paired_schedule("critical", 3, "q-budget")
    ]
    assert backend.prepared == ["critical"]
    assert backend.calls == expected_critical_calls
    assert result.run_order == tuple(expected_critical_calls)
    assert [item.canary_id for item in result.executions] == [
        "normal-a",
        "critical",
        "normal-b",
    ]
    assert result.verdict is Verdict.INCOMPLETE

    first, critical, last = result.executions
    for skipped in (first, last):
        assert skipped.attempts == ()
        assert skipped.prepared_image_digest == ""
        assert skipped.baseline_successes == 0
        assert skipped.baseline_valid == 0
        assert skipped.candidate_successes == 0
        assert skipped.candidate_valid == 0
        assert skipped.verdict is Verdict.INCOMPLETE
        assert skipped.reason == (
            "INCOMPLETE: skipped by attempt budget "
            "(max_attempts=6, complete_canary_attempts=6)"
        )
    assert critical.verdict is Verdict.PASS


def test_budget_never_starts_a_partial_canary(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-seven",
        max_attempts=7,
    )

    assert backend.prepared == ["critical"]
    assert len(backend.calls) == 6
    assert result.executions[1].attempts == ()
    assert result.verdict is Verdict.INCOMPLETE


def test_budget_smaller_than_one_canary_runs_nothing(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-small",
        max_attempts=5,
    )

    assert backend.prepared == []
    assert backend.calls == []
    assert result.run_order == ()
    assert all(item.verdict is Verdict.INCOMPLETE for item in result.executions)
    assert result.verdict is Verdict.INCOMPLETE


def test_observed_critical_block_plus_skipped_canary_is_still_incomplete(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend(failing_candidates={"critical"})
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-block",
        max_attempts=6,
    )

    assert result.executions[0].verdict is Verdict.BLOCK
    assert result.executions[1].verdict is Verdict.INCOMPLETE
    assert result.verdict is Verdict.INCOMPLETE
```

Add the stable-partition case explicitly:

```python
def test_constrained_priority_is_stable_with_multiple_critical_canaries(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical-a", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
        make_canary(tmp_path, "critical-b", critical=True),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-stable-priority",
        max_attempts=12,
    )

    assert backend.prepared == ["critical-a", "critical-b"]
    assert [item.canary_id for item in result.executions] == [
        "normal-a",
        "critical-a",
        "normal-b",
        "critical-b",
    ]
    assert result.executions[0].verdict is Verdict.INCOMPLETE
    assert result.executions[2].verdict is Verdict.INCOMPLETE
    assert result.verdict is Verdict.INCOMPLETE
```

- [ ] **Step 4: Run the full focused executor file and verify RED**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_budgeted_qualification.py -q
```

Expected: FAIL until budget semantics exist.

- [ ] **Step 5: Implement the optional budget in `QualificationExecutor.run()`**

Modify the signature to:

```python
def run(
    self,
    baseline_binary: AgentBinary,
    candidate_binary: AgentBinary,
    suite: Sequence[CanarySpec],
    *,
    qualification_id: str,
    max_attempts: int | None = None,
) -> QualificationResult:
```

Validate defensively for direct library callers:

```python
if max_attempts is not None and max_attempts < 1:
    raise ValueError("max_attempts must be greater than zero")
```

Use **suite indexes**, not canary IDs, for result restoration so the executor does not introduce a new duplicate-ID assumption on direct callers:

```python
indexed_suite = tuple(enumerate(suite))
attempts_per_canary = self.repetitions * 2
full_suite_attempts = len(indexed_suite) * attempts_per_canary
constrained = max_attempts is not None and max_attempts < full_suite_attempts

if constrained:
    execution_order = tuple(
        pair for pair in indexed_suite if pair[1].critical
    ) + tuple(pair for pair in indexed_suite if not pair[1].critical)
    remaining_attempts = max_attempts
else:
    execution_order = indexed_suite
    remaining_attempts = None
```

Store selected/skipped values by original index:

```python
executions_by_index: dict[int, CanaryExecution] = {}
comparisons_by_index: dict[int, CanaryComparison] = {}
run_order: list[tuple[str, str, int]] = []
```

Before calling `prepare()` for each constrained canary, check the whole-canary budget. When it does not fit, build explicit skipped values and continue without backend calls.

Extend the existing qualification-model import in `executor.py` to include `CanaryComparison` and `Verdict`; no new module is required.

Create a small private helper in `executor.py` with a precise return type:

```python
def _budget_skipped_canary(
    canary: CanarySpec,
    *,
    repetitions: int,
    max_attempts: int,
    attempts_per_canary: int,
) -> tuple[CanaryComparison, CanaryExecution]:
    aggregate = CanaryAggregate(
        valid_runs=0,
        successes=0,
        expected_runs=repetitions,
    )
    reason = (
        "INCOMPLETE: skipped by attempt budget "
        f"(max_attempts={max_attempts}, "
        f"complete_canary_attempts={attempts_per_canary})"
    )
    comparison = CanaryComparison(
        canary_id=canary.id,
        baseline=aggregate,
        candidate=aggregate,
        critical=canary.critical,
        verdict=Verdict.INCOMPLETE,
        reason=reason,
        baseline_stable=False,
    )
    execution = CanaryExecution(
        canary_id=canary.id,
        critical=canary.critical,
        prepared_image_digest="",
        attempts=(),
        baseline_successes=0,
        candidate_successes=0,
        baseline_valid=0,
        candidate_valid=0,
        verdict=Verdict.INCOMPLETE,
        reason=reason,
    )
    return comparison, execution
```

For selected canaries, keep the current prepare/schedule/run/aggregate code semantically unchanged. After a complete selected canary finishes, decrement `remaining_attempts` by exactly `attempts_per_canary`.

Restore original order before suite policy/rendering:

```python
comparisons = tuple(comparisons_by_index[index] for index, _ in indexed_suite)
executions = tuple(executions_by_index[index] for index, _ in indexed_suite)
suite_verdict = qualify_suite(comparisons)
```

Return the existing `QualificationResult` fields only; do not add budget metadata fields.

- [ ] **Step 6: Run focused executor tests to GREEN**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_budgeted_qualification.py \
  tests/integration/test_fake_qualification.py \
  tests/unit/test_schedule.py -q
```

Expected: PASS, including the pre-existing paired/interleaved integration test.

- [ ] **Step 7: Run static checks for the executor task**

Run:

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/run/executor.py tests/unit/test_budgeted_qualification.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/run/executor.py
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/qualock/run/executor.py tests/unit/test_budgeted_qualification.py
git commit -m "feat: add whole-canary attempt budgets"
```

---

### Task 2: Wire Budgeting Through `execute_check()` and Persist Incomplete Evidence

**Files:**
- Modify: `tests/unit/test_commands.py`
- Modify: `src/qualock/commands.py`

**Interfaces:**
- Consumes: `QualificationExecutor.run(..., max_attempts=...)` from Task 1.
- Produces: `execute_check(root, candidate_spec, *, resolver=None, backend=None, qualification_id=None, max_attempts: int | None = None) -> QualificationResult`.
- Error contract: `max_attempts <= 0` raises `CommandError("max attempts must be greater than zero")` before resolver/model execution.

- [ ] **Step 1: Add command-level validation and forwarding tests**

Extend `tests/unit/test_commands.py` with a recording backend or minimally add call tracking to the existing `FakeBackend`:

```python
class FakeBackend:
    def __init__(self, success_versions: set[str] | None = None) -> None:
        self.success_versions = success_versions or {"0.150.0"}
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def prepare(self, canary, qualification_id: str) -> PreparedImage:
        self.prepared.append(canary.id)
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary, prepared, binary, side: Side, repetition: int) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=binary.version in self.success_versions,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=1),
        )
```

Add:

```python
def test_check_forwards_attempt_budget_and_writes_incomplete_report(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    baseline_backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=baseline_backend,
        qualification_id="baseline-budget",
        created_at="2026-09-05T00:00:00Z",
    )
    check_backend = FakeBackend()

    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=check_backend,
        qualification_id="check-budget",
        max_attempts=5,
    )

    assert check_backend.prepared == []
    assert check_backend.calls == []
    assert result.verdict is Verdict.INCOMPLETE
    payload = json.loads(
        (tmp_path / ".qualock/results/check-budget/report.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["verdict"] == "incomplete"
    assert payload["executions"][0]["attempts"] == []
    assert payload["executions"][0]["prepared_image_digest"] == ""
    assert "max_attempts=5" in payload["executions"][0]["reason"]


def test_check_rejects_nonpositive_attempt_budget_before_resolution(tmp_path: Path) -> None:
    resolver = FakeResolver()
    backend = FakeBackend()

    for bad in (0, -1):
        with pytest.raises(CommandError, match="max attempts must be greater than zero"):
            execute_check(
                tmp_path,
                "codex@0.151.0",
                resolver=resolver,
                backend=backend,
                max_attempts=bad,
            )

    assert resolver.calls == []
    assert backend.prepared == []
    assert backend.calls == []
```

Keep the existing no-budget `test_check_reruns_pinned_baseline_and_candidate_and_writes_report` unchanged as the default compatibility regression.

- [ ] **Step 2: Run the new command tests and verify RED**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_commands.py::test_check_forwards_attempt_budget_and_writes_incomplete_report \
  tests/unit/test_commands.py::test_check_rejects_nonpositive_attempt_budget_before_resolution -q
```

Expected: FAIL because `execute_check()` has no budget keyword/validation.

- [ ] **Step 3: Add the keyword-only budget to `execute_check()`**

Update the signature:

```python
def execute_check(
    root: Path,
    candidate_spec: str,
    *,
    resolver: Resolver | None = None,
    backend: QualificationBackend | None = None,
    qualification_id: str | None = None,
    max_attempts: int | None = None,
) -> QualificationResult:
```

Make the very first executable check fail fast before project loading/resolution:

```python
if max_attempts is not None and max_attempts <= 0:
    raise CommandError("max attempts must be greater than zero")
```

Forward the value only at the executor boundary:

```python
result = QualificationExecutor(
    backend=backend,
    repetitions=config.qualification.repetitions,
).run(
    baseline_binary,
    candidate_binary,
    canaries,
    qualification_id=qid,
    max_attempts=max_attempts,
)
```

Do not change monitor/bisect/GitHub PR call sites; their omission of the keyword is the unlimited compatibility contract.

- [ ] **Step 4: Run command + protected-caller regression tests**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_commands.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_version_bisect_commands.py \
  tests/unit/test_github_pr_commands.py -q
```

Expected: PASS.

- [ ] **Step 5: Run static checks for command wiring**

Run:

```bash
/tmp/qualock-static-22-final/bin/ruff check src/qualock/commands.py tests/unit/test_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/commands.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/qualock/commands.py tests/unit/test_commands.py
git commit -m "feat: expose check attempt budgets"
```

---

### Task 3: Add the Local `--max-attempts` CLI Without Changing Unlimited Arity

**Files:**
- Modify: `tests/unit/test_cli.py`
- Modify: `src/qualock/cli.py`

**Interfaces:**
- Consumes: `execute_check(..., max_attempts: int | None = None)` from Task 2.
- Produces: `qualock check AGENT@VERSION --max-attempts N`.
- Compatibility rule: when the flag is omitted, `check_command()` must still call `execute_check(root, candidate)` with exactly two positional arguments and no `max_attempts=None` keyword.
- Error rule: zero/negative values use existing user-error exit code 3 and do not call `execute_check()`.

- [ ] **Step 1: Add CLI forwarding and validation tests**

Append to `tests/unit/test_cli.py`:

```python
def test_check_max_attempts_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, int] = {}

    def fake_execute_check(root: Path, candidate: str, *, max_attempts: int):
        captured["max_attempts"] = max_attempts
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", "6"],
    )

    assert captured == {"max_attempts": 6}
    assert result.exit_code == 2


def test_check_without_budget_preserves_two_argument_execute_check_call(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[tuple[Path, str]] = []

    def fake_execute_check(root: Path, candidate: str):
        called.append((root, candidate))
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 2
    assert called == [(tmp_path, "codex@0.151.0")]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_check_rejects_nonpositive_max_attempts_before_execution(
    tmp_path: Path, monkeypatch, bad: str
) -> None:
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_execute_check(*args, **kwargs):
        nonlocal called
        called = True
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", bad],
    )

    assert result.exit_code == 3
    assert "max attempts must be greater than zero" in result.stdout
    assert called is False
```

Import `pytest` if the file does not already import it.

Add a budget-limited incomplete exit-code test by reusing the existing `QualificationResult` construction pattern:

```python
def test_budget_limited_incomplete_keeps_exit_4(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = sample_result()
    incomplete = source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=Verdict.INCOMPLETE,
        executions=source.executions,
        reasons=("budget skipped a configured canary",),
        run_order=source.run_order,
    )
    monkeypatch.setattr(
        "qualock.cli.execute_check",
        lambda root, candidate, *, max_attempts: incomplete,
    )

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", "6"],
    )

    assert result.exit_code == 4
    assert "CHECK COULD NOT FINISH" in result.stdout
```

- [ ] **Step 2: Run the new CLI tests and verify RED**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_cli.py -k 'max_attempts or budget_limited' -q
```

Expected: FAIL because the option is not registered.

- [ ] **Step 3: Add the option and preserve the old unlimited call path**

Change `check_command()` to include:

```python
max_attempts: int | None = typer.Option(
    None,
    "--max-attempts",
    help=(
        "Cap model attempts for this local check. Skipped canaries make the "
        "result incomplete."
    ),
),
```

Inside the existing `try` block, validate before execution:

```python
if max_attempts is not None and max_attempts <= 0:
    raise CommandError("max attempts must be greater than zero")
```

Preserve old arity exactly when omitted:

```python
agent_name, _version = parse_agent_spec(candidate)
if max_attempts is None:
    result = execute_check(root, candidate)
else:
    result = execute_check(root, candidate, max_attempts=max_attempts)
```

Do not change verdict rendering or exit-code mapping. Budget-limited results naturally use the existing `Verdict.INCOMPLETE -> Exit(4)` path.

- [ ] **Step 4: Run CLI regression tests to GREEN**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_cli.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_version_bisect_cli.py \
  tests/unit/test_github_pr_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Run static checks for CLI wiring**

Run:

```bash
/tmp/qualock-static-22-final/bin/ruff check src/qualock/cli.py tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/cli.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/qualock/cli.py tests/unit/test_cli.py
git commit -m "feat: add local check attempt budget flag"
```

---

### Task 4: Document the Low-Tech Budget Contract and Lock Report Compatibility

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Test: existing `tests/unit/test_report.py`, `tests/unit/test_safety_report.py`, `tests/unit/test_budgeted_qualification.py`, and `tests/unit/test_commands.py`

**Interfaces:**
- Produces no new Python interface.
- User-facing rule: `--max-attempts` is a cost/time cap that may intentionally produce `INCOMPLETE`; it is not a cheaper PASS or BLOCK shortcut.

- [ ] **Step 1: Add one artifact-shape assertion for a skipped canary**

In the command-level budget test from Task 2, ensure `report.md`, `report.json`, and `qualification.json` all exist and that `qualification.json` retains the existing schema keys only:

```python
artifact_root = tmp_path / ".qualock/results/check-budget"
assert (artifact_root / "report.md").is_file()
assert (artifact_root / "report.json").is_file()
qualification = json.loads(
    (artifact_root / "qualification.json").read_text(encoding="utf-8")
)
assert set(qualification) == {
    "qualification_id",
    "baseline_version",
    "candidate_version",
    "run_order",
    "verdict",
}
assert qualification["run_order"] == []
assert qualification["verdict"] == "incomplete"
```

Run the focused test and keep it GREEN; no report/storage production changes should be necessary.

- [ ] **Step 2: Add README usage immediately after the normal `qualock check` example**

Add concise copy equivalent to:

````markdown
To cap model calls for a quick, explicitly incomplete check, use an attempt budget:

```bash
qualock check codex@0.151.0 --max-attempts 6
```

QuaLock only starts a canary when the remaining budget can run its complete baseline/candidate paired schedule. When the cap prevents any configured canary from running, that canary is reported as `INCOMPLETE`, so the overall result is also `INCOMPLETE`. A budgeted check never turns missing evidence into a cheaper PASS or BLOCK. Omit the flag for the full qualification used by release monitoring and automated workflows.
````

Keep examples agent-neutral in prose; the same flag works with a locally configured Claude Code project.

- [ ] **Step 3: Update `ROADMAP.md` without expanding Batch #32 scope**

Move or annotate the cost-control roadmap item as delivered with wording such as:

```markdown
- Attempt-budgeted local qualification with critical-first canary selection and fail-closed incomplete results.
```

Do not claim historical effectiveness ranking, token budgeting, monetary cost estimates, or persistent presets are delivered.

- [ ] **Step 4: Run report/docs regression gates**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest \
  tests/unit/test_budgeted_qualification.py \
  tests/unit/test_commands.py \
  tests/unit/test_report.py \
  tests/unit/test_safety_report.py -q
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add README.md ROADMAP.md tests/unit/test_commands.py
git commit -m "docs: explain budgeted qualification"
```

---

### Task 5: Prove Unlimited Compatibility and Complete Whole-Branch Verification

**Files:**
- No planned production changes.
- Temporary compatibility output may be written only under `/tmp` and must not be committed.

**Interfaces:**
- Verifies all Batch #32 contracts against base `e60b5d113e0a56fc88e850c6d54234d6cd96c3b3`.

- [ ] **Step 1: Run the complete test suite from the Batch #32 worktree**

Run:

```bash
cd /home/pacmap/qualock-budgeted-qualification
/home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
```

Expected: all tests PASS except the existing opt-in real-Claude contract test remains skipped by default.

- [ ] **Step 2: Run whole-branch static gates**

Run Ruff on every Python file changed from base:

```bash
changed_py=$(git diff --name-only e60b5d113e0a56fc88e850c6d54234d6cd96c3b3...HEAD -- '*.py')
/tmp/qualock-static-22-final/bin/ruff check $changed_py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check e60b5d113e0a56fc88e850c6d54234d6cd96c3b3...HEAD
```

Expected: PASS.

- [ ] **Step 3: Prove protected scope remained unchanged**

The following must show no diff from Batch #32 base:

```bash
git diff --exit-code e60b5d113e0a56fc88e850c6d54234d6cd96c3b3...HEAD -- \
  src/qualock/qualification/policy.py \
  src/qualock/config \
  src/qualock/canary \
  src/qualock/baseline \
  src/qualock/agents \
  src/qualock/run/backend.py \
  src/qualock/run/docker.py \
  src/qualock/release_monitor \
  src/qualock/version_bisect \
  src/qualock/github_pr \
  src/qualock/scheduler \
  pyproject.toml
```

Expected: no output, exit 0.

- [ ] **Step 4: Prove unlimited artifact bytes against the exact base commit**

Create this temporary helper outside the repository:

```bash
cat > /tmp/qualock-b32-compat.py <<'PYCOMPAT'
from pathlib import Path
import shutil
import sys
import tempfile

from qualock.agents.base import AgentBinary
from qualock.commands import execute_baseline, execute_check
from qualock.config.io import write_default_config
from qualock.project import load_project
from qualock.qualification.models import AttemptResult, Usage
from qualock.report.render import render_safety_terminal, render_terminal
from qualock.report.safety import build_safety_summary
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side


class FakeResolver:
    def resolve(self, version: str) -> AgentBinary:
        return AgentBinary(
            "codex",
            version,
            Path(f"/fake/codex/{version}/codex"),
            f"sha-{version}",
        )


class FakeBackend:
    def prepare(self, canary, qualification_id: str) -> PreparedImage:
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(
        self,
        *,
        canary,
        prepared,
        binary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=binary.version == "0.150.0",
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=2),
        )


def make_project(root: Path) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    write_default_config(ub / "config.yaml")
    patch = ub / "canaries" / "grader.patch"
    patch.write_text("patch", encoding="utf-8")
    (ub / "canaries" / "sample.yaml").write_text(
        """schema_version: 1
id: critical-bug
name: Critical bug
repository:
  url: https://example.invalid/repo.git
  base_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
runtime:
  image: python:3.12-slim
task: Fix it.
setup: []
agent:
  timeout_seconds: 60
grader:
  patch: grader.patch
  command:
    - pytest -q
constraints:
  protected_paths: []
critical: true
""",
        encoding="utf-8",
    )


def main(output_arg: str) -> None:
    output = Path(output_arg)
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="qualock-b32-project-") as temporary:
        project = Path(temporary)
        make_project(project)
        resolver = FakeResolver()
        execute_baseline(
            project,
            "codex@0.150.0",
            resolver=resolver,
            backend=FakeBackend(),
            qualification_id="baseline-q",
            created_at="2026-09-05T00:00:00+00:00",
        )
        result = execute_check(
            project,
            "codex@0.151.0",
            resolver=resolver,
            backend=FakeBackend(),
            qualification_id="check-q",
        )
        _config, canaries = load_project(project)
        display_names = {canary.id: canary.name for canary in canaries}
        summary = build_safety_summary(
            result,
            display_names,
            agent_display_name="Codex",
        )
        (output / "technical.txt").write_text(
            render_terminal(result, agent_display_name="Codex"),
            encoding="utf-8",
        )
        (output / "safety.txt").write_text(
            render_safety_terminal(summary, ".qualock/results/check-q/"),
            encoding="utf-8",
        )
        artifacts = project / ".qualock" / "results" / "check-q"
        for name in ("report.md", "report.json", "qualification.json"):
            shutil.copyfile(artifacts / name, output / name)


if __name__ == "__main__":
    main(sys.argv[1])
PYCOMPAT
```

Run the helper in separate interpreters so each imports QuaLock from the intended worktree:

```bash
set -euo pipefail
BASE=e60b5d113e0a56fc88e850c6d54234d6cd96c3b3
BASE_WT=$(mktemp -d /tmp/qualock-b32-base-XXXXXX)
rmdir "$BASE_WT"
git worktree add --detach "$BASE_WT" "$BASE"
cleanup() { git worktree remove --force "$BASE_WT" >/dev/null 2>&1 || true; }
trap cleanup EXIT
rm -rf /tmp/qualock-b32-base-out /tmp/qualock-b32-head-out

PYTHONPATH="$BASE_WT/src" \
  /home/pacmap/qualock-exp/.venv/bin/python \
  /tmp/qualock-b32-compat.py /tmp/qualock-b32-base-out
PYTHONPATH="/home/pacmap/qualock-budgeted-qualification/src" \
  /home/pacmap/qualock-exp/.venv/bin/python \
  /tmp/qualock-b32-compat.py /tmp/qualock-b32-head-out

for file in technical.txt safety.txt report.md report.json qualification.json; do
    cmp -s "/tmp/qualock-b32-base-out/$file" "/tmp/qualock-b32-head-out/$file"
    printf '%s BYTE_IDENTICAL\n' "$file"
done
```

Expected output:

```text
technical.txt BYTE_IDENTICAL
safety.txt BYTE_IDENTICAL
report.md BYTE_IDENTICAL
report.json BYTE_IDENTICAL
qualification.json BYTE_IDENTICAL
```

The helper is evidence tooling only and must remain under `/tmp`, never committed.

- [ ] **Step 5: Run explicit constrained acceptance with no real model calls**

Reuse the executable tests from Task 1 instead of inventing a second acceptance harness:

```bash
/home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_budgeted_qualification.py::test_constrained_budget_runs_critical_first_but_returns_original_order \
  tests/unit/test_budgeted_qualification.py::test_budget_never_starts_a_partial_canary \
  tests/unit/test_budgeted_qualification.py::test_budget_smaller_than_one_canary_runs_nothing \
  tests/unit/test_budgeted_qualification.py::test_observed_critical_block_plus_skipped_canary_is_still_incomplete
```

Expected: PASS. These tests prove 6-attempt constrained execution, no partial second canary, zero execution below one-canary cost, original returned order, and `INCOMPLETE` precedence over an observed critical BLOCK.

- [ ] **Step 6: Self-review the exact whole-branch diff**

Check specifically for:

- accidental changes to unlimited ordering;
- decrement-before/after bugs that could exceed the cap;
- use of canary ID as a restoration key instead of original suite index;
- any `prepare()` call on a skipped canary;
- any report/schema field added solely for budget metadata;
- any budget keyword added to monitor/bisect/GitHub PR/scheduler callers;
- any partial-canary stop path;
- any new provider-pricing or token-budget logic.

Fix any issue with a focused RED test first, then rerun relevant gates.

- [ ] **Step 7: Run independent exact-head whole-branch review**

Prepare a review package containing:

- approved spec;
- this implementation plan;
- exact base/head SHAs;
- full diff;
- full test/static/scope results;
- 5/5 unlimited byte-compatibility proof;
- explicit constrained fake acceptance evidence.

Ask the reviewer to classify findings as Critical / Important / Minor and require Critical=0 and Important=0 before calling Batch #32 local-complete.

- [ ] **Step 8: Final local state check**

Run:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git rev-list --count e60b5d113e0a56fc88e850c6d54234d6cd96c3b3..HEAD
```

Expected: clean worktree on `feat/budgeted-qualification`, with no push/PR/tag/release action performed.

