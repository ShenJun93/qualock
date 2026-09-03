# QuaLock Version Bisect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `qualock bisect codex@X.Y.Z` to find the first confirmed bad stable Codex release after the locked baseline without creating a second qualification engine.

**Architecture:** Extend `CodexResolver` with metadata-only stable-version enumeration. Add a focused `version_bisect` package for immutable outcomes, atomic summary provenance, and sequential orchestration; every candidate delegates to existing `execute_check()`, while `cli.py` only renders progress and maps terminal state to exit codes.

**Tech Stack:** Python 3.11+, Typer, Pydantic 2, `packaging`, pytest, Ruff, strict mypy, existing QuaLock resolver/baseline/qualification infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-03-version-bisect-design.md`

## Global Constraints

- Base: `cee40d6b5475f6496df2980692a3748ba4020934`; branch: `feat/version-bisect`; approved spec commit: `f75f2bbbf0569edecbfac35223c2f581a30547e5`.
- V1 accepts only `codex@X.Y.Z` where `X.Y.Z` is an exact published stable version. Reject `latest`, prereleases, alternate agents, equal/older upper bounds, and unpublished upper bounds.
- Freeze the npm stable catalog once per run. Enumerate metadata only; catalog discovery must never install Codex or mutate cache.
- Scan published stable candidates in numeric SemVer order `(baseline, upper]`; do not assume regressions are monotonic.
- Every candidate must call `execute_check(root, "codex@<exact-version>")`; do not share/reuse a baseline run or old qualification result.
- Continue only on `PASS`. `BLOCK` is first bad only after an all-PASS prefix. `WARN` and `INCOMPLETE` stop unresolved and the bisect command exits `4`.
- Never mutate `baseline.lock`, canaries, repetitions, verdict policy, release-monitor state, scheduler state, globally installed Codex, or `pyproject.toml`.
- Write initial bisect summary before the first check and atomically refresh it after every returned qualification. A raised exception/interruption must leave only truthful completed-prefix provenance and no fabricated terminal claim.
- No push, PR, merge, tag, GitHub Release, or PyPI action is authorized by this plan.

---

### Task 1: Stable Codex Version Catalog

**Files:**
- Modify: `src/qualock/agents/resolver.py`
- Modify: `tests/unit/test_agent_resolver.py`

**Interfaces:**
- Produces `CodexResolver.stable_versions() -> tuple[str, ...]`.
- One command only: `[npm, view, @openai/codex, versions, --json]`, timeout 30 seconds.
- Return unique stable `X.Y.Z` strings sorted by integer `(major, minor, patch)`.

- [ ] **Step 1: Write failing resolver tests**

Add this success test:

```python
def test_stable_versions_filters_dedupes_and_sorts_numerically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    def fake_run(args: list[str], *, timeout_seconds: int) -> ProcessResult:
        calls.append(args)
        return ProcessResult(0, '["0.10.0","0.9.0","0.10.0","0.11.0-beta.1","1.0.0"]', "", 0.01, False)
    monkeypatch.setattr("qualock.agents.resolver.run_process", fake_run)
    cache = tmp_path / "cache"
    resolver = CodexResolver(cache, npm_executable="npm", machine="x86_64")
    assert resolver.stable_versions() == ("0.9.0", "0.10.0", "1.0.0")
    assert calls == [["npm", "view", "@openai/codex", "versions", "--json"]]
    assert not cache.exists()
```

Add failure coverage:

```python
@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(None, "", "registry timeout", 30.0, True), "registry timeout"),
        (ProcessResult(1, "", "registry failed", 0.02, False), "registry failed"),
        (ProcessResult(0, "not-json", "", 0.01, False), "unexpected Codex versions"),
        (ProcessResult(0, '{"0":"0.150.0"}', "", 0.01, False), "unexpected Codex versions"),
        (ProcessResult(0, '["0.150.0",151]', "", 0.01, False), "unexpected Codex versions"),
    ],
)
def test_stable_versions_rejects_bad_registry_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: ProcessResult, message: str
) -> None:
    monkeypatch.setattr("qualock.agents.resolver.run_process", lambda *args, **kwargs: result)
    with pytest.raises(CodexResolveError, match=message):
        CodexResolver(tmp_path / "cache", machine="x86_64").stable_versions()
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_agent_resolver.py -q
```

Expected: new tests fail because `stable_versions()` is absent.

- [ ] **Step 3: Implement minimal catalog parsing**

Import `json`, add `_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")`, and:

```python
def _stable_version_key(version: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"not a stable version: {version!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def stable_versions(self) -> tuple[str, ...]:
    result = run_process([self.npm_executable, "view", "@openai/codex", "versions", "--json"], timeout_seconds=30)
    if result.timed_out or result.exit_code != 0:
        raise CodexResolveError(result.stderr.strip() or "failed to resolve Codex versions")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CodexResolveError("unexpected Codex versions from npm") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise CodexResolveError("unexpected Codex versions from npm")
    stable = {item for item in payload if _STABLE_VERSION_RE.fullmatch(item)}
    return tuple(sorted(stable, key=_stable_version_key))
```

Keep existing `_VERSION_RE` and `resolve()` behavior unchanged.

- [ ] **Step 4: Verify and commit Task 1**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_agent_resolver.py -q
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents/resolver.py tests/unit/test_agent_resolver.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents/resolver.py
git add src/qualock/agents/resolver.py tests/unit/test_agent_resolver.py
git diff --cached --check
git commit -m "feat: enumerate stable Codex releases"
```

Expected: tests/Ruff/mypy/diff-check exit `0` before commit.

---

### Task 2: Bisect Models and Atomic Summary Storage

**Files:**
- Create: `src/qualock/version_bisect/__init__.py`
- Create: `src/qualock/version_bisect/models.py`
- Create: `src/qualock/version_bisect/storage.py`
- Create: `tests/unit/test_version_bisect_models.py`
- Create: `tests/unit/test_version_bisect_storage.py`

**Interfaces:** `BisectStep(version, qualification_id, verdict)`, `BisectStop`, terminal `BisectOutcome`, and `FileBisectSummaryStore.create/save`.

- [ ] **Step 1: Write RED model test**

```python
from dataclasses import FrozenInstanceError
import pytest
from qualock.qualification.models import Verdict
from qualock.version_bisect.models import BisectOutcome, BisectStep, BisectStop

def test_models_are_frozen_and_reuse_verdict() -> None:
    step = BisectStep("0.152.0", "check-1", Verdict.PASS)
    outcome = BisectOutcome(
        bisect_id="bisect-test", baseline_version="0.151.0", upper_version="0.152.0",
        steps=(step,), last_known_good="0.152.0", first_bad=None,
        stop_reason=BisectStop.NO_BAD_FOUND,
    )
    assert outcome.steps[0].verdict is Verdict.PASS
    with pytest.raises(FrozenInstanceError):
        step.version = "0.153.0"  # type: ignore[misc]
```

- [ ] **Step 2: Write RED storage tests**

Create a real `FileBisectSummaryStore` test that calls `create()` with candidates `("0.152.0", "0.153.0")`, no steps, `last_known_good="0.151.0"`, `first_bad=None`, `stop_reason=None`, then assert parsed `summary.json` equals those frozen fields and `schema_version == 1`.

Then call `save()` with `BisectStep("0.152.0", "check-1", Verdict.BLOCK)`, `first_bad="0.152.0"`, `stop_reason=BisectStop.FIRST_BAD_FOUND`; assert JSON step verdict is `"block"`, terminal fields match, and `list(run_dir.glob(".*.tmp")) == []`.

Add a collision test: pre-create `bisect-test/summary.json` containing `preserve`; `create(..., bisect_id="bisect-test")` must raise `FileExistsError` and leave `preserve` unchanged.

- [ ] **Step 3: Run RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_storage.py -q
```

Expected: import errors because the package is absent.

- [ ] **Step 4: Implement immutable models**

```python
from dataclasses import dataclass
from enum import Enum
from qualock.qualification.models import Verdict

class BisectStop(str, Enum):
    NO_BAD_FOUND = "no_bad_found"
    FIRST_BAD_FOUND = "first_bad_found"
    WARN_UNRESOLVED = "warn_unresolved"
    INCOMPLETE = "incomplete"

@dataclass(frozen=True)
class BisectStep:
    version: str
    qualification_id: str
    verdict: Verdict

@dataclass(frozen=True)
class BisectOutcome:
    bisect_id: str
    baseline_version: str
    upper_version: str
    steps: tuple[BisectStep, ...]
    last_known_good: str
    first_bad: str | None
    stop_reason: BisectStop
```

- [ ] **Step 5: Implement atomic summary storage**

`FileBisectSummaryStore.create()` must use `run_dir.mkdir(parents=True, exist_ok=False)` and write `summary.json`. `save()` must require `run_dir.is_dir()` then replace only `summary.json`.

Use this atomic helper and payload shape:

```python
def _replace_summary(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _payload(bisect_id: str, baseline: str, upper: str, candidates: tuple[str, ...],
             steps: tuple[BisectStep, ...], last_good: str, first_bad: str | None,
             stop: BisectStop | None) -> dict[str, object]:
    return {
        "schema_version": 1, "bisect_id": bisect_id,
        "baseline_version": baseline, "upper_version": upper,
        "candidates": list(candidates),
        "steps": [{"version": s.version, "qualification_id": s.qualification_id,
                   "verdict": s.verdict.value} for s in steps],
        "last_known_good": last_good, "first_bad": first_bad,
        "stop_reason": stop.value if stop is not None else None,
    }
```

Expose `BisectSummaryStore` as a `Protocol` with the same `create/save` keyword fields used by `_payload`; this lets orchestration tests inject an in-memory store without filesystem I/O.

- [ ] **Step 6: Verify and commit Task 2**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_storage.py -q
/tmp/qualock-static-22-final/bin/ruff check src/qualock/version_bisect tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_storage.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/version_bisect/models.py src/qualock/version_bisect/storage.py
git add src/qualock/version_bisect tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_storage.py
git diff --cached --check
git commit -m "feat: add version bisect provenance models"
```

Expected: all verification commands exit `0` before commit.

---

### Task 3: Forward-Scan Orchestration

**Files:**
- Create: `src/qualock/version_bisect/commands.py`
- Create: `tests/unit/test_version_bisect_commands.py`

**Interfaces:**
- `VersionCatalog.stable_versions() -> tuple[str, ...]`.
- `execute_bisect(root, upper_spec, *, catalog=None, summary_store=None, check_executor=execute_check, bisect_id=None, on_start=None, on_step=None) -> BisectOutcome`.
- `on_start(baseline: str, upper: str, run_dir: Path)` runs after initial summary persistence; `on_step(step: BisectStep)` runs after that step is persisted.

- [ ] **Step 1: Write RED preflight/range tests**

Use fake catalog/check/store objects so tests never hit npm, Docker, or real Codex. Cover these RED cases explicitly:

```python
@pytest.mark.parametrize("upper", ["codex@latest", "codex@0.153.0-beta.1", "other@0.153.0"])
def test_invalid_upper_stops_before_catalog(tmp_path: Path, monkeypatch, upper: str) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    catalog = FakeCatalog(("0.152.0", "0.153.0"))
    with pytest.raises(CommandError):
        execute_bisect(tmp_path, upper, catalog=catalog, summary_store=MemoryStore(), check_executor=fail_check)
    assert catalog.calls == 0

@pytest.mark.parametrize("upper", ["0.151.0", "0.150.0", "0.154.0"])
def test_upper_must_be_newer_and_published(tmp_path: Path, monkeypatch, upper: str) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    with pytest.raises(CommandError):
        execute_bisect(tmp_path, f"codex@{upper}",
            catalog=FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0")),
            summary_store=MemoryStore(), check_executor=fail_check)
```

Add a non-stable baseline case (`0.151.0-beta.1`) and non-Codex baseline case; both must raise `CommandError`. Add a real preflight-order test that patches `load_project`, `read_baseline_lock`, fingerprints, and `assert_suite_fresh`; a raised `BaselineStaleError` must occur before catalog access.

- [ ] **Step 2: Write RED verdict/provenance tests**

Core scan test:

```python
def test_pass_prefix_then_block_is_first_bad(tmp_path: Path, monkeypatch) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    calls: list[str] = []
    results = {
        "codex@0.152.0": qualification("0.152.0", Verdict.PASS, "check-152"),
        "codex@0.153.0": qualification("0.153.0", Verdict.BLOCK, "check-153"),
        "codex@0.154.0": qualification("0.154.0", Verdict.PASS, "check-154"),
    }
    outcome = execute_bisect(tmp_path, "codex@0.154.0",
        catalog=FakeCatalog(("0.150.0", "0.152.0", "0.153.0", "0.154.0")),
        summary_store=MemoryStore(),
        check_executor=lambda root, spec: calls.append(spec) or results[spec],
        bisect_id="bisect-test")
    assert calls == ["codex@0.152.0", "codex@0.153.0"]
    assert outcome.stop_reason is BisectStop.FIRST_BAD_FOUND
    assert outcome.last_known_good == "0.152.0"
    assert outcome.first_bad == "0.153.0"
```

Add parameterized WARN/INCOMPLETE tests asserting only the first candidate runs, `first_bad is None`, and stop reason is `WARN_UNRESOLVED`/`INCOMPLETE`. Add all-PASS coverage asserting baseline is excluded, upper included, all candidates run in numeric order, `NO_BAD_FOUND`, and `last_known_good == upper_version`.

Add exception/progress ordering: initial `store.create` precedes first check; each returned step is `store.save`d before `on_step`; if the next check raises `RuntimeError("candidate crashed")`, propagate it and assert the last saved summary has `stop_reason is None` and `first_bad is None`.

- [ ] **Step 3: Run RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_commands.py -q
```

Expected: import failure because `commands.py` is absent.

- [ ] **Step 4: Implement preflight and frozen range**

Use the existing freshness chain exactly:

```python
def bisect_preflight(root: Path) -> BisectPreflight:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    if lock.agent.name != "codex":
        raise CommandError("version bisect supports only a Codex baseline")
    _version_key(lock.agent.version)  # stable X.Y.Z required
    return BisectPreflight(baseline_version=lock.agent.version)
```

`_version_key()` uses regex `^(\d+)\.(\d+)\.(\d+)$` and integer tuples. In `execute_bisect`, call `parse_agent_spec(upper_spec)` and `_version_key(upper)` before preflight; then fetch `frozen_catalog = tuple((catalog or _default_catalog()).stable_versions())`, require exact upper membership, require `upper > baseline`, and compute:

```python
candidates = tuple(
    version for version in sorted(set(frozen_catalog), key=_version_key)
    if _version_key(context.baseline_version) < _version_key(version) <= _version_key(upper_version)
)
```

Do not require the baseline itself to remain in npm metadata. Generate IDs as `bisect-<UTC YYYYmmddTHHMMSSZ>-<8 hex>`.

- [ ] **Step 5: Implement sequential loop with truthful persistence**

Create the initial summary before checking; invoke `on_start` only after it exists. For each candidate call `check_executor(root, f"codex@{version}")`, append `BisectStep`, map PASS/BLOCK/WARN/INCOMPLETE explicitly, save the full prefix, then invoke `on_step`.

Use this decision block; do not use a catch-all verdict branch:

```python
if result.verdict is Verdict.PASS:
    last_known_good = version
    first_bad = None
    stop_reason = None
elif result.verdict is Verdict.BLOCK:
    first_bad = version
    stop_reason = BisectStop.FIRST_BAD_FOUND
elif result.verdict is Verdict.WARN:
    first_bad = None
    stop_reason = BisectStop.WARN_UNRESOLVED
elif result.verdict is Verdict.INCOMPLETE:
    first_bad = None
    stop_reason = BisectStop.INCOMPLETE
else:
    raise AssertionError(f"unsupported qualification verdict: {result.verdict!r}")
```

Return immediately after persisted BLOCK/WARN/INCOMPLETE. If every candidate passes, perform one final summary save with `stop_reason=NO_BAD_FOUND` and return the matching terminal outcome. Do not catch exceptions from `check_executor` or storage: propagation preserves fail-closed behavior and the last truthful summary prefix.

- [ ] **Step 6: Verify and commit Task 3**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_commands.py tests/unit/test_agent_resolver.py tests/unit/test_commands.py tests/unit/test_release_monitor_flow.py -q
/tmp/qualock-static-22-final/bin/ruff check src/qualock/version_bisect src/qualock/agents/resolver.py tests/unit/test_version_bisect_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/version_bisect src/qualock/agents/resolver.py
git add src/qualock/version_bisect/commands.py tests/unit/test_version_bisect_commands.py
git diff --cached --check
git commit -m "feat: find first bad Codex release"
```

Expected: all targeted tests/static checks exit `0`; release-monitor tests remain green.

---

### Task 4: Low-Tech Bisect CLI

**Files:**
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_version_bisect_cli.py`

**Interfaces:** top-level `qualock bisect UPPER`; callbacks print start/step progress; terminal renderer prints conclusion and `.qualock/results/<bisect-id>/`.

- [ ] **Step 1: Write RED CLI tests**

Use `CliRunner` and monkeypatch `cli.execute_bisect`. The fake executor must call the supplied callbacks before returning a deterministic `BisectOutcome`.

Required assertions:
- `FIRST_BAD_FOUND` with BLOCK: stdout contains header, baseline, upper, `0.152.0  BLOCK`, `FIRST BAD RELEASE`, last known good, evidence path; exit `2`.
- `NO_BAD_FOUND` with PASS: stdout says `No confirmed bad release found through Codex <upper>.`; exit `0`.
- `WARN_UNRESOLVED`: stdout contains `SEARCH STOPPED`, `WARN`, and `No first bad release was claimed.`; exit `4`.
- `INCOMPLETE`: same unresolved copy with `INCOMPLETE`; exit `4`.
- `CommandError`, `ConfigError`, `CanaryLoadError`, `FileNotFoundError`: exit `3`.
- `BaselineStaleError`: exit `4`.
- `CodexResolveError` and unexpected `OSError`: exit `1`, preserving the literal error text.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_cli.py -q
```

Expected: failures because no command/callback wiring exists.

- [ ] **Step 3: Implement callbacks and command wiring**

Import `CodexResolveError`, `execute_bisect`, `BisectOutcome`, `BisectStep`, `BisectStop`. Add:

```python
def _print_bisect_start(baseline: str, upper: str, run_dir: Path) -> None:
    del run_dir
    console.print(f"Baseline: Codex {baseline}", markup=False)
    console.print(f"Searching through: {upper}\n", markup=False)


def _print_bisect_step(step: BisectStep) -> None:
    console.print(f"{step.version}  {step.verdict.value.upper()}", markup=False)
```

`bisect_command(upper: str)` prints `QuaLock Version Bisect`, calls `execute_bisect(..., on_start=_print_bisect_start, on_step=_print_bisect_step)`, catches input/config errors to `3`, `BaselineStaleError` to `4`, `CodexResolveError` to `1`, and unexpected exceptions to `1`. Always print errors with `markup=False`.

- [ ] **Step 4: Implement terminal renderer**

For `FIRST_BAD_FOUND`, assert `first_bad is not None`, print `FIRST BAD RELEASE`, `Codex <first_bad>`, last-known-good, evidence, then exit `2`. For `NO_BAD_FOUND`, print no-bad-through-upper, last-known-good, evidence, return normally. For WARN/INCOMPLETE, read the last step, print `SEARCH STOPPED`, its verdict, `No first bad release was claimed.`, evidence, then exit `4`.

Do not add `--technical`; do not render each candidate's full Easy report.

- [ ] **Step 5: Verify and commit Task 4**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest tests/unit/test_version_bisect_cli.py tests/unit/test_cli.py tests/unit/test_release_monitor_cli.py -q
/tmp/qualock-static-22-final/bin/ruff check src/qualock/cli.py tests/unit/test_version_bisect_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/cli.py src/qualock/version_bisect
git add src/qualock/cli.py tests/unit/test_version_bisect_cli.py
git diff --cached --check
git commit -m "feat: add version bisect CLI"
```

Expected: all targeted tests/static checks exit `0`; existing check/monitor CLI tests remain green.

---

### Task 5: README and ROADMAP

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`

- [ ] **Step 1: Add README section after release scheduling**

Use this exact substance: show `qualock bisect codex@0.160.0`; state the endpoint must be an exact published stable `X.Y.Z`; explain forward scan avoids a false monotonic-regression assumption; every candidate performs a normal contemporaneous baseline-vs-candidate check; PASS continues, BLOCK is first confirmed bad, WARN/INCOMPLETE stop unresolved; note cost can be high; identify `.qualock/results/bisect-.../summary.json` as orchestration provenance linking normal qualification IDs.

- [ ] **Step 2: Update ROADMAP only after feature tests are green**

Add `Forward version scan for the first confirmed bad stable coding-agent release.` to delivered capability scope and remove `Version bisect for the first bad coding-agent release.` from “Next”. Leave GitHub PR reports, additional adapters, and smarter canary/cost controls in “Next”.

- [ ] **Step 3: Verify docs and commit**

```bash
grep -n "bisect\|first confirmed bad" README.md ROADMAP.md docs/superpowers/specs/2026-09-03-version-bisect-design.md
git diff --check
git add README.md ROADMAP.md
git diff --cached --check
git commit -m "docs: explain version bisect"
```

Expected: docs consistently say forward stable scan, exact upper bound, and BLOCK-only first-bad; no claim of binary-search complexity, prerelease support, caching, or automatic Codex updates.

---

### Task 6: Exact-Head Verification and Independent Review

**Files:** No planned source edits. Any behavior fix returns to the owning task with a failing regression test first and a separate commit.

- [ ] **Step 1: Record exact candidate HEAD and require a clean tree**

```bash
git rev-parse HEAD
git status --short
git log --oneline cee40d6b5475f6496df2980692a3748ba4020934..HEAD
```

Expected: status is empty. Record this SHA; all following evidence must be for this exact HEAD.

- [ ] **Step 2: Run full tests, lint, typing, compilation, and CLI discovery**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q -rs
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents/resolver.py src/qualock/version_bisect src/qualock/cli.py tests/unit/test_agent_resolver.py tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_commands.py tests/unit/test_version_bisect_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents/resolver.py src/qualock/version_bisect src/qualock/cli.py
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli --help
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli bisect --help
```

Expected: all exit `0`; `-rs` exposes any skip provenance; top-level help lists `bisect`; bisect help has one required upper argument and no `--technical`/prerelease/update option.

- [ ] **Step 3: Verify base-to-head scope and protected paths**

```bash
git diff --check cee40d6b5475f6496df2980692a3748ba4020934...HEAD
git diff --name-only cee40d6b5475f6496df2980692a3748ba4020934...HEAD
git diff --exit-code cee40d6b5475f6496df2980692a3748ba4020934...HEAD -- src/qualock/qualification src/qualock/run src/qualock/evidence src/qualock/source src/qualock/release_monitor src/qualock/scheduler src/qualock/project_protection src/qualock/project_setup
git diff --exit-code cee40d6b5475f6496df2980692a3748ba4020934...HEAD -- pyproject.toml
git status --short
```

Expected: diff-check passes; only approved resolver/version-bisect/CLI/tests/docs files changed; all protected diffs are empty; `pyproject.toml` unchanged; tree clean.

- [ ] **Step 4: Dispatch an independent whole-branch reviewer on exact HEAD**

Reviewer receives base SHA, exact HEAD, design spec, implementation plan, and actual base-to-head diff. Require explicit answers:

1. Catalog is metadata-only, stable-only, deduped, numerically ordered, and malformed npm payloads fail closed.
2. Upper endpoint is exact/published/stable/newer; `latest` and prereleases are rejected before catalog/check execution where applicable.
3. Preflight reuses current suite/config freshness chain and rejects non-Codex/non-stable baselines.
4. Every candidate delegates to existing `execute_check()`; no qualification/policy/evidence fork exists.
5. Only PASS continues; BLOCK after all-PASS prefix is first bad; WARN/INCOMPLETE stop unresolved.
6. Initial/progress/terminal summary persistence is atomic and exception/interruption never fabricates a terminal claim.
7. Release-monitor, scheduler, qualification, run, evidence, source, project-protection/setup, and `pyproject.toml` are untouched.
8. CLI output/exit mapping matches spec, including WARN remaining WARN while bisect exits `4`.
9. README/ROADMAP make no binary-search, prerelease, cache, automatic-update, or low-cost claim.

Reviewer output must end with `Critical / Important / Minor / P1 / P2` counts and `Approved` or `Changes required`.

- [ ] **Step 5: Resolve findings, then repeat exact-head gates if HEAD changes**

For any correctness/safety finding: reproduce with a failing test when behavior is involved, apply the minimum fix, run the owning task's tests, commit separately, then rerun all Task 6 steps and dispatch a fresh whole-branch review. Do not call the branch reviewed if the reviewed SHA differs from the SHA that passed final local gates.

- [ ] **Step 6: Capture final evidence and STOP before external side effects**

```bash
git rev-parse HEAD
git status --short
git log -1 --oneline
```

Report exact reviewed HEAD, full pytest count/skip provenance, Ruff/mypy/compileall, protected-path checks, reviewer severity counts, and assessment. Then STOP. `push + pr` requires a new explicit user authorization; merge, tag, GitHub Release, and PyPI remain separate boundaries.

## Expected Local Commit Sequence

```text
docs: design version bisect                 # already f75f2bb
feat: enumerate stable Codex releases
feat: add version bisect provenance models
feat: find first bad Codex release
feat: add version bisect CLI
docs: explain version bisect
```

Review fixes, if required, receive additional narrow commits; do not rewrite reviewed history.
