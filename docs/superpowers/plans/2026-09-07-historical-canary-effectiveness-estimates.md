# Historical Canary Effectiveness + Runtime/Token Estimates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `qualock history` command that learns advisory canary-effectiveness and model-attempt runtime/token estimates from existing local `report.json` artifacts without changing qualification policy or execution behavior.

**Architecture:** Add a focused `qualock.history` package with frozen data models, a field-tolerant read-only loader, pure analysis, and deterministic low-tech rendering. `commands.execute_history` loads the current configured canary universe and composes those pure layers; `cli.history_command` owns exit-code mapping and stdout only. No database, index, artifact rewrite, network access, pricing, or executor/policy change is allowed.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, `pathlib`, `json`, `statistics.median`, existing Typer/Rich CLI, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-07-historical-canary-effectiveness-estimates-design.md`

## Global Constraints

- Exact implementation base: `f6410c610642b4e271a9932172326006f37cfaa6`; canonical spec commit: `24c0de3`.
- History source of truth is only `.qualock/results/*/report.json`; never read `qualification.json` or `report.md` for analytics.
- Loader is zero-write: no create/modify/rename/delete under `.qualock/results/` or elsewhere.
- Missing `results_dir` is normal and returns an empty `HistorySummary`; an empty current configured canary suite is instead `CommandError("no canaries found")` / CLI exit 3.
- Qualification directories are scanned by ascending directory name; first successfully loaded duplicate `qualification_id` wins, later duplicates are ignored with fixed reason `duplicate qualification_id`.
- Shared pairing identity uses only normalized `side` + `repetition`; effectiveness, runtime, and token field gates are independent exactly as Invariant P3 states.
- Token samples use only `input_tokens + output_tokens`; never add cache-read, cache-write, or reasoning subsets.
- Current configured canary IDs are the actionable universe; obsolete historical IDs never appear in ranking, estimates, or missing lists.
- Historical analytics are advisory only. Do not change `qualification/policy.py`, `run/executor.py`, max-attempt/token admission, baseline, monitor, bisect, GitHub PR qualification, scheduler, canary schema, config schema, or `pyproject.toml`.
- No network/provider calls and no monetary pricing/cost estimation; #42 remains deferred outside pass/fail policy.
- Strict mypy may report only the exact three pre-existing PyYAML `import-untyped` errors from the base; any additional error blocks completion.
- Ruff gate is changed Batch #41 Python files clean plus full-tree diagnostics introducing zero findings beyond exact base; do not fix unrelated base lint in this batch.
- Windows CI must run the path-neutral scanning and full CLI tests green.
- Model budget for SDD: Claude Sonnet medium for Tasks 1/3/4/5 and their bounded reviews; Sonnet high for Task 2 and pre-PR whole-implementation review; Opus high is reserved for the final docs-inclusive whole-branch review only. If Claude is hard-limited, use Codex GPT-5.6 Sol at the nearest effort level and record the substitution in the ledger; never run duplicate reviewers in parallel.

---
## File Structure

- Create `src/qualock/history/__init__.py`: re-export the small history surface used by `commands.py`.
- Create `src/qualock/history/models.py`: frozen normalized artifact/analysis dataclasses only; no I/O or policy logic.
- Create `src/qualock/history/loader.py`: deterministic read-only scan, JSON structural validation, field normalization, duplicate-ID handling.
- Create `src/qualock/history/analysis.py`: pure pairing/effectiveness/runtime/token aggregation, ranking, medians, current-suite filtering.
- Create `src/qualock/history/render.py`: deterministic low-tech text and display-only rounding.
- Modify `src/qualock/commands.py`: add only `execute_history(root: Path) -> HistoryAnalysis` composition.
- Modify `src/qualock/cli.py`: add argument-free `history` command and exit-code/stdout mapping.
- Create `tests/unit/test_history_loader.py`, `tests/unit/test_history_analysis.py`, `tests/unit/test_history_render.py`.
- Modify `tests/unit/test_commands.py` and `tests/unit/test_cli.py` for composition and end-to-end contracts.
- Modify `README.md` and `ROADMAP.md` only after implementation review + CI gates pass in Task 6.

### Task 1: Add Normalized History Models and the Read-Only Loader

**Files:**
- Create: `src/qualock/history/__init__.py`
- Create: `src/qualock/history/models.py`
- Create: `src/qualock/history/loader.py`
- Create: `tests/unit/test_history_loader.py`

**Interfaces:**
- Produces: `HistoricalAttempt`, `HistoricalExecution`, `LoadedReport`, `ReportLoadFailure`, `HistorySummary` exactly as Spec §5.2.
- Produces: `scan_results(results_dir: Path) -> HistorySummary`.
- Consumes: existing `report.json` shape only; no dependency on `QualificationResult` construction or policy strings.

- [ ] **Step 1: Write the loader RED tests and fixture helper**

Create a local JSON helper in `tests/unit/test_history_loader.py` that writes one qualification directory at a time without importing production serializers:

```python
def write_report(results: Path, directory: str, payload: object) -> Path:
    root = results / directory
    root.mkdir(parents=True)
    path = root / "report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def attempt(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "side": "baseline",
        "repetition": 1,
        "success": True,
        "valid": True,
        "duration_ms": 100,
        "usage": {"observed": True, "input_tokens": 10, "output_tokens": 5},
    }
    value.update(overrides)
    return value
```

Add named RED tests for: nonexistent results dir; empty dir; unreadable `report.json` (monkeypatch read-only I/O to raise `OSError`, rather than relying on platform permissions); invalid JSON/non-object/missing identity; malformed execution entry (`canary_id`/`attempts`) rejecting the whole report with the fixed category; mixed valid+malformed reports; raw contradictory `verdict`/`reason` keys ignored rather than normalized into history models; malformed `attempts[]` element normalized rather than report rejection; pre-#40 attempt with no `usage`; bool rejected as int for repetition/duration/token counters; deterministic/path-neutral directory scan using only `Path`/`Path.name`; duplicate `qualification_id` first-loaded-wins; and scan-only zero-write byte/mtime preservation.

- [ ] **Step 2: Run loader tests to verify RED**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py
```

Expected: collection/import failure because `qualock.history` does not exist yet; after adding only model imports, behavior tests still fail until `scan_results` is implemented.

- [ ] **Step 3: Implement the frozen models exactly**
In `src/qualock/history/models.py` define only normalized/pure data:

```python
@dataclass(frozen=True)
class HistoricalAttempt:
    side: str | None
    repetition: int | None
    success: bool | None
    valid: bool | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    usage_observed: bool = False


@dataclass(frozen=True)
class HistoricalExecution:
    canary_id: str
    attempts: tuple[HistoricalAttempt, ...]


@dataclass(frozen=True)
class LoadedReport:
    qualification_id: str
    qualification_dir: Path
    executions: tuple[HistoricalExecution, ...]


@dataclass(frozen=True)
class ReportLoadFailure:
    qualification_dir: Path
    reason: str
```
```python
@dataclass(frozen=True)
class HistorySummary:
    loaded: tuple[LoadedReport, ...]
    ignored: tuple[ReportLoadFailure, ...]
```

Export these plus `scan_results` from `history/__init__.py`; do not export loader-private normalization helpers.

- [ ] **Step 4: Implement deterministic field-tolerant `scan_results`**

Use fixed reasons only:

```python
_FAILURE_UNREADABLE = "unreadable file"
_FAILURE_INVALID_JSON = "invalid JSON"
_FAILURE_NOT_OBJECT = "not a JSON object"
_FAILURE_ID = "missing or invalid qualification_id"
_FAILURE_EXECUTIONS = "missing or invalid executions list"
_FAILURE_EXECUTION = "malformed execution entry"
_FAILURE_DUPLICATE = "duplicate qualification_id"
```

Implementation rules: return empty summary before any `iterdir()` if `results_dir.exists()` is false; iterate only child directories sorted by `path.name`; skip directories lacking `report.json`; never follow a report directory merely because some other artifact exists. Parse whole-report identity first, normalize every attempt field independently to `None`/`False`, explicitly reject `bool` before accepting integer fields, and track accepted qualification IDs only after structural validation. A later duplicate becomes `ReportLoadFailure` and is never appended to `loaded`.

- [ ] **Step 5: Run Task 1 GREEN and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/history
git diff --check
```

Expected: all Task 1 tests PASS; Ruff/compileall/diff-check PASS.
- [ ] **Step 6: Commit Task 1**

```bash
git add src/qualock/history/__init__.py src/qualock/history/models.py src/qualock/history/loader.py tests/unit/test_history_loader.py
git commit -m "feat: load local qualification history"
```

Reviewer must reject Task 1 if loader writes anything, leaks raw exception text, treats an attempt-level defect as a whole-report defect, accepts bool-as-int, or allows duplicate qualification IDs to double-count.

### Task 2: Implement Pure Effectiveness and Runtime/Token Analysis

**Files:**
- Modify: `src/qualock/history/models.py`
- Create: `src/qualock/history/analysis.py`
- Create: `tests/unit/test_history_analysis.py`
- Modify: `src/qualock/history/__init__.py`

**Interfaces:**
- Consumes: `HistorySummary` from Task 1 and `current_canary_ids: Sequence[str]` in config order.
- Produces: `CanaryEffectiveness`, `CanaryEstimate`, `SuiteEstimate`, `HistoryAnalysis` exactly as Spec §5.2.
- Produces: `analyze_history(summary: HistorySummary, current_canary_ids: Sequence[str]) -> HistoryAnalysis`.

- [ ] **Step 1: Write RED tests for pairing identity and metric independence**

Create helpers that construct normalized `HistoricalAttempt`/`HistoricalExecution` directly; do not route these unit tests back through JSON loader behavior.

```python
def hist_attempt(
    *, side: str = "baseline", repetition: int = 1,
    success: bool | None = True, valid: bool | None = True,
    duration_ms: int | None = 100,
    input_tokens: int | None = 10, output_tokens: int | None = 5,
    usage_observed: bool = True,
) -> HistoricalAttempt:
    return HistoricalAttempt(side, repetition, success, valid, duration_ms,
                             input_tokens, output_tokens, usage_observed)
```
Pin named cases for: empty/mismatched/duplicate pairing excluded from all metrics; all-success stable-baseline execution is an eligible non-detection; malformed `success`/`valid` loses effectiveness only; `valid=False` loses effectiveness but keeps runtime/token; malformed/negative duration loses runtime only; unobserved/missing token counters lose token only; candidate `success=False` with all `valid=True` is a detection and still runtime/token sample; unstable baseline loses effectiveness only; cache/reasoning absence from the normalized model cannot affect token totals.

- [ ] **Step 2: Write RED tests for ranking and current-suite filtering**

Use at least four current IDs in non-alphabetical config order and an obsolete historical ID. Assert:

```python
assert [item.canary_id for item in analysis.ranked] == ["critical-a", "workflow-z"]
assert [item.canary_id for item in analysis.not_enough_history] == ["workflow-b", "workflow-a"]
assert all(item.canary_id != "obsolete" for item in analysis.per_canary_estimates)
```

Pin the exact ranking threshold (`eligible_samples >= 3`) and four-key sort: detection rate desc, detections desc, eligible samples desc, canary ID asc. Prove policy-string independence structurally: the normalized history models contain no verdict/reason fields, and a loader fixture may include contradictory raw `verdict`/`reason` keys that are ignored. Pure analysis therefore has no field through which policy strings can influence classification.

- [ ] **Step 3: Write RED tests for medians and suite all-or-nothing estimates**

Assert odd and even `statistics.median` semantics without display rounding:

```python
assert estimate.runtime_median_ms == 1500.5
assert estimate.token_median == 125.5
```

Then pin independent suite availability: all current canaries with runtime samples + one missing token sample => runtime suite value present, token suite `None`, missing-token list in current config order; mirror the reverse case. Pin partial/budgeted historical runs: executed canaries contribute per-canary medians, `attempts=()` executions contribute nothing, and whole-suite estimates remain unavailable until every current canary has the metric.

- [ ] **Step 4: Run analysis tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_analysis.py
```

Expected: FAIL because analysis/result dataclasses and `analyze_history` are not implemented.
- [ ] **Step 5: Implement analysis result models and shared pairing helper**

Add the four frozen result dataclasses from Spec §5.2. In `analysis.py`, keep pairing identity as one private predicate/helper that reads only `side` and `repetition`:

```python
def _has_usable_pairing(execution: HistoricalExecution) -> bool:
    slots: set[tuple[str, int]] = set()
    baseline: set[int] = set()
    candidate: set[int] = set()
    for attempt in execution.attempts:
        side = attempt.side
        repetition = attempt.repetition
        if side is None or side not in {"baseline", "candidate"}:
            return False
        if repetition is None or repetition < 1:
            return False
        slot = (side, repetition)
        if slot in slots:
            return False
        slots.add(slot)
        (baseline if side == "baseline" else candidate).add(repetition)
    return bool(baseline) and baseline == candidate
```

No runtime, outcome, or usage field is allowed inside this helper.

- [ ] **Step 6: Implement effectiveness and independent resource samples**

Effectiveness eligibility: usable pairing + every `success`/`valid` is not `None` + every `valid is True` + every baseline `success is True`. Detection is any candidate `success is False`. Runtime sample: usable pairing + all nonnegative `duration_ms`; sum all attempt durations regardless of success/valid. Token sample: usable pairing + all `usage_observed` and nonnegative input/output; sum exactly `input_tokens + output_tokens` for every attempt regardless of duration/success/valid.

Do not import `qualock.qualification.policy` or `qualock.run.executor` in this module.

- [ ] **Step 7: Implement current-suite aggregation, medians, and deterministic ordering**

Filter historical executions against `set(current_canary_ids)` but build `not_enough_history`, per-canary estimates, and missing lists by iterating the original `current_canary_ids` sequence. Use `statistics.median` on integer sample tuples without pre-rounding. Compute suite values only when every current ID has that metric; otherwise `None` plus exact missing IDs. Sort only `ranked` by the Spec §7.5 four-key ranking.
- [ ] **Step 8: Run Task 2 GREEN and regression checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_analysis.py tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history tests/unit/test_history_analysis.py tests/unit/test_history_loader.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock/history
git diff --check
```

Expected: both history test modules PASS; Ruff/compileall/diff-check PASS.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/qualock/history/models.py src/qualock/history/analysis.py src/qualock/history/__init__.py tests/unit/test_history_analysis.py
git commit -m "feat: analyze historical canary effectiveness"
```

Reviewer must independently verify Invariant P3. Any cross-metric gate, policy import, obsolete-ID output, pre-rounded median, or fabricated empty-suite zero is Important.

### Task 3: Add Deterministic Low-Tech History Rendering

**Files:**
- Create: `src/qualock/history/render.py`
- Create: `tests/unit/test_history_render.py`
- Modify: `src/qualock/history/__init__.py`

**Interfaces:**
- Consumes: `HistoryAnalysis` only; no filesystem/config access.
- Produces: `render_history_text(analysis: HistoryAnalysis) -> str`.

- [ ] **Step 1: Write rendering RED tests for zero history and section structure**

Pin output that always includes loaded/ignored counts and the exact section labels `Estimated model-attempt runtime` and `Estimated model tokens`. For `loaded_reports == 0`, assert plain guidance includes `No qualification history found yet` and never uses error language or raises.
- [ ] **Step 2: Write RED tests for exact display rounding**

Pin helpers through public output, not private helper tests only:

```python
analysis = HistoryAnalysis(
    loaded_reports=3,
    ignored_reports=(),
    ranked=(CanaryEffectiveness("sample", 3, 2, 2 / 3),),
    not_enough_history=(),
    per_canary_estimates=(CanaryEstimate("sample", (60_600,), (12_346,), 60_600.0, 12_345.6),),
    suite_estimate=SuiteEstimate(60_600.0, 12_345.6, (), ()),
)
text = render_history_text(analysis)
assert "1m 1s" in text
assert "12,346" in text
assert "67%" in text
```

Add exact `.5` banker-rounding fixtures, e.g. `60_500ms -> 1m 0s` and `12_344.5 tokens -> 12,344`, so tests pin Python built-in `round` rather than decimal half-up behavior.

Include an exact `.5` banker-rounding fixture so tests pin Python built-in `round` rather than decimal half-up behavior.

- [ ] **Step 3: Write RED tests for missing lists, ranking, and safe diagnostics**

Assert ranked rows use `analysis.ranked` order; not-enough-history rows preserve current config order already supplied by analysis; missing-runtime/token lists are explicit and separate; ignored diagnostics may include only qualification directory name + one fixed reason category. Assert no raw exception payload/content appears in output.

Add a wording regression that lowercases the full output and rejects forbidden advisory-confusion terms produced by the renderer itself: `pricing`, `price`, `billing`, and monetary-cost phrasing. Do not reject a canary ID merely because user-controlled text contains those substrings; tests should target renderer-owned labels/copy, not arbitrary IDs.

- [ ] **Step 4: Run rendering tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_render.py
```

Expected: FAIL because `render_history_text` does not exist.

- [ ] **Step 5: Implement display-only formatting and rendering**

Keep private helpers deterministic:

```python
def _format_runtime(milliseconds: float) -> str:
    seconds = round(milliseconds / 1000)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60}s"
```
```python
def _format_tokens(tokens: float) -> str:
    return f"{round(tokens):,}"


def _format_rate(rate: float) -> str:
    return f"{round(rate * 100)}%"
```

`render_history_text` must always include the note that model-attempt runtime excludes setup, preparation, and CLI overhead. It must render unavailable suite metrics plus exact missing IDs, ranked current canaries, not-enough-history current canaries, loaded/ignored report counts, and optional fixed-category ignored diagnostics. Do not recompute medians/ranking in render.

- [ ] **Step 6: Run Task 3 GREEN and commit**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_render.py tests/unit/test_history_analysis.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history tests/unit/test_history_render.py
git diff --check
git add src/qualock/history/render.py src/qualock/history/__init__.py tests/unit/test_history_render.py
git commit -m "feat: render historical qualification insights"
```

Reviewer must confirm rendering is display-only: no file reads, config reads, sample aggregation, ranking, or mutation of analysis values.

### Task 4: Wire `execute_history` Through Existing Project Loading

**Files:**
- Modify: `src/qualock/commands.py:1-280`
- Modify: `tests/unit/test_commands.py`

**Interfaces:**
- Consumes: `load_project`, `project_dir`, `scan_results`, `analyze_history`.
- Produces: `execute_history(root: Path) -> HistoryAnalysis`.
- Must not render text or map Typer exit codes; that remains Task 5.

- [ ] **Step 1: Write command composition RED tests**
Reuse `setup_project()` already present in `tests/unit/test_commands.py`. Add:

```python
def test_execute_history_uses_current_canaries_in_config_order(tmp_path: Path, monkeypatch) -> None:
    setup_project(tmp_path)
    seen: dict[str, object] = {}
    summary = HistorySummary(loaded=(), ignored=())
    expected = HistoryAnalysis(
        loaded_reports=0, ignored_reports=(), ranked=(), not_enough_history=(),
        per_canary_estimates=(), suite_estimate=SuiteEstimate(None, None, (), ()),
    )

    def fake_scan(path: Path) -> HistorySummary:
        seen["path"] = path
        return summary

    def fake_analyze(value: HistorySummary, ids: Sequence[str]) -> HistoryAnalysis:
        seen["summary"] = value
        seen["ids"] = tuple(ids)
        return expected

    monkeypatch.setattr(commands_module, "scan_results", fake_scan)
    monkeypatch.setattr(commands_module, "analyze_history", fake_analyze)
    assert execute_history(tmp_path) is expected
    assert seen["path"] == tmp_path.resolve() / ".qualock/results"
    assert seen["summary"] is summary
    assert seen["ids"] == ("sample",)
```

Import the exact history result types used above into `tests/unit/test_commands.py`; do not share mutable test helpers across modules. Add `test_execute_history_rejects_empty_canary_suite` by creating valid config with an empty canary directory and matching exact message `no canaries found`. Add a propagation test showing `ConfigError`/`CanaryLoadError` are not wrapped into `CommandError` in `commands.py`.

- [ ] **Step 2: Run command tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_commands.py -k history
```

Expected: FAIL because `execute_history` is missing.

- [ ] **Step 3: Implement minimal `execute_history` composition**

```python
def execute_history(root: Path) -> HistoryAnalysis:
    _config, canaries = load_project(root)
    if not canaries:
        raise CommandError("no canaries found")
    summary = scan_results(project_dir(root) / "results")
    return analyze_history(summary, [canary.id for canary in canaries])
```

Do not catch config/canary exceptions here. Do not render or touch artifacts.
- [ ] **Step 4: Run Task 4 GREEN and focused regressions**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_commands.py -k "history or check or baseline"
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/commands.py tests/unit/test_commands.py
git diff --check
```

Expected: history command-composition tests PASS and existing baseline/check command tests remain green.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/qualock/commands.py tests/unit/test_commands.py
git commit -m "feat: compose local history analysis"
```

Reviewer must confirm `execute_history` is read-only composition only, preserves current canary order, rejects an empty suite before analysis, and leaves load exceptions for CLI mapping.

### Task 5: Expose the Argument-Free `qualock history` CLI and End-to-End Safety Contracts

**Files:**
- Modify: `src/qualock/cli.py:1-780`
- Modify: `tests/unit/test_cli.py`
- Test-only dependency: `tests/unit/test_history_loader.py` already contains the path-neutral loader characterization from Task 1 and is re-run here; Task 5 does not edit it.

**Interfaces:**
- Consumes: `execute_history(Path.cwd())` and `render_history_text(analysis)`.
- Produces: `qualock history` with no flags/arguments in Batch #41.
- Error contract: `(ConfigError, CanaryLoadError, CommandError, ValueError)` -> print message + exit 3; unexpected exceptions -> print message + exit 1.

- [ ] **Step 1: Write CLI RED tests for normal output and zero history**

Monkeypatch only the analysis boundary for copy-focused tests:

```python
def test_history_zero_history_exits_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    analysis = HistoryAnalysis(
        loaded_reports=0,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(CanaryEffectiveness("sample", 0, 0, None),),
        per_canary_estimates=(CanaryEstimate("sample", (), (), None, None),),
        suite_estimate=SuiteEstimate(None, None, ("sample",), ("sample",)),
    )
    monkeypatch.setattr("qualock.cli.execute_history", lambda root: analysis)
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "No qualification history found yet" in result.stdout
    assert "Estimated model-attempt runtime" in result.stdout
```

Import the exact history dataclasses into `tests/unit/test_cli.py`; keep this fixture local to the CLI test module.

- [ ] **Step 2: Write CLI RED tests for exit semantics and argument-free surface**

Add parameterized tests proving each configured failure maps to exit 3 without a traceback:

```python
@pytest.mark.parametrize("exc", [ConfigError("bad config"), CanaryLoadError("bad canary"), CommandError("no canaries found"), ValueError("bad input")])
def test_history_configuration_failures_exit_3(tmp_path: Path, monkeypatch, exc: Exception) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_history", lambda root: (_ for _ in ()).throw(exc))
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 3
    assert str(exc) in result.stdout
```

Also add one unexpected `RuntimeError` case that exits `1`, and assert `runner.invoke(app, ["history", "extra"])` is rejected by Typer because #41 exposes no positional arguments or flags beyond framework help.

- [ ] **Step 3: Run CLI history tests to verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_cli.py -k history
```

Expected: FAIL because the `history` command/imports do not exist yet.
- [ ] **Step 4: Implement the minimal CLI handler**

Add imports for `execute_history` and `render_history_text`, then register exactly:

```python
@app.command("history")
def history_command() -> None:
    try:
        analysis = execute_history(Path.cwd())
    except (ConfigError, CanaryLoadError, CommandError, ValueError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except Exception as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    console.print(render_history_text(analysis), end="", markup=False)
```

Do not add `--json`, filtering, pricing, budget, write, or network options.

- [ ] **Step 5: Pin actual-project cold-start and zero-write behavior end to end**

Use the existing project test helpers to create a valid configured project with one canary and no `results/` directory, invoke `qualock history`, and assert exit `0`, the zero-history guidance, and that the directory remains absent. Add a second fixture containing one valid `report.json`; snapshot every artifact path, bytes, and `st_mtime_ns` before invoking the real command, then assert the snapshot is identical afterward.

```python
before = {p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns) for p in results.rglob("*") if p.is_file()}
result = runner.invoke(app, ["history"])
after = {p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns) for p in results.rglob("*") if p.is_file()}
assert result.exit_code == 0
assert after == before
```
- [ ] **Step 6: Pin Windows/path-neutral behavior**

Re-run the Task 1 path-neutral loader characterization: all fixture paths are `Path` objects, directory ordering is asserted from `Path.name`, and no production code splits path strings on `/` or `\\`. The existing `windows-latest` CI lane must run the same test suite. No Windows-only production branch is allowed.

- [ ] **Step 7: Run Task 5 GREEN and regressions**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_cli.py -k "history or check or baseline or monitor"
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py tests/unit/test_commands.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/cli.py tests/unit/test_cli.py tests/unit/test_history_loader.py
git diff --check
```

Expected: history CLI tests PASS, zero-write/cold-start proofs PASS, and existing check/baseline/monitor tests remain green.

- [ ] **Step 8: Commit Task 5**

```bash
git add src/qualock/cli.py tests/unit/test_cli.py
git commit -m "feat: expose local qualification history"
```

Reviewer must confirm the command is argument-free/read-only, config failures exit `3`, unexpected failures exit `1`, zero history exits `0`, and no existing command output/error behavior changed.

### Task 6: Verify, Integrate, Document, and Close Batch #41

**Files:**
- Modify after implementation-head CI is green: `README.md`, `ROADMAP.md`
- No production file should change in this task unless a verified review/CI finding requires a scoped fix.

**Interfaces:**
- Consumes the complete Batch #41 implementation from Tasks 1–5.
- Produces a reviewed, CI-green PR merged to `main` by rebase merge.
- Batch #42 remains deferred; no provider pricing table or monetary estimate is introduced.
- [ ] **Step 1: Run fresh implementation-head local gates**

Run from `/home/pacmap/qualock-history` on the exact committed implementation HEAD:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py tests/unit/test_commands.py tests/unit/test_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
set +e
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock > /tmp/b41-mypy.txt 2>&1
mypy_rc=$?
set -e
cat /tmp/b41-mypy.txt
test "$mypy_rc" -eq 1
test "$(grep -F -c '[import-untyped]' /tmp/b41-mypy.txt)" -eq 3
grep -F 'src/qualock/config/io.py:' /tmp/b41-mypy.txt
grep -F 'src/qualock/canary/loader.py:' /tmp/b41-mypy.txt
grep -F 'src/qualock/project_setup/config.py:' /tmp/b41-mypy.txt
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/history src/qualock/commands.py src/qualock/cli.py tests/unit/test_history_loader.py tests/unit/test_history_analysis.py tests/unit/test_history_render.py tests/unit/test_commands.py tests/unit/test_cli.py
git diff --check f6410c610642b4e271a9932172326006f37cfaa6..HEAD
```

Strict mypy may report only the three exact base PyYAML `import-untyped` findings in `config/io.py`, `canary/loader.py`, and `project_setup/config.py`; any additional error blocks. Changed Batch #41 Python files must be Ruff-clean.

- [ ] **Step 2: Prove no new full-tree Ruff debt and no protected-scope changes**

Archive the exact base into a temporary directory outside the worktree and require canonical full-tree Ruff diagnostics to be byte-identical to HEAD; Batch #41 is not allowed to fix or add unrelated base lint debt:

```bash
base_tmp=$(mktemp -d)
git archive f6410c610642b4e271a9932172326006f37cfaa6 | tar -x -C "$base_tmp"
set +e
(cd "$base_tmp" && /home/pacmap/qualock-easy/.venv/bin/ruff check . > /tmp/b41-base-ruff.txt 2>&1)
base_ruff_rc=$?
/home/pacmap/qualock-easy/.venv/bin/ruff check . > /tmp/b41-head-ruff.txt 2>&1
head_ruff_rc=$?
set -e
test "$head_ruff_rc" -eq "$base_ruff_rc"
cmp /tmp/b41-base-ruff.txt /tmp/b41-head-ruff.txt
rm -rf "$base_tmp"
```

Separately require the protected diff to be empty:

```bash
git diff --name-only f6410c610642b4e271a9932172326006f37cfaa6..HEAD -- src/qualock/agents src/qualock/github_pr src/qualock/release_monitor src/qualock/version_bisect src/qualock/scheduler src/qualock/source src/qualock/qualification/policy.py src/qualock/run/executor.py pyproject.toml
```

Expected: no output. Record exact HEAD and full gate outputs in the SDD ledger.
- [ ] **Step 3: Run independent whole-implementation review before push**

Review exact diff `f6410c610642b4e271a9932172326006f37cfaa6..HEAD` against the canonical spec. Block on any Critical/Important finding; repair through a fresh implementation worker plus scoped re-review. The reviewer must explicitly verify metric independence, duplicate-ID handling, zero-write behavior, current-config filtering, empty-suite exit `3`, cold-start exit `0`, protected surfaces, and no pricing/policy coupling.

- [ ] **Step 4: Push implementation head and open a non-draft PR**

Create the scratch PR body outside the repo with this exact scope statement:

```text
## Summary
- add read-only `qualock history` over existing local `report.json` artifacts
- rank current canaries from stable-baseline historical detections
- estimate model-attempt runtime and normalized model tokens with independent metric trust gates
- preserve pre-#40 effectiveness/runtime history and never rewrite artifacts

## Boundaries
- advisory only; no qualification policy/executor changes
- no pricing, billing, network/provider calls, database/index, or artifact migration
- provider-specific monetary estimates remain deferred to #42

## Verification
Implementation-head local tests/static gates and independent review are required before this PR; Windows and all Linux CI lanes must be green before documentation is marked delivered.
```

Write that text to `/mnt/c/Users/PACMAP/batch41_pr_body.md`, then:

```bash
git push -u origin feat/history-estimates
gh pr create --base main --head feat/history-estimates --title "feat: add historical qualification insights" --body-file /mnt/c/Users/PACMAP/batch41_pr_body.md
gh pr view --json number,state,isDraft,headRefOid,mergeable,mergeStateStatus,url
```

The PR body must summarize the advisory-only history command, metric independence, backward-compatible artifact salvage, zero-write contract, local test/static evidence, and explicitly state that #42 pricing is not implemented.

- [ ] **Step 5: Require implementation-head CI green on every lane**

```bash
gh pr checks --watch --fail-fast
```

Require Python 3.11/3.12/3.13 and `windows-test` all PASS. If CI exposes a defect, reproduce it locally, fix only the root cause via TDD, scoped-review the fix, rerun the full Step 1–2 gates on the new exact HEAD, push, and require a fresh all-green CI run before proceeding.
- [ ] **Step 6: Update README/ROADMAP only after implementation-head CI is green**

Update `README.md` with a concise `qualock history` example and the exact advisory framing: estimated model-attempt runtime excludes setup/preparation/CLI overhead; estimated tokens are historical normalized observations, not pricing/billing or a cap. Update `ROADMAP.md` to mark #41 delivered and leave #42 pending/advisory/outside pass-fail policy, exactly as canonical spec §13 requires.

```bash
git add README.md ROADMAP.md
git commit -m "docs: document historical qualification insights"
```

Do not change product claims beyond verified behavior and do not introduce #42 pricing language as delivered functionality.

- [ ] **Step 7: Re-run complete docs-inclusive local gates and push docs head**

Repeat Task 6 Steps 1–2 on the new exact HEAD, including full pytest, compileall, strict mypy baseline comparison, changed-file Ruff, full-tree no-new-Ruff proof, diff-check, and protected-scope proof. Then:

```bash
git push origin feat/history-estimates
gh pr checks --watch --fail-fast
```

Require a fresh all-green Python 3.11/3.12/3.13 + Windows CI run on the docs-inclusive head.

- [ ] **Step 8: Run final whole-branch review on exact docs-inclusive head**

Use Claude Opus high exactly once for the exact base-to-HEAD diff; if Claude is hard-limited at that point, use Codex GPT-5.6 Sol high as the single fallback reviewer and ledger the substitution rather than starting duplicate reviewers. Require `APPROVED` with no Critical/Important findings. Any final-review finding must be fixed minimally, scoped re-reviewed, and followed by fresh exact-head local gates plus fresh all-green CI before merge.
- [ ] **Step 9: Verify exact identity and rebase-merge the PR**

Immediately before merge, require local HEAD, remote branch head, and PR `headRefOid` to be identical, and require the latest PR checks to be all green:

```bash
git rev-parse HEAD
git ls-remote origin refs/heads/feat/history-estimates
gh pr view --json state,headRefOid,mergeable,mergeStateStatus,url
gh pr checks
gh pr merge --rebase --delete-branch=false
```

After merge, verify `gh pr view --json state,mergedAt,mergeCommit,url` reports `MERGED`, fetch `origin/main`, and record the resulting main SHA. Preserve the host-owned worktree/branch unless cleanup is explicitly authorized by the finishing workflow.

- [ ] **Step 10: Close Batch #41 bookkeeping**

Record the implementation commits, review verdicts, exact local gate counts, CI run identity, final PR head, merge result, and any baseline waivers in the SDD ledger. Remove only the Batch #41 `.superpowers/sdd/...` scratch directory after merged-state verification. Never create a tag, release, package publish, or provider-pricing artifact in this batch.

## Plan Self-Review

- **Spec coverage:** Tasks 1–5 implement every canonical spec surface: models/loader, independent metrics/ranking/estimates, renderer, command composition, CLI/error/zero-write/Windows behavior. Task 6 implements every review/CI/docs/roadmap/merge gate.
- **Boundary coverage:** `qualification/policy.py`, `run/executor.py`, canary/config schemas, provider adapters, GitHub/release/bisect/scheduler/source surfaces and `pyproject.toml` remain protected; #42 pricing remains deferred.
- **Type consistency:** `scan_results(Path) -> HistorySummary`, `analyze_history(HistorySummary, Sequence[str]) -> HistoryAnalysis`, `render_history_text(HistoryAnalysis) -> str`, and `execute_history(Path) -> HistoryAnalysis` use the same names/types in every task.
- **Artifact compatibility:** Task 1 normalizes raw JSON field-by-field and salvages pre-#40 reports; no task deserializes historical JSON into the current `QualificationResult` dataclass or rewrites artifacts.
- **Metric independence:** Task 2 has separate assertions proving malformed outcome, duration, and usage affect only their own metric after shared pairing validation.
- **No placeholders:** every task names exact files, test commands, implementation interfaces, expected RED/GREEN behavior, and commit boundaries. No implementation decision is deferred to a worker.

## Execution Handoff

Use **Subagent-Driven Development** for this plan. The project's standing workflow delegates routine execution decisions to the controller, so no additional execution-mode confirmation is required: one fresh implementer per task, independent task review after each task, fix loops for Critical/Important findings, and final whole-branch review before merge.
