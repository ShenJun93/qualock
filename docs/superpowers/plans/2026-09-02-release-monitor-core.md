# Release Monitor Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent one-shot `qualock monitor` command that discovers the exact latest Codex release without installing it, reuses the existing qualification engine for genuinely new releases, and deduplicates terminal results with operational state outside the repository.

**Architecture:** Add `qualock.release_monitor` as a thin orchestration package. It validates the current baseline/config/canary freshness before any state-based suppression, compares exact versions with `packaging.version.Version`, delegates new releases to the unchanged `execute_check(root, "codex@X.Y.Z")`, and stores only PASS/WARN/BLOCK deduplication state under the per-user state directory. CLI owns rendering and exit codes; qualification/run/evidence/source engines remain unchanged.

**Tech Stack:** Python 3.11+, Pydantic 2, Typer, Rich, platformdirs, packaging, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-02-release-monitor-core-design.md`

## Global Constraints

- V1 supports Codex only; a non-Codex baseline is an input error.
- `baseline.lock` is read-only for monitor and must never be modified.
- `CodexResolver.latest_version()` may run only `npm view @openai/codex version`; it must not create an agent cache directory or execute `npm install`.
- Freshness must be validated with existing `load_project`, `read_baseline_lock`, `suite_fingerprint`, `config_fingerprint`, and `assert_suite_fresh` before monitor state can suppress qualification.
- Baseline identity is SHA-256 of canonical JSON for the already parsed fresh `BaselineLock`.
- Default state path is exactly `Path(user_state_dir("qualock")) / "release-monitor" / "projects" / f"{project_key}.json"`.
- `project_key` is SHA-256 of `os.path.normcase(str(root.resolve()))` encoded as UTF-8.
- Monitor state is a deduplication hint, never a trust artifact; missing/corrupt/unknown-schema state causes conservative work, never synthetic PASS.
- Only PASS/WARN/BLOCK are terminal state records. INCOMPLETE is returned but never persisted as completed state.
- `--force` skips only a matching terminal-state dedupe when latest is newer than baseline; it never forces same-version or downgrade qualification and never bypasses freshness.
- A real qualification receives an exact `codex@X.Y.Z`, never `codex@latest`.
- State-save failure must not change the real PASS/WARN/BLOCK verdict or exit code.
- Sequential idempotence is required; no daemon, scheduler, cross-process monitor lock, auto-baseline, or auto-upgrade is added in Batch #26.
- No intended behavior changes in `src/qualock/qualification/`, `src/qualock/run/`, `src/qualock/evidence/`, or `src/qualock/source/`.
- No release, tag, or PyPI publish action belongs to this batch.

---

### Task 1: Read-only Codex latest-version discovery

**Files:**
- Modify: `src/qualock/agents/resolver.py`
- Modify: `tests/unit/test_agent_resolver.py`

**Interfaces:**
- Consumes: existing `CodexResolver`, `_VERSION_RE`, and `run_process`.
- Produces: `CodexResolver.latest_version() -> str`; existing `resolve("latest")` delegates to that method.

- [ ] **Step 1: Write RED tests for metadata-only discovery**

Add these behaviors to `tests/unit/test_agent_resolver.py`:

```python
import pytest

from qualock.agents.resolver import CodexResolveError
from qualock.run.process import ProcessResult


def test_latest_version_queries_metadata_without_install_or_cache(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    cache = tmp_path / "cache"
    resolver = CodexResolver(cache, npm_executable=str(npm), machine="x86_64")

    assert resolver.latest_version() == "0.151.0"
    assert not cache.exists()


def test_latest_version_rejects_malformed_registry_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(0, "not-a-version\n", "", 0.01, False),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="unexpected Codex version"):
        resolver.latest_version()


def test_latest_version_rejects_registry_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(None, "", "registry timeout", 30.0, True),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="registry timeout"):
        resolver.latest_version()


def test_latest_version_rejects_registry_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", "registry failed", 0.02, False),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="registry failed"):
        resolver.latest_version()
```

Add this exact delegation test so `resolve("latest")` cannot regress to a second parser:

```python
def test_resolve_latest_delegates_to_public_latest_version(tmp_path: Path, monkeypatch) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    calls: list[str] = []
    monkeypatch.setattr(
        resolver,
        "latest_version",
        lambda: calls.append("latest") or "0.151.0",
    )

    binary = resolver.resolve("latest")

    assert calls == ["latest"]
    assert binary.version == "0.151.0"
```


- [ ] **Step 2: Run resolver tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_agent_resolver.py
```

Expected: the new tests fail because `CodexResolver.latest_version` does not exist.

- [ ] **Step 3: Implement the public discovery method**

Replace the private latest resolver with the public method and keep the existing validation/error semantics:

```python
def latest_version(self) -> str:
    result = run_process(
        [self.npm_executable, "view", "@openai/codex", "version"],
        timeout_seconds=30,
    )
    if result.timed_out or result.exit_code != 0:
        raise CodexResolveError(result.stderr.strip() or "failed to resolve Codex latest")
    version = result.stdout.strip()
    if not _VERSION_RE.match(version):
        raise CodexResolveError(f"unexpected Codex version from npm: {version!r}")
    return version
```

In `resolve()` use:

```python
version = self.latest_version() if requested_version == "latest" else requested_version
```

No cache-path creation is allowed in `latest_version()`.

- [ ] **Step 4: Run GREEN + static checks**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_agent_resolver.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents/resolver.py tests/unit/test_agent_resolver.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents/resolver.py
git diff --check
```

Expected: all pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/qualock/agents/resolver.py tests/unit/test_agent_resolver.py
git commit -m "feat: expose read-only Codex release discovery"
```

Reviewer gate: verify `latest_version()` cannot reach `npm install` or create the version cache, and `resolve("latest")` has only one latest-version parser.

---

### Task 2: Operational release-monitor state outside the repository

**Files:**
- Create: `src/qualock/release_monitor/__init__.py`
- Create: `src/qualock/release_monitor/models.py`
- Create: `src/qualock/release_monitor/state.py`
- Create: `tests/unit/test_release_monitor_state.py`

**Interfaces:**
- Produces `MonitorAction`, `TerminalVerdict`, `MonitorState`, and immutable `MonitorOutcome`.
- Produces `MonitorStateStore` protocol with `load(root) -> tuple[MonitorState | None, str | None]` and `save(root, state) -> None`.
- Produces `FileMonitorStateStore(base_dir: Path | None = None)` using the exact per-user default path from the spec.
- Produces `project_key(root: Path) -> str` and `baseline_sha256(lock: BaselineLock) -> str`.

- [ ] **Step 1: Write RED model/state tests**

Create `tests/unit/test_release_monitor_state.py` with these concrete helpers and behaviors:

```python
import hashlib
import json
import os
from pathlib import Path

import pytest

from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.evidence.fingerprint import sha256_canonical
from qualock.release_monitor.models import MonitorState, TerminalVerdict
from qualock.release_monitor.state import FileMonitorStateStore, baseline_sha256, project_key


def sample_baseline_lock() -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name="codex", version="0.151.0", binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


def sample_state(verdict: TerminalVerdict = TerminalVerdict.PASS) -> MonitorState:
    return MonitorState(
        baseline_sha256="d" * 64,
        candidate_version="0.152.0",
        verdict=verdict,
        qualification_id="check-test",
        completed_at="2026-09-02T01:00:00+00:00",
    )


def test_project_key_uses_normalized_absolute_root(tmp_path: Path) -> None:
    expected = hashlib.sha256(
        os.path.normcase(str(tmp_path.resolve())).encode("utf-8")
    ).hexdigest()
    assert project_key(tmp_path) == expected


def test_default_state_path_is_outside_project(tmp_path: Path, monkeypatch) -> None:
    user_state = tmp_path / "user-state"
    monkeypatch.setattr("qualock.release_monitor.state.user_state_dir", lambda app: str(user_state))
    store = FileMonitorStateStore()
    project = tmp_path / "project"

    path = store.path_for(project)

    assert path.parent == user_state / "release-monitor/projects"
    assert path.name == f"{project_key(project)}.json"
    assert project not in path.parents


def test_baseline_sha_is_canonical_for_parsed_lock() -> None:
    lock = sample_baseline_lock()
    expected = sha256_canonical(lock.model_dump(mode="json"))
    assert baseline_sha256(lock) == expected


@pytest.mark.parametrize("verdict", list(TerminalVerdict))
def test_terminal_state_round_trips_atomically(tmp_path: Path, verdict: TerminalVerdict) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    state = sample_state(verdict)

    store.save(tmp_path / "project", state)
    loaded, warning = store.load(tmp_path / "project")

    assert loaded == state
    assert warning is None
    path = store.path_for(tmp_path / "project")
    assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == verdict.value
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_missing_state_is_clean_absence(tmp_path: Path) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    assert store.load(tmp_path / "project") == (None, None)


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        '{"schema_version": 99}',
        '{"schema_version": 1, "baseline_sha256": "x", "agent": "codex", '
        '"candidate_version": "not-a-version", "verdict": "pass", '
        '"qualification_id": "q", "completed_at": "now"}',
        '{"schema_version": 1, "baseline_sha256": "x", "agent": "codex", '
        '"candidate_version": "0.152.0", "verdict": "incomplete", '
        '"qualification_id": "q", "completed_at": "now"}',
    ],
)
def test_invalid_state_is_ignored_with_warning(tmp_path: Path, payload: str) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    path = store.path_for(tmp_path / "project")
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    loaded, warning = store.load(tmp_path / "project")

    assert loaded is None
    assert warning is not None
    assert "ignored" in warning
```

- [ ] **Step 2: Run state tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_release_monitor_state.py
```

Expected: import/collection failure because `qualock.release_monitor` does not exist.

- [ ] **Step 3: Implement immutable models**

Create `src/qualock/release_monitor/models.py` with these imports and types:

```python
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, field_validator

from qualock.qualification.models import QualificationResult, Verdict


class MonitorAction(str, Enum):
    NO_NEW_RELEASE = "no_new_release"
    NO_DOWNGRADE = "no_downgrade"
    ALREADY_QUALIFIED = "already_qualified"
    CHECKED = "checked"


class TerminalVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class MonitorState(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    baseline_sha256: str
    agent: Literal["codex"] = "codex"
    candidate_version: str
    verdict: TerminalVerdict
    qualification_id: str
    completed_at: str

    @field_validator("candidate_version")
    @classmethod
    def validate_candidate_version(cls, value: str) -> str:
        Version(value)
        return value


@dataclass(frozen=True)
class MonitorOutcome:
    action: MonitorAction
    baseline_version: str
    latest_version: str
    qualification_result: QualificationResult | None = None
    recorded_verdict: Verdict | None = None
    state_persisted: bool | None = None
    state_warning: str | None = None
```

Create `src/qualock/release_monitor/__init__.py` exporting the four public model types. Task 3 will add `execute_monitor` to the exports.

- [ ] **Step 4: Implement state identity and atomic file store**

Create `src/qualock/release_monitor/state.py` with this implementation shape:

```python
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol

from platformdirs import user_state_dir
from pydantic import ValidationError

from qualock.baseline.models import BaselineLock
from qualock.evidence.fingerprint import sha256_canonical

from .models import MonitorState


class MonitorStateStore(Protocol):
    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        raise NotImplementedError

    def save(self, root: Path, state: MonitorState) -> None:
        raise NotImplementedError


def project_key(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve())).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def baseline_sha256(lock: BaselineLock) -> str:
    return sha256_canonical(lock.model_dump(mode="json"))


class FileMonitorStateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (
            Path(user_state_dir("qualock")) / "release-monitor" / "projects"
        )

    def path_for(self, root: Path) -> Path:
        return self.base_dir / f"{project_key(root)}.json"

    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        path = self.path_for(root)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            return None, f"release monitor state ignored: {exc}"
        try:
            return MonitorState.model_validate_json(raw), None
        except ValidationError as exc:
            return None, f"release monitor state ignored: {exc}"

    def save(self, root: Path, state: MonitorState) -> None:
        path = self.path_for(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
```

`FileMonitorStateStore` is the production implementation; the protocol methods are interface-only and raise `NotImplementedError` if invoked directly. A failure inside `user_state_dir()` is intentionally not swallowed here; Task 4 maps that operational failure to exit `1` when the default store is actually needed.

- [ ] **Step 5: Run GREEN + static checks**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_release_monitor_state.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/release_monitor tests/unit/test_release_monitor_state.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/release_monitor
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/qualock/release_monitor tests/unit/test_release_monitor_state.py
git commit -m "feat: add release monitor state store"
```

Reviewer gate: deleting/corrupting state can only cause extra work; state files never carry baseline authority and never live under the project directory.

---

### Task 3: Freshness-first release monitor orchestration

**Files:**
- Create: `src/qualock/release_monitor/commands.py`
- Modify: `src/qualock/release_monitor/__init__.py`
- Create: `tests/unit/test_release_monitor_flow.py`

**Interfaces:**
- Consumes Task 1 `CodexResolver.latest_version()` and Task 2 models/store.
- Produces `ReleaseSource` protocol: `latest_version() -> str`.
- Produces `CheckExecutor = Callable[[Path, str], QualificationResult]`.
- Produces `execute_monitor(root: Path, *, force: bool = False, release_source: ReleaseSource | None = None, state_store: MonitorStateStore | None = None, check_executor: CheckExecutor = execute_check) -> MonitorOutcome`.

- [ ] **Step 1: Write RED preflight-order tests**

Create `tests/unit/test_release_monitor_flow.py` with explicit fakes used throughout the task:

```python
from pathlib import Path

import pytest

from qualock.baseline.io import BaselineStaleError
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.commands import CommandError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.commands import execute_monitor
from qualock.release_monitor.models import MonitorState, TerminalVerdict


class FakeReleaseSource:
    def __init__(self, latest: str) -> None:
        self.latest = latest
        self.calls = 0

    def latest_version(self) -> str:
        self.calls += 1
        return self.latest


class FailIfCalledReleaseSource:
    def latest_version(self) -> str:
        raise AssertionError("release discovery must not be called")


class MemoryStateStore:
    def __init__(self, state: MonitorState | None = None, warning: str | None = None) -> None:
        self.state = state
        self.warning = warning
        self.loads = 0
        self.saved: list[MonitorState] = []
        self.save_error: Exception | None = None

    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        self.loads += 1
        return self.state, self.warning

    def save(self, root: Path, state: MonitorState) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(state)


class FailIfCalledStateStore(MemoryStateStore):
    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        raise AssertionError("monitor state must not be read")


def baseline_lock(agent: str = "codex") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version="0.151.0", binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


def qualification(verdict: Verdict, candidate: str = "0.152.0") -> QualificationResult:
    return QualificationResult(
        qualification_id="check-test",
        baseline_version="0.151.0",
        candidate_version=candidate,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
    )
```

For the freshness-order test, monkeypatch `load_project`, `read_baseline_lock`, `suite_fingerprint`, and `config_fingerprint` to deterministic sentinel values, then force the public `assert_suite_fresh` call to raise:

```python
def test_stale_baseline_stops_before_release_or_state_lookup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("qualock.release_monitor.commands.load_project", lambda root: (object(), []))
    monkeypatch.setattr("qualock.release_monitor.commands.read_baseline_lock", lambda path: baseline_lock())
    monkeypatch.setattr("qualock.release_monitor.commands.suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr("qualock.release_monitor.commands.config_fingerprint", lambda config: "config-now")
    monkeypatch.setattr(
        "qualock.release_monitor.commands.assert_suite_fresh",
        lambda *args: (_ for _ in ()).throw(BaselineStaleError("suite changed")),
    )

    with pytest.raises(BaselineStaleError, match="suite changed"):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )
```

Add the same ordering assertion for a parsed non-Codex baseline, expecting `CommandError("release monitor supports only a Codex baseline")`. Add separate tests using a missing `baseline.lock` and malformed baseline JSON to preserve `FileNotFoundError` versus Pydantic validation failure; neither path may call release discovery or state lookup.

- [ ] **Step 2: Run preflight tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_release_monitor_flow.py -k "stale or codex or baseline"
```

Expected: import failure because `commands.py`/`execute_monitor` do not exist.

- [ ] **Step 3: Implement freshness-first preparation**

Implement a private frozen context and preparation function:

```python
@dataclass(frozen=True)
class _MonitorContext:
    baseline_version: str
    baseline_sha256: str


def _prepare_context(root: Path) -> _MonitorContext:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(
        lock,
        suite_fingerprint(canaries),
        config_fingerprint(config),
    )
    if lock.agent.name != "codex":
        raise CommandError("release monitor supports only a Codex baseline")
    return _MonitorContext(
        baseline_version=lock.agent.version,
        baseline_sha256=baseline_sha256(lock),
    )
```

Do not resolve binaries or run canaries here.

- [ ] **Step 4: Write RED version-decision/delegation tests**

Add a fixed fresh context helper and state helper:

```python
from types import SimpleNamespace

import qualock.release_monitor.commands as monitor_commands
from qualock.release_monitor.models import MonitorAction


FRESH_SHA = "f" * 64


def patch_fresh_context(monkeypatch, baseline_version: str = "0.151.0") -> None:
    monkeypatch.setattr(
        monitor_commands,
        "_prepare_context",
        lambda root: SimpleNamespace(
            baseline_version=baseline_version,
            baseline_sha256=FRESH_SHA,
        ),
    )


def terminal_state(
    candidate: str = "0.152.0",
    verdict: TerminalVerdict = TerminalVerdict.PASS,
    baseline_sha: str = FRESH_SHA,
) -> MonitorState:
    return MonitorState(
        baseline_sha256=baseline_sha,
        candidate_version=candidate,
        verdict=verdict,
        qualification_id="check-old",
        completed_at="2026-09-02T01:00:00+00:00",
    )


def fail_check(root: Path, candidate_spec: str) -> QualificationResult:
    raise AssertionError(f"check must not run: {candidate_spec}")
```

Pin the no-new and exact-delegation branches directly:

```python
@pytest.mark.parametrize("latest", ["0.151.0", "0.150.0"])
def test_same_or_older_than_baseline_does_not_read_state_or_check(
    tmp_path: Path,
    monkeypatch,
    latest: str,
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource(latest),
        state_store=FailIfCalledStateStore(),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.NO_NEW_RELEASE
    assert outcome.latest_version == latest


def test_new_release_is_frozen_to_exact_candidate(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []

    def check(root: Path, candidate_spec: str) -> QualificationResult:
        calls.append(candidate_spec)
        return qualification(Verdict.PASS)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(),
        check_executor=check,
    )

    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED
```

Add explicit tests for the remaining decision table:

```python
@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (TerminalVerdict.PASS, Verdict.PASS),
        (TerminalVerdict.WARN, Verdict.WARN),
        (TerminalVerdict.BLOCK, Verdict.BLOCK),
    ],
)
def test_matching_exact_terminal_state_dedupes(
    tmp_path: Path,
    monkeypatch,
    terminal: TerminalVerdict,
    expected: Verdict,
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(verdict=terminal)),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.ALREADY_QUALIFIED
    assert outcome.recorded_verdict is expected


def test_recorded_newer_candidate_prevents_downgrade(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(candidate="0.153.0")),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.NO_DOWNGRADE


def test_force_reruns_only_exact_matching_newer_candidate(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []
    outcome = execute_monitor(
        tmp_path,
        force=True,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state()),
        check_executor=lambda root, spec: calls.append(spec) or qualification(Verdict.PASS),
    )
    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED
```

Also add one test each for: latest newer than the recorded candidate qualifies; baseline-SHA mismatch ignores the record and qualifies; `--force` with latest `<= baseline` still never reads state or checks. Dedupe equality must compare the exact discovered string to `state.candidate_version`; use `Version` only for ordering so a differently spelled semantically equivalent version cannot suppress a real check.

- [ ] **Step 5: Run decision tests to verify RED**

Run the flow suite. Expected: failures because decision logic is not implemented yet.

- [ ] **Step 6: Implement decision and exact delegation**

Add these production helpers/imports in `commands.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from packaging.version import Version
from platformdirs import user_cache_dir

from qualock.agents.resolver import CodexResolver
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.commands import CommandError, execute_check
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict

from .models import MonitorAction, MonitorOutcome
from .state import FileMonitorStateStore, MonitorStateStore, baseline_sha256


class ReleaseSource(Protocol):
    def latest_version(self) -> str:
        raise NotImplementedError


CheckExecutor = Callable[[Path, str], QualificationResult]


@dataclass(frozen=True)
class _MonitorContext:
    baseline_version: str
    baseline_sha256: str


def _default_release_source() -> ReleaseSource:
    return CodexResolver(Path(user_cache_dir("qualock")))


def _prepare_context(root: Path) -> _MonitorContext:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    if lock.agent.name != "codex":
        raise CommandError("release monitor supports only a Codex baseline")
    return _MonitorContext(
        baseline_version=lock.agent.version,
        baseline_sha256=baseline_sha256(lock),
    )
```

Then implement the first version of `execute_monitor()` with decision/delegation but without terminal-state saving yet:

```python
def execute_monitor(
    root: Path,
    *,
    force: bool = False,
    release_source: ReleaseSource | None = None,
    state_store: MonitorStateStore | None = None,
    check_executor: CheckExecutor = execute_check,
) -> MonitorOutcome:
    context = _prepare_context(root)
    source = release_source or _default_release_source()
    latest = source.latest_version()
    baseline_order = Version(context.baseline_version)
    latest_order = Version(latest)

    if latest_order <= baseline_order:
        return MonitorOutcome(
            action=MonitorAction.NO_NEW_RELEASE,
            baseline_version=context.baseline_version,
            latest_version=latest,
        )

    store = state_store or FileMonitorStateStore()
    state, state_warning = store.load(root)
    matching = (
        state
        if state is not None and state.baseline_sha256 == context.baseline_sha256
        else None
    )

    if matching is not None:
        recorded_order = Version(matching.candidate_version)
        if latest == matching.candidate_version and not force:
            return MonitorOutcome(
                action=MonitorAction.ALREADY_QUALIFIED,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )
        if latest_order < recorded_order:
            return MonitorOutcome(
                action=MonitorAction.NO_DOWNGRADE,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )

    result = check_executor(root, f"codex@{latest}")
    return MonitorOutcome(
        action=MonitorAction.CHECKED,
        baseline_version=context.baseline_version,
        latest_version=latest,
        qualification_result=result,
        state_warning=state_warning,
    )
```

The exact-string equality on the dedupe branch is mandatory. The `Version` objects are for ordering only. The default `FileMonitorStateStore()` is created only after `latest > baseline`.

- [ ] **Step 7: Write RED terminal-state tests**

Add these focused tests after the decision table is GREEN:

```python
@pytest.mark.parametrize(
    ("verdict", "terminal"),
    [
        (Verdict.PASS, TerminalVerdict.PASS),
        (Verdict.WARN, TerminalVerdict.WARN),
        (Verdict.BLOCK, TerminalVerdict.BLOCK),
    ],
)
def test_terminal_result_is_persisted(
    tmp_path: Path,
    monkeypatch,
    verdict: Verdict,
    terminal: TerminalVerdict,
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    result = qualification(verdict)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.state_persisted is True
    assert len(store.saved) == 1
    saved = store.saved[0]
    assert saved.baseline_sha256 == FRESH_SHA
    assert saved.candidate_version == "0.152.0"
    assert saved.verdict is terminal
    assert saved.qualification_id == "check-test"
    assert saved.completed_at


def test_incomplete_is_not_persisted(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    result = qualification(Verdict.INCOMPLETE)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.state_persisted is None
    assert store.saved == []


def test_save_failure_preserves_real_verdict_and_warns(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    store.save_error = OSError("disk full")
    result = qualification(Verdict.BLOCK)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.qualification_result.verdict is Verdict.BLOCK
    assert outcome.state_persisted is False
    assert outcome.state_warning is not None
    assert "disk full" in outcome.state_warning


def test_load_warning_does_not_block_real_qualification(tmp_path: Path, monkeypatch) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore(warning="release monitor state ignored: [literal]")

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: qualification(Verdict.PASS),
    )

    assert outcome.action is MonitorAction.CHECKED
    assert outcome.state_warning == "release monitor state ignored: [literal]"
```

Add one combined-warning test where `load()` supplies a warning and `save()` raises; assert both messages remain in `state_warning`.

- [ ] **Step 8: Implement terminal-state update**

Add UTC time imports and the warning helper:

```python
from datetime import UTC, datetime

from .models import MonitorState, TerminalVerdict


def _join_warning(existing: str | None, new: str) -> str:
    return f"{existing}; {new}" if existing else new
```

Replace the final `result = check_executor(root, f"codex@{latest}")` return block with:

```python
result = check_executor(root, f"codex@{latest}")
state_persisted: bool | None = None

if result.verdict is not Verdict.INCOMPLETE:
    terminal_state = MonitorState(
        baseline_sha256=context.baseline_sha256,
        candidate_version=latest,
        verdict=TerminalVerdict(result.verdict.value),
        qualification_id=result.qualification_id,
        completed_at=datetime.now(UTC).isoformat(),
    )
    try:
        store.save(root, terminal_state)
    except Exception as exc:
        state_persisted = False
        state_warning = _join_warning(
            state_warning,
            f"release monitor state could not be saved: {exc}",
        )
    else:
        state_persisted = True

return MonitorOutcome(
    action=MonitorAction.CHECKED,
    baseline_version=context.baseline_version,
    latest_version=latest,
    qualification_result=result,
    state_persisted=state_persisted,
    state_warning=state_warning,
)
```

No exception from `check_executor` is caught here. Existing qualification failures remain authoritative and Task 4 maps them through the existing CLI error vocabulary.

- [ ] **Step 9: Run flow GREEN + preservation/static checks**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_commands.py \
  tests/unit/test_agent_resolver.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/release_monitor tests/unit/test_release_monitor_flow.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/release_monitor
git diff --check
```

Expected: all pass; no changes in qualification/run/evidence/source.

- [ ] **Step 10: Commit Task 3**

```bash
git add src/qualock/release_monitor tests/unit/test_release_monitor_flow.py
git commit -m "feat: orchestrate Codex release monitoring"
```

Reviewer gate: freshness must precede dedupe; state cannot suppress a stale project; exact discovered version must be frozen before existing `execute_check` is called.

---

### Task 4: `qualock monitor` CLI and existing safety-summary reuse

**Files:**
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_release_monitor_cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes Task 3 `execute_monitor`, `MonitorAction`, `MonitorOutcome`.
- Produces CLI `qualock monitor [--force]`.
- Produces private `_render_safety_result(root: Path, result: QualificationResult) -> None` shared by existing `check` and new monitor output.
- Produces private `_monitor_check_executor(root: Path, candidate_spec: str) -> QualificationResult` that prints the pre-qualification transition then calls the existing `execute_check`.

- [ ] **Step 1: Add an exact characterization test for existing easy check output**

Before extracting any rendering helper, add this test to `tests/unit/test_cli.py` using the current implementation:

```python
def test_check_easy_output_is_exactly_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 2
    assert result.stdout == (
        "QuaLock Safety Check\n\n"
        "DON'T UPDATE YET\n\n"
        "At least one critical protected workflow regressed.\n\n"
        "Codex 0.150.0 -> 0.151.0\n\n"
        "Protected workflows\n"
        "- REGRESSED: critical-bug  3/3 -> 0/3\n\n"
        "Recommendation:\n"
        "Keep using Codex 0.150.0 for now. Do not update to Codex 0.151.0 until the \n"
        "regression is understood.\n\n"
        "Technical evidence: .qualock/results/q1/\n"
    )
```

Run only this test before refactoring and require it to PASS. This locks the exact current output that the shared helper must preserve.

- [ ] **Step 2: Write RED monitor CLI tests**

Create `tests/unit/test_release_monitor_cli.py` with explicit outcome/result helpers:

```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock import cli
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.models import MonitorAction, MonitorOutcome
from tests.unit.test_report import sample_result


runner = CliRunner()


def result_with_verdict(verdict: Verdict) -> QualificationResult:
    source = sample_result()
    return source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=source.run_order,
    )


def monitor_outcome(
    action: MonitorAction,
    *,
    result: QualificationResult | None = None,
    recorded: Verdict | None = None,
    warning: str | None = None,
) -> MonitorOutcome:
    return MonitorOutcome(
        action=action,
        baseline_version="0.151.0",
        latest_version="0.152.0",
        qualification_result=result,
        recorded_verdict=recorded,
        state_warning=warning,
    )


def invoke_outcome(tmp_path: Path, monkeypatch, outcome: MonitorOutcome, *args: str):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "execute_monitor", lambda root, **kwargs: outcome)
    return runner.invoke(cli.app, ["monitor", *args])
```

Pin no-op and remembered verdict exits:

```python
def test_monitor_no_new_release_exits_zero(tmp_path: Path, monkeypatch) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        baseline_version="0.151.0",
        latest_version="0.151.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome)
    assert result.exit_code == 0
    assert "QuaLock Release Monitor" in result.stdout
    assert "Baseline: Codex 0.151.0" in result.stdout
    assert "Latest:   Codex 0.151.0" in result.stdout
    assert "No newer Codex release needs qualification." in result.stdout


def test_monitor_baseline_newer_than_npm_reports_no_downgrade(tmp_path: Path, monkeypatch) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        baseline_version="0.152.0",
        latest_version="0.151.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome)
    assert result.exit_code == 0
    assert "Your baseline is newer than npm latest." in result.stdout
    assert "No downgrade qualification was run." in result.stdout


def test_monitor_no_downgrade_exits_zero(tmp_path: Path, monkeypatch) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.NO_DOWNGRADE, recorded=Verdict.PASS),
    )
    assert result.exit_code == 0
    assert "no downgrade check" in result.stdout.lower()


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [(Verdict.PASS, 0), (Verdict.WARN, 0), (Verdict.BLOCK, 2)],
)
def test_monitor_remembered_terminal_exit(
    tmp_path: Path,
    monkeypatch,
    verdict: Verdict,
    exit_code: int,
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.ALREADY_QUALIFIED, recorded=verdict),
    )
    assert result.exit_code == exit_code
    assert verdict.value.upper() in result.stdout
```

Pin fresh result exits and reuse of the existing safety summary:

```python
@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [
        (Verdict.PASS, 0),
        (Verdict.WARN, 0),
        (Verdict.BLOCK, 2),
        (Verdict.INCOMPLETE, 4),
    ],
)
def test_monitor_checked_result_uses_existing_safety_summary(
    tmp_path: Path,
    monkeypatch,
    verdict: Verdict,
    exit_code: int,
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.CHECKED, result=result_with_verdict(verdict)),
    )
    assert result.exit_code == exit_code
    assert "QuaLock Safety Check" in result.stdout
    assert "Technical evidence: .qualock/results/q1/" in result.stdout
```

Pin exception mapping with one test per public bucket:

```python
@pytest.mark.parametrize(
    "exc",
    [ConfigError("bad config"), CanaryLoadError("bad canary"), CommandError("bad input"), FileNotFoundError("missing")],
)
def test_monitor_input_errors_exit_three(tmp_path: Path, monkeypatch, exc: Exception) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "execute_monitor", lambda root, **kwargs: (_ for _ in ()).throw(exc))
    result = runner.invoke(cli.app, ["monitor"])
    assert result.exit_code == 3


def test_monitor_stale_baseline_exits_four(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(BaselineStaleError("stale")),
    )
    assert runner.invoke(cli.app, ["monitor"]).exit_code == 4


def test_monitor_operational_error_exits_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(OSError("state unavailable")),
    )
    assert runner.invoke(cli.app, ["monitor"]).exit_code == 1
```

Also add: `state_warning="ignored [literal]"` remains literal and does not change a remembered PASS exit; invoking `monitor --force` makes the fake `execute_monitor` observe `force is True`.

For transition ordering, test the private wrapper directly after Step 5: monkeypatch `cli.read_baseline_lock` to return `SimpleNamespace(agent=SimpleNamespace(version="0.151.0"))`, then monkeypatch `cli.console.print` and `cli.execute_check` into an event list and assert baseline/latest/transition output appears before `"check"`.

- [ ] **Step 3: Run monitor CLI tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_release_monitor_cli.py
```

Expected: failure because `monitor` command does not exist.

- [ ] **Step 4: Extract existing safety renderer without output change**

Move only the current easy-mode rendering block into:

```python
def _render_safety_result(root: Path, result: QualificationResult) -> None:
    try:
        _config, canaries = load_project(root)
        display_names = {canary.id: canary.name for canary in canaries}
    except (ConfigError, CanaryLoadError, FileNotFoundError):
        display_names = {}
    summary = build_safety_summary(result, display_names)
    evidence_path = f".qualock/results/{result.qualification_id}/"
    console.print(render_safety_terminal(summary, evidence_path), end="", markup=False)
```

Use it from `check_command`; keep technical mode untouched. Re-run `tests/unit/test_cli.py` immediately and require byte-equivalent asserted output.

- [ ] **Step 5: Implement monitor command and transition wrapper**

Add imports for `Version`, `read_baseline_lock`, `QualificationResult`, `MonitorAction`, and `execute_monitor`. `project_dir` is already imported by the CLI. Keep `Verdict` imported from the existing qualification models.

The transition wrapper is:

```python
def _monitor_check_executor(root: Path, candidate_spec: str) -> QualificationResult:
    version = candidate_spec.rsplit("@", 1)[1]
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    console.print(f"Baseline: Codex {lock.agent.version}", markup=False)
    console.print(f"Latest:   Codex {version}", markup=False)
    console.print(
        f"\nNew Codex release found. Qualifying {version} against baseline "
        f"{lock.agent.version}.",
        markup=False,
    )
    return execute_check(root, candidate_spec)
```

Use this exact command signature and control flow:

```python
@app.command("monitor")
def monitor_command(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run the matching newer Codex release if it was already qualified.",
        ),
    ] = False,
) -> None:
    root = Path.cwd()
    console.print("QuaLock Release Monitor\n", end="", markup=False)
    try:
        outcome = execute_monitor(
            root,
            force=force,
            check_executor=_monitor_check_executor,
        )
    except (ConfigError, CanaryLoadError, CommandError, FileNotFoundError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except BaselineStaleError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(4) from exc
    except Exception as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    if outcome.action is not MonitorAction.CHECKED:
        console.print(f"Baseline: Codex {outcome.baseline_version}", markup=False)
        console.print(f"Latest:   Codex {outcome.latest_version}", markup=False)
    if outcome.state_warning:
        console.print(f"Warning: {outcome.state_warning}", markup=False)

    if outcome.action is MonitorAction.NO_NEW_RELEASE:
        if Version(outcome.latest_version) < Version(outcome.baseline_version):
            console.print(
                "Your baseline is newer than npm latest. No downgrade qualification was run.",
                markup=False,
            )
        else:
            console.print("No newer Codex release needs qualification.", markup=False)
        return
    if outcome.action is MonitorAction.NO_DOWNGRADE:
        console.print(
            "A newer candidate was already qualified; no downgrade check was run.",
            markup=False,
        )
        return
    if outcome.action is MonitorAction.ALREADY_QUALIFIED:
        if outcome.recorded_verdict is None:
            console.print("Release monitor state is missing its verdict.", markup=False)
            raise typer.Exit(1)
        console.print(
            f"Codex {outcome.latest_version} was already qualified for this baseline: "
            f"{outcome.recorded_verdict.value.upper()}",
            markup=False,
        )
        console.print("Run `qualock monitor --force` to qualify it again.", markup=False)
        if outcome.recorded_verdict is Verdict.BLOCK:
            raise typer.Exit(2)
        return

    result = outcome.qualification_result
    if result is None:
        console.print("Release monitor check result is missing.", markup=False)
        raise typer.Exit(1)
    _render_safety_result(root, result)
    if result.verdict is Verdict.BLOCK:
        raise typer.Exit(2)
    if result.verdict is Verdict.INCOMPLETE:
        raise typer.Exit(4)
```

For the transition-order test, test `_monitor_check_executor` directly: monkeypatch `read_baseline_lock` to return `SimpleNamespace(agent=SimpleNamespace(version="0.151.0"))`, monkeypatch `console.print` to append rendered messages to a list, and monkeypatch `execute_check` to append `"check"`; assert the baseline line, latest line, and transition entry all precede `"check"`.

All warning/message prints that can contain paths or exception text use `markup=False`.

- [ ] **Step 6: Run CLI GREEN + existing CLI preservation**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/cli.py tests/unit/test_release_monitor_cli.py tests/unit/test_cli.py
git diff --check
```

Expected: all pass and existing `check` assertions remain unchanged.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/cli.py tests/unit/test_release_monitor_cli.py tests/unit/test_cli.py
git commit -m "feat: add one-shot release monitor CLI"
```

Reviewer gate: remembered BLOCK stays exit `2`, INCOMPLETE never dedupes, warnings are literal-safe, and monitor does not invent a second report format.

---

### Task 5: User docs, full preservation, independent review, and integration gates

**Files:**
- Modify: `README.md`
- Verify all files changed since base `8b0d74f6fa2d7d408daa0809e66b100290bce129`.

**Interfaces:**
- Documents the one-shot core that Batch #27 scheduler will call.
- Does not add scheduler installation, daemon behavior, release/tag/PyPI work, or new qualification semantics.

- [ ] **Step 1: Update README with low-tech monitor workflow**

Document immediately after the baseline/check quick-start material:

```text
qualock monitor
qualock monitor --force
```

Explain in plain language:

```text
- checks npm metadata for the newest Codex version without downloading it first;
- does nothing expensive when latest is not newer than the baseline;
- qualifies a genuinely newer exact version through the same `qualock check` engine;
- remembers terminal PASS/WARN/BLOCK per fresh baseline so repeated one-shot runs are cheap;
- retries INCOMPLETE later;
- `--force` reruns only a matching newer candidate and never bypasses stale baseline checks;
- user-state corruption/deletion causes conservative re-checking, never false trust;
- this command is one-shot; automatic scheduling is intentionally Batch #27.
```

- [ ] **Step 2: Run focused preservation suite**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_commands.py \
  tests/unit/test_cli.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py
```

Expected: all pass.

- [ ] **Step 3: Run exact-head full/local static gates**

Record `git rev-parse HEAD`, then run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/agents/resolver.py \
  src/qualock/release_monitor \
  src/qualock/cli.py \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/agents/resolver.py \
  src/qualock/release_monitor
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check 8b0d74f6fa2d7d408daa0809e66b100290bce129...HEAD
git status --short
```

Require a clean tree after the README commit and zero failures.

- [ ] **Step 4: Scope audit**

List changed paths from the exact base. Require no diff in:

```text
src/qualock/qualification/
src/qualock/run/
src/qualock/evidence/
src/qualock/source/
```

A resolver method extraction, new release-monitor package, CLI integration, tests, spec/plan, and README are in scope. Any execution-engine diff is blocking.

- [ ] **Step 5: Independent final review**

Review exact base `8b0d74f6fa2d7d408daa0809e66b100290bce129` through current `HEAD` against the approved spec and this plan. Require explicit coverage of:

```text
discovery no-install boundary
freshness-before-dedupe ordering
baseline/project identity
state corruption/delete semantics
version ordering + --force
exact execute_check delegation
INCOMPLETE retry
state-save failure authority
CLI exits + remembered BLOCK
existing check output preservation
scope audit / Batch #27 non-goals
```

No merge while Critical/Important/P1/P2 findings remain. Fix accepted findings with a RED test first, re-run focused/full gates, and re-review the finding.

- [ ] **Step 6: Commit README/final docs**

```bash
git add README.md docs/superpowers/plans/2026-09-02-release-monitor-core.md
git commit -m "docs: explain one-shot release monitoring"
```

If the plan file was already committed before execution, commit only README here.

- [ ] **Step 7: Push exact reviewed head and open PR #26**

PR summary must include:

```text
qualock monitor one-shot flow
metadata-only npm discovery
freshness-before-dedupe guarantee
per-user operational state outside repo
exact existing execute_check delegation
INCOMPLETE retry and remembered BLOCK behavior
no scheduler/daemon in this batch
final local tests/static/reviewer evidence
```

- [ ] **Step 8: Exact-head PR CI gate**

Require GitHub CI success for Python 3.11, 3.12, and 3.13, including the existing Docker tmpfs smoke on the configured matrix job. Verify PR head SHA still equals the reviewed/tested SHA immediately before merge.

- [ ] **Step 9: Squash merge with expected head SHA**

Use squash merge with `--match-head-commit <reviewed-sha>`. Do not tag, release, or publish PyPI.

- [ ] **Step 10: Post-merge main verification**

Confirm `origin/main` equals the returned merge SHA. Require push-CI success on that exact merge SHA for Python 3.11/3.12/3.13. Fast-forward local `main` and run a fresh full local suite if worker capacity permits.

## Definition of Done

Batch #26 is complete only when:

- `qualock monitor` discovers latest Codex metadata without installation;
- same/older-than-baseline releases do no expensive qualification;
- a genuinely newer release is frozen to an exact version and delegated to existing `execute_check`;
- fresh baseline/config/canary context is validated before dedupe;
- matching terminal PASS/WARN/BLOCK state dedupes per exact baseline identity;
- remembered BLOCK remains exit `2`;
- INCOMPLETE remains retryable and is not persisted;
- state deletion/corruption causes conservative requalification;
- state-save failure cannot change the authoritative qualification result;
- `--force` cannot bypass freshness or force same/downgrade checks;
- existing baseline/check qualification behavior and safety-summary output are preserved;
- qualification/run/evidence/source execution core has zero intended diff;
- full exact-head local tests/static gates pass;
- independent final review has no unresolved high-priority findings;
- exact-head PR CI and post-merge main CI pass.
