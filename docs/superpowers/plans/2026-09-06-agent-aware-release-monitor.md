# Agent-Aware Release Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `qualock monitor` and native scheduled monitoring support Claude Code through a shared latest-release discovery capability while preserving Codex behavior and keeping Antigravity fail-closed.

**Architecture:** Add a small agent-level `LatestReleaseSource` boundary that normalizes Codex/Claude resolver discovery failures and refuses Antigravity. Extend monitor context/outcomes with agent identity, preserve monitor state schema version 1 while widening its existing `agent` value domain to `codex|claude`, and make monitor/CLI/scheduler surfaces consume the trusted agent identity instead of assuming Codex.

**Tech Stack:** Python 3.11+, Typer, Pydantic, `packaging.version.Version`, `platformdirs`, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-06-agent-aware-release-monitor-design.md`

## Global Constraints

- Base implementation work on branch `feat/agent-aware-release-monitor` in `/home/pacmap/qualock-release-source`.
- Preserve existing Codex monitor behavior, state compatibility, exit codes, scheduler backend behavior, and registration schema.
- Support release monitoring only for `codex` and `claude`.
- Antigravity monitoring and schedule enable must fail closed before release discovery, state lookup, qualification, binary probing, or authentication.
- Do not add Antigravity release discovery, downloads, web lookup, resolver probes, or Windows fallback.
- Do not add Claude `stable_versions()` or enable Claude `bisect` or GitHub PR qualification.
- Do not change qualification runtime, Docker/host sandbox contracts, credentials, authentication, or agent execution semantics.
- `MonitorState.schema_version` remains `1`; field names and state file/store layout remain unchanged.
- Widen only the existing `MonitorState.agent` value domain from `Literal["codex"]` to `Literal["codex", "claude"]`; keep default `"codex"` for backward compatibility.
- Do not change scheduler registration/state schema.
- No new dependencies and no dependency installation for tooling.
- Do not run authenticated Claude or Antigravity real-agent tests.
- Do not push, open PRs, merge, tag, release, or publish unless separately authorized.

---

## File Map

### Create

- `src/qualock/agents/releases.py` — latest-release capability protocol, factory, private Codex/Claude adapters, normalized discovery error.
- `tests/unit/test_agent_releases.py` — factory mapping, cache semantics, error normalization, Antigravity fail-closed tests.

### Modify

- `src/qualock/release_monitor/models.py` — widen `MonitorState.agent`; add required `MonitorOutcome.agent_name`.
- `src/qualock/release_monitor/commands.py` — agent-aware preflight, shared release source selection, agent-aware candidate spec, state matching/persistence.
- `src/qualock/cli.py` — agent-aware monitor rendering, force help, operational discovery error mapping, agent-neutral schedule text.
- `tests/unit/test_release_monitor_flow.py` — preflight, Claude flow, state reuse/persistence, Antigravity ordering.
- `tests/unit/test_release_monitor_state.py` — schema-v1 backward compatibility and Claude state round-trip.
- `tests/unit/test_release_monitor_cli.py` — Claude/Codex rendering, discovery error exit, result exits, force help.
- `tests/unit/test_scheduler_commands.py` — expanded `MonitorPreflight` constructor and Claude/Antigravity preflight consequences.
- `tests/unit/test_scheduler_cli.py` — exact agent-neutral schedule copy.
- `README.md` — monitor/schedule support wording and Antigravity scope.

---

### Task 1: Shared Latest-Release Discovery Capability

**Files:**
- Create: `src/qualock/agents/releases.py`
- Create: `tests/unit/test_agent_releases.py`

**Interfaces:**
- Consumes:
  - `CodexResolver(cache_root: Path)` and `CodexResolveError`
  - `ClaudeResolver(cache_root: Path)` and `ClaudeResolveError`
  - `platformdirs.user_cache_dir("qualock")`
- Produces:
  - `class ReleaseDiscoveryError(RuntimeError)`
  - `class LatestReleaseSource(Protocol): latest_version() -> str`
  - `default_latest_release_source(agent_name: str, *, cache_root: Path | None = None) -> LatestReleaseSource`

- [ ] **Step 1: Write failing factory and error-normalization tests**

Create `tests/unit/test_agent_releases.py` with tests equivalent to:

```python
from pathlib import Path

import pytest

import qualock.agents.releases as releases
from qualock.agents.claude_resolver import ClaudeResolveError
from qualock.agents.resolver import CodexResolveError


def test_default_source_maps_codex_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeCodex:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "0.152.0"

    monkeypatch.setattr(releases, "CodexResolver", FakeCodex)
    source = releases.default_latest_release_source("codex", cache_root=tmp_path)

    assert source.latest_version() == "0.152.0"
    assert observed == [tmp_path]


def test_default_source_maps_claude_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeClaude:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "2.1.260"

    monkeypatch.setattr(releases, "ClaudeResolver", FakeClaude)
    source = releases.default_latest_release_source("claude", cache_root=tmp_path)

    assert source.latest_version() == "2.1.260"
    assert observed == [tmp_path]


def test_default_source_uses_qualock_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []
    monkeypatch.setattr(releases, "user_cache_dir", lambda app: str(tmp_path))

    class FakeCodex:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "0.152.0"

    monkeypatch.setattr(releases, "CodexResolver", FakeCodex)
    releases.default_latest_release_source("codex")

    assert observed == [tmp_path]


def test_antigravity_release_discovery_fails_closed() -> None:
    assert not hasattr(releases, "AntigravityResolver")
    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="release discovery is unavailable for Antigravity",
    ):
        releases.default_latest_release_source("antigravity")


@pytest.mark.parametrize(
    ("agent_name", "resolver_attr", "error_type"),
    [
        ("codex", "CodexResolver", CodexResolveError),
        ("claude", "ClaudeResolver", ClaudeResolveError),
    ],
)
def test_resolver_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str,
    resolver_attr: str,
    error_type: type[Exception],
) -> None:
    class BrokenResolver:
        def __init__(self, cache_root: Path) -> None:
            del cache_root

        def latest_version(self) -> str:
            raise error_type("npm unavailable")

    monkeypatch.setattr(releases, resolver_attr, BrokenResolver)
    source = releases.default_latest_release_source(agent_name, cache_root=tmp_path)

    with pytest.raises(releases.ReleaseDiscoveryError, match="npm unavailable"):
        source.latest_version()


def test_unknown_agent_release_discovery_is_rejected() -> None:
    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="release discovery is unavailable for agent 'other'",
    ):
        releases.default_latest_release_source("other")
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_agent_releases.py -q
```

Expected: FAIL during import because `qualock.agents.releases` does not exist.

- [ ] **Step 3: Implement the minimal release capability**

Create `src/qualock/agents/releases.py` with this shape:

```python
from pathlib import Path
from typing import Protocol

from platformdirs import user_cache_dir

from .claude_resolver import ClaudeResolveError, ClaudeResolver
from .resolver import CodexResolveError, CodexResolver


class ReleaseDiscoveryError(RuntimeError):
    pass


class LatestReleaseSource(Protocol):
    def latest_version(self) -> str: ...


class _CodexLatestReleaseSource:
    def __init__(self, resolver: CodexResolver) -> None:
        self.resolver = resolver

    def latest_version(self) -> str:
        try:
            return self.resolver.latest_version()
        except CodexResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class _ClaudeLatestReleaseSource:
    def __init__(self, resolver: ClaudeResolver) -> None:
        self.resolver = resolver

    def latest_version(self) -> str:
        try:
            return self.resolver.latest_version()
        except ClaudeResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


def default_latest_release_source(
    agent_name: str,
    *,
    cache_root: Path | None = None,
) -> LatestReleaseSource:
    cache = cache_root or Path(user_cache_dir("qualock"))
    if agent_name == "codex":
        return _CodexLatestReleaseSource(CodexResolver(cache))
    if agent_name == "claude":
        return _ClaudeLatestReleaseSource(ClaudeResolver(cache))
    if agent_name == "antigravity":
        raise ReleaseDiscoveryError(
            "release discovery is unavailable for Antigravity"
        )
    raise ReleaseDiscoveryError(
        f"release discovery is unavailable for agent {agent_name!r}"
    )
```

Do not import `AntigravityResolver` in this module.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_agent_releases.py -q
```

Expected: all tests PASS.

- [ ] **Step 5: Run touched-file lint**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/agents/releases.py tests/unit/test_agent_releases.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit Task 1**

```bash
git add src/qualock/agents/releases.py tests/unit/test_agent_releases.py
git commit -m "feat: add agent release discovery capability"
```

---

### Task 2: Agent-Aware Monitor Context and State Contract

**Files:**
- Modify: `src/qualock/release_monitor/models.py`
- Modify: `src/qualock/release_monitor/commands.py`
- Modify: `tests/unit/test_release_monitor_flow.py`
- Modify: `tests/unit/test_release_monitor_state.py`
- Modify: `tests/unit/test_release_monitor_cli.py`
- Modify: `tests/unit/test_scheduler_commands.py`

**Interfaces:**
- Consumes:
  - `load_project(root)` returning config with `config.agent.name`
  - trusted baseline lock with `lock.agent.name` and `lock.agent.version`
- Produces:
  - `MonitorPreflight(agent_name: str, baseline_version: str, baseline_sha256: str)`
  - `MonitorOutcome.agent_name: str` as a required field
  - `MonitorState.agent: Literal["codex", "claude"] = "codex"`
- Does not yet switch the default release source or candidate spec; that is Task 3.

- [ ] **Step 1: Update test helpers and write RED tests for preflight**

In `tests/unit/test_release_monitor_flow.py`, make project loading carry an agent:

```python
def project_config(agent_name: str = "codex") -> SimpleNamespace:
    return SimpleNamespace(agent=SimpleNamespace(name=agent_name))


def patch_project_loading(
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str = "codex",
) -> None:
    monkeypatch.setattr(
        "qualock.release_monitor.commands.load_project",
        lambda root: (project_config(agent_name), []),
    )
    monkeypatch.setattr(
        "qualock.release_monitor.commands.suite_fingerprint",
        lambda canaries: "suite-now",
    )
    monkeypatch.setattr(
        "qualock.release_monitor.commands.config_fingerprint",
        lambda config: "config-now",
    )
```

Replace the old non-Codex rejection test with:

```python
def test_claude_fresh_baseline_preflight_returns_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, "claude")
    lock = baseline_lock("claude")
    monkeypatch.setattr(monitor_commands, "read_baseline_lock", lambda path: lock)
    monkeypatch.setattr(monitor_commands, "assert_suite_fresh", lambda *args: None)

    context = monitor_commands.monitor_preflight(tmp_path)

    assert context.agent_name == "claude"
    assert context.baseline_version == lock.agent.version
    assert context.baseline_sha256 == baseline_sha256(lock)


def test_agent_mismatch_fails_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, "claude")
    monkeypatch.setattr(
        monitor_commands,
        "read_baseline_lock",
        lambda path: baseline_lock("codex"),
    )
    monkeypatch.setattr(monitor_commands, "assert_suite_fresh", lambda *args: None)

    with pytest.raises(CommandError, match="baseline agent does not match configured agent"):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )


def test_antigravity_fails_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, "antigravity")
    monkeypatch.setattr(
        monitor_commands,
        "read_baseline_lock",
        lambda path: baseline_lock("antigravity"),
    )
    monkeypatch.setattr(monitor_commands, "assert_suite_fresh", lambda *args: None)

    with pytest.raises(
        CommandError,
        match="release monitor is unavailable for Antigravity",
    ):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )
```

Update `test_monitor_preflight_reuses_exact_freshness_chain` so its fake config exposes `agent.name == "codex"` and assert `context.agent_name == "codex"`.

- [ ] **Step 2: Write RED state compatibility tests**

In `tests/unit/test_release_monitor_state.py`, update `sample_state`:

```python
def sample_state(
    verdict: TerminalVerdict = TerminalVerdict.PASS,
    *,
    agent: str = "codex",
) -> MonitorState:
    return MonitorState(
        baseline_sha256="d" * 64,
        agent=agent,
        candidate_version="0.152.0",
        verdict=verdict,
        qualification_id="check-test",
        completed_at="2026-09-02T01:00:00+00:00",
    )
```

Add:

```python
def test_claude_state_round_trips_without_schema_change(tmp_path: Path) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    state = sample_state(agent="claude")

    store.save(tmp_path / "project", state)
    loaded, warning = store.load(tmp_path / "project")
    payload = json.loads(
        store.path_for(tmp_path / "project").read_text(encoding="utf-8")
    )

    assert warning is None
    assert loaded == state
    assert payload["schema_version"] == 1
    assert payload["agent"] == "claude"


def test_schema_v1_state_without_agent_defaults_to_codex(tmp_path: Path) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    path = store.path_for(tmp_path / "project")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha256": "d" * 64,
                "candidate_version": "0.152.0",
                "verdict": "pass",
                "qualification_id": "check-test",
                "completed_at": "2026-09-02T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    loaded, warning = store.load(tmp_path / "project")

    assert warning is None
    assert loaded is not None
    assert loaded.agent == "codex"
```

- [ ] **Step 3: Run the preflight/state tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_state.py -q
```

Expected failures:
- Claude state rejected by `Literal["codex"]`.
- `MonitorPreflight` lacks `agent_name`.
- existing preflight still rejects non-Codex baselines.

- [ ] **Step 4: Implement the model and preflight changes**

In `src/qualock/release_monitor/models.py`:

```python
class MonitorState(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    baseline_sha256: str
    agent: Literal["codex", "claude"] = "codex"
    candidate_version: str
    verdict: TerminalVerdict
    qualification_id: str
    completed_at: str
```

Make `MonitorOutcome` require agent identity:

```python
@dataclass(frozen=True)
class MonitorOutcome:
    action: MonitorAction
    agent_name: str
    baseline_version: str
    latest_version: str
    qualification_result: QualificationResult | None = None
    recorded_verdict: Verdict | None = None
    state_persisted: bool | None = None
    state_warning: str | None = None
```

In `src/qualock/release_monitor/commands.py`:

```python
@dataclass(frozen=True)
class MonitorPreflight:
    agent_name: str
    baseline_version: str
    baseline_sha256: str
```

Implement preflight after freshness validation:

```python
agent_name = lock.agent.name
if config.agent.name != agent_name:
    raise CommandError("baseline agent does not match configured agent")
if agent_name == "antigravity":
    raise CommandError(
        "release monitor is unavailable for Antigravity because "
        "QuaLock does not discover Antigravity releases"
    )
if agent_name not in {"codex", "claude"}:
    raise CommandError(f"release monitor does not support agent {agent_name!r}")
return MonitorPreflight(
    agent_name=agent_name,
    baseline_version=lock.agent.version,
    baseline_sha256=baseline_sha256(lock),
)
```

Do not select a release source before this preflight completes.

To keep Task 2 independently green while `MonitorOutcome.agent_name` becomes required:

1. add `agent_name=context.agent_name` to every existing `MonitorOutcome(...)` constructor in `execute_monitor()` without otherwise changing the Codex-only execution path yet;
2. update the existing `patch_fresh_context()` helper in `tests/unit/test_release_monitor_flow.py` to return `agent_name="codex"` in its `SimpleNamespace`;
3. add `agent_name="codex"` to every existing direct `MonitorOutcome(...)` constructor in `tests/unit/test_release_monitor_cli.py`;
4. update existing scheduler test constructors to `MonitorPreflight("codex", "0.151.0", "f" * 64)`.

These are interface-compilation updates only. Task 3 owns default-source selection, Claude candidate construction, state-agent matching, and state-agent persistence.

- [ ] **Step 5: Run Task 2 compatibility tests and verify GREEN**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_scheduler_commands.py -q
```

Expected: PASS with existing Codex execution semantics unchanged, plus the new Claude/Antigravity preflight and state-model tests passing.

- [ ] **Step 6: Commit Task 2**

```bash
git add \
  src/qualock/release_monitor/models.py \
  src/qualock/release_monitor/commands.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_scheduler_commands.py
git commit -m "feat: make monitor preflight agent-aware"
```

---

### Task 3: Agent-Aware Monitor Execution and State Reuse

**Files:**
- Modify: `src/qualock/release_monitor/commands.py`
- Modify: `tests/unit/test_release_monitor_flow.py`

**Interfaces:**
- Consumes:
  - `default_latest_release_source(agent_name) -> LatestReleaseSource`
  - `MonitorPreflight.agent_name`
  - `MonitorState.agent`
- Produces:
  - default source selection for Codex/Claude
  - candidate spec `<agent>@<latest>`
  - agent-aware state match/persistence

- [ ] **Step 1: Write RED tests for Claude execution and default source selection**

In `tests/unit/test_release_monitor_flow.py`, update:

```python
def patch_fresh_context(
    monkeypatch: pytest.MonkeyPatch,
    baseline_version: str = "0.151.0",
    agent_name: str = "codex",
) -> None:
    monkeypatch.setattr(
        monitor_commands,
        "monitor_preflight",
        lambda root: SimpleNamespace(
            agent_name=agent_name,
            baseline_version=baseline_version,
            baseline_sha256=FRESH_SHA,
        ),
    )
```

Add:

```python
def test_claude_new_release_qualifies_exact_claude_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, baseline_version="2.1.260", agent_name="claude")
    seen: list[str] = []

    def check(root: Path, candidate_spec: str) -> QualificationResult:
        seen.append(candidate_spec)
        return qualification(Verdict.PASS, candidate="2.1.261")

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("2.1.261"),
        state_store=MemoryStateStore(),
        check_executor=check,
    )

    assert seen == ["claude@2.1.261"]
    assert outcome.agent_name == "claude"


def test_claude_no_new_release_skips_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, baseline_version="2.1.260", agent_name="claude")

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("2.1.260"),
        state_store=FailIfCalledStateStore(),
        check_executor=fail_check,
    )

    assert outcome.action is MonitorAction.NO_NEW_RELEASE
    assert outcome.agent_name == "claude"


def test_default_release_source_uses_trusted_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, baseline_version="2.1.260", agent_name="claude")
    seen: list[str] = []

    class Source:
        def latest_version(self) -> str:
            return "2.1.260"

    monkeypatch.setattr(
        monitor_commands,
        "default_latest_release_source",
        lambda agent_name: seen.append(agent_name) or Source(),
    )

    execute_monitor(
        tmp_path,
        state_store=FailIfCalledStateStore(),
        check_executor=fail_check,
    )

    assert seen == ["claude"]
```

- [ ] **Step 2: Write RED state matching/persistence tests**

Update `terminal_state(...)` helper to accept `agent: str = "codex"` and pass it to `MonitorState`. Add:

```python
def test_claude_terminal_result_persists_claude_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, baseline_version="2.1.260", agent_name="claude")
    store = MemoryStateStore()

    execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("2.1.261"),
        state_store=store,
        check_executor=lambda root, candidate: qualification(
            Verdict.PASS, candidate="2.1.261"
        ),
    )

    assert len(store.saved) == 1
    assert store.saved[0].agent == "claude"


def test_state_with_wrong_agent_is_not_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, baseline_version="2.1.260", agent_name="claude")
    state = terminal_state(
        candidate="2.1.261",
        baseline_sha=FRESH_SHA,
        agent="codex",
    )
    store = MemoryStateStore(state)
    seen: list[str] = []

    execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("2.1.261"),
        state_store=store,
        check_executor=lambda root, candidate: (
            seen.append(candidate)
            or qualification(Verdict.PASS, candidate="2.1.261")
        ),
    )

    assert seen == ["claude@2.1.261"]


def test_codex_candidate_spec_remains_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch, agent_name="codex")
    seen: list[str] = []

    execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(),
        check_executor=lambda root, candidate: (
            seen.append(candidate) or qualification(Verdict.PASS)
        ),
    )

    assert seen == ["codex@0.152.0"]
```

- [ ] **Step 3: Run the flow tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_release_monitor_flow.py -q
```

Expected failures include:
- candidate still `codex@...` for Claude;
- state producer still defaults to Codex;
- state matching ignores agent identity;
- default source still constructs Codex directly.

- [ ] **Step 4: Implement agent-aware execution**

In `src/qualock/release_monitor/commands.py`:

1. Remove the local `ReleaseSource(Protocol)` and `_default_release_source()` definitions, plus their now-unused `Protocol`, `CodexResolver`, and `user_cache_dir` imports.
2. Import:

```python
from qualock.agents.releases import (
    LatestReleaseSource,
    default_latest_release_source,
)
```

3. Type the injection seam as `release_source: LatestReleaseSource | None = None`.
4. Select source only after preflight:

```python
context = monitor_preflight(root)
source = release_source or default_latest_release_source(context.agent_name)
latest = source.latest_version()
```

5. Make state reuse require agent identity:

```python
matching = (
    state
    if (
        state is not None
        and state.baseline_sha256 == context.baseline_sha256
        and state.agent == context.agent_name
    )
    else None
)
```

6. Build candidate from the trusted agent:

```python
result = check_executor(root, f"{context.agent_name}@{latest}")
```

7. Persist agent:

```python
terminal_state = MonitorState(
    baseline_sha256=context.baseline_sha256,
    agent=context.agent_name,
    candidate_version=latest,
    verdict=TerminalVerdict(result.verdict.value),
    qualification_id=result.qualification_id,
    completed_at=datetime.now(UTC).isoformat(),
)
```

Task 2 already populated `MonitorOutcome.agent_name` on every return path; preserve that coverage while changing execution behavior.

Do not catch `ReleaseDiscoveryError` inside `execute_monitor`; let the CLI operational boundary handle it.

- [ ] **Step 5: Run monitor flow/state/source tests and verify GREEN**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_agent_releases.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add \
  src/qualock/release_monitor/commands.py \
  tests/unit/test_release_monitor_flow.py
git commit -m "feat: monitor Claude releases"
```

---

### Task 4: Agent-Aware Monitor CLI and Error Mapping

**Files:**
- Modify: `src/qualock/cli.py`
- Modify: `tests/unit/test_release_monitor_cli.py`

**Interfaces:**
- Consumes:
  - `MonitorOutcome.agent_name`
  - `agent_display_name(agent_name)`
  - `ReleaseDiscoveryError`
- Produces:
  - correct Codex/Claude monitor copy
  - agent-neutral `--force` help
  - release-discovery operational exit code 1
  - unchanged BLOCK=2 and INCOMPLETE=4 semantics

- [ ] **Step 1: Make CLI test helper agent-aware and add Claude RED tests**

In `tests/unit/test_release_monitor_cli.py`:

```python
def monitor_outcome(
    action: MonitorAction,
    *,
    agent_name: str = "codex",
    result: QualificationResult | None = None,
    recorded: Verdict | None = None,
    warning: str | None = None,
) -> MonitorOutcome:
    return MonitorOutcome(
        action=action,
        agent_name=agent_name,
        baseline_version="0.151.0",
        latest_version="0.152.0",
        qualification_result=result,
        recorded_verdict=recorded,
        state_warning=warning,
    )
```

Update every direct `MonitorOutcome(...)` in this test file with `agent_name="codex"`. Add:

```python
def test_monitor_claude_no_new_release_uses_claude_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="claude",
        baseline_version="2.1.260",
        latest_version="2.1.260",
    )

    result = invoke_outcome(tmp_path, monkeypatch, outcome)

    assert result.exit_code == 0
    assert "Baseline: Claude Code 2.1.260" in result.stdout
    assert "Latest:   Claude Code 2.1.260" in result.stdout
    assert "No newer Claude Code release needs qualification." in result.stdout
    assert "Codex" not in result.stdout


def test_monitor_claude_already_qualified_uses_claude_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(
            MonitorAction.ALREADY_QUALIFIED,
            agent_name="claude",
            recorded=Verdict.PASS,
        ),
    )

    assert result.exit_code == 0
    assert "Claude Code 0.152.0 was already qualified" in result.stdout
    assert "Codex" not in result.stdout
```

- [ ] **Step 2: Add RED check-executor and operational-error tests**

Import `ReleaseDiscoveryError` and add:

```python
def test_release_discovery_error_exits_one(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(
            ReleaseDiscoveryError("npm unavailable")
        ),
    )

    result = runner.invoke(cli.app, ["monitor"])

    assert result.exit_code == 1
    assert "npm unavailable" in result.stdout


def test_monitor_check_executor_prints_claude_transition(monkeypatch) -> None:
    events: list[str] = []
    expected = sample_result()
    monkeypatch.setattr(
        cli,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="claude", version="2.1.260")
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, **kwargs: events.append(message),
    )
    monkeypatch.setattr(
        cli,
        "execute_check",
        lambda root, candidate: events.append("check") or expected,
    )

    result = cli._monitor_check_executor(Path("."), "claude@2.1.261")

    assert result is expected
    assert events == [
        "Baseline: Claude Code 2.1.260",
        "Latest:   Claude Code 2.1.261",
        (
            "\nNew Claude Code release found. Qualifying 2.1.261 "
            "against baseline 2.1.260."
        ),
        "check",
    ]


def test_monitor_force_help_is_agent_neutral() -> None:
    result = runner.invoke(cli.app, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "newer release" in result.stdout
    assert "newer Codex release" not in result.stdout
```

- [ ] **Step 3: Run CLI tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_release_monitor_cli.py -q
```

Expected: Claude rendering still says Codex and force help is Codex-specific.

- [ ] **Step 4: Implement agent-aware CLI rendering**

In `src/qualock/cli.py` import:

```python
from qualock.agents.releases import ReleaseDiscoveryError
```

Generalize force help:

```python
help="Re-run the matching newer release if it was already qualified."
```

Make `_monitor_check_executor` parse candidate agent and render its display name:

```python
def _monitor_check_executor(root: Path, candidate_spec: str) -> QualificationResult:
    agent_name, version = parse_agent_spec(candidate_spec)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    display_name = agent_display_name(agent_name)
    console.print(f"Baseline: {display_name} {lock.agent.version}", markup=False)
    console.print(f"Latest:   {display_name} {version}", markup=False)
    console.print(
        (
            f"\nNew {display_name} release found. Qualifying {version} "
            f"against baseline {lock.agent.version}."
        ),
        markup=False,
    )
    return execute_check(root, candidate_spec)
```

In `monitor_command`, derive:

```python
display_name = agent_display_name(outcome.agent_name)
```

Use `display_name` for baseline/latest lines, no-new-release message, already-qualified message, and `_render_safety_result(root, result, display_name)`.

Add an explicit operational boundary before the generic `Exception` catch:

```python
except ReleaseDiscoveryError as exc:
    console.print(str(exc), markup=False)
    raise typer.Exit(1) from exc
```

Do not remove `CodexResolveError` if `bisect` still uses it.

- [ ] **Step 5: Run CLI and monitor tests and verify GREEN**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_agent_releases.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/qualock/cli.py tests/unit/test_release_monitor_cli.py
git commit -m "feat: render agent-aware release monitor"
```

---

### Task 5: Scheduler Consequence, Exact Copy, and README

**Files:**
- Modify: `tests/unit/test_scheduler_commands.py`
- Modify: `tests/unit/test_scheduler_cli.py`
- Modify: `src/qualock/cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes:
  - `MonitorPreflight(agent_name, baseline_version, baseline_sha256)`
  - existing `enable_schedule()` behavior that runs preflight before backend mutation
- Produces:
  - Claude schedule enable proceeds through the existing scheduler seam
  - Antigravity preflight failure prevents native mutation
  - schedule output is agent-neutral
  - docs accurately show Codex+Claude monitor support and Antigravity exclusion

- [ ] **Step 1: Write RED scheduler consequence tests**

Task 2 already updated existing scheduler test constructors to the approved three-field `MonitorPreflight` interface. Add:

```python
def test_enable_accepts_claude_monitor_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    backend = FakeBackend(events, final_state=NativeScheduleState.MATCHING)
    store = MemoryRegistrationStore(events)
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: (
            events.append("preflight")
            or MonitorPreflight("claude", "2.1.260", "f" * 64)
        ),
    )

    outcome = enable_schedule(
        tmp_path,
        backend=backend,
        store=store,
        executable=existing_python(tmp_path),
        home=tmp_path,
        environ={},
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )

    assert outcome.status is ScheduleStatus.ENABLED
    assert events == ["preflight", "probe", "load", "save", "install", "inspect"]


def test_enable_antigravity_preflight_failure_prevents_native_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    backend = FakeBackend(events, final_state=NativeScheduleState.MATCHING)
    store = MemoryRegistrationStore(events)

    def fail_preflight(root: Path) -> MonitorPreflight:
        del root
        raise CommandError(
            "release monitor is unavailable for Antigravity because "
            "QuaLock does not discover Antigravity releases"
        )

    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        fail_preflight,
    )

    with pytest.raises(CommandError, match="unavailable for Antigravity"):
        enable_schedule(
            tmp_path,
            backend=backend,
            store=store,
            executable=existing_python(tmp_path),
            home=tmp_path,
            environ={},
        )

    assert events == []
```

Ensure `CommandError` is imported in this test file.

- [ ] **Step 2: Write RED exact-copy scheduler CLI test**

Update expected output in `tests/unit/test_scheduler_cli.py` from:

```text
It does not update Codex or change your baseline.
```

to:

```text
It does not update the configured agent or change your baseline.
```

Keep the rest of the exact output unchanged.

- [ ] **Step 3: Run scheduler tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py -q
```

Expected: the exact schedule output test fails on Codex-specific copy before the production copy change. Constructor-shape failures must be fixed only by updating tests to the approved `MonitorPreflight` interface; scheduler production semantics should remain unchanged.

- [ ] **Step 4: Generalize schedule CLI copy**

In `_render_schedule_outcome()` in `src/qualock/cli.py`, use:

```python
"The scheduled job only runs `qualock monitor`.",
"It does not update the configured agent or change your baseline.",
```

Do not change scheduler registration, backend, state, runner, or command signatures.

- [ ] **Step 5: Update README monitor/schedule scope**

In the release-monitor section around the existing `qualock monitor` examples, state:

```markdown
Release monitoring supports Codex and Claude Code. QuaLock discovers the
latest published npm release for the configured agent and qualifies it
against the trusted baseline. It never updates the agent or changes the
baseline automatically.
```

In the schedule section, state that the fixed runner invokes the same agent-aware `qualock monitor`, so schedules support Codex and Claude Code only.

In the Antigravity section, use wording equivalent to:

```markdown
Antigravity is supported for `baseline`, `check`, and `doctor`.
`qualock monitor` and scheduled monitoring are unavailable for Antigravity
because QuaLock does not discover Antigravity releases. `qualock bisect`
and the GitHub PR workflow remain Codex-only.
```

Do not claim Claude `bisect` or GitHub PR support.

- [ ] **Step 6: Run scheduler/CLI/docs-focused checks**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_release_monitor_cli.py -q

/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/cli.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_release_monitor_cli.py

git diff --check
```

Expected: all pytest tests PASS, Ruff PASS, diff-check silent.

- [ ] **Step 7: Commit Task 5**

```bash
git add \
  src/qualock/cli.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py \
  README.md
git commit -m "docs: extend release monitoring to Claude"
```

---

### Task 6: Whole-Branch Verification and Review Gate

**Files:**
- No planned production changes.
- Any fix discovered here must use a fresh RED test and a separate fix commit before re-running this gate.

**Interfaces:**
- Verifies the complete Batch #37 branch against the approved spec.
- Produces local-ready evidence only; no push/PR/merge.

- [ ] **Step 1: Confirm branch/diff scope**

Run:

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --name-status origin/main..HEAD
```

Expected:
- branch `feat/agent-aware-release-monitor`;
- only approved spec/plan plus Task 1–5 implementation/test/docs files;
- clean working tree before final verification.

- [ ] **Step 2: Run focused Batch #37 tests**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_agent_releases.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py -q
```

Expected: PASS with 0 failures.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
```

Expected: 0 failures. Platform/auth-gated tests may remain skipped exactly as their guards require; do not enable authenticated-agent guards to reduce skip count.

- [ ] **Step 4: Run Ruff on all touched Python files**

First derive the touched Python file set:

```bash
git diff --name-only origin/main..HEAD -- '*.py'
```

Then run Ruff against that exact set, including at minimum:

```bash
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/agents/releases.py \
  src/qualock/release_monitor/models.py \
  src/qualock/release_monitor/commands.py \
  src/qualock/cli.py \
  tests/unit/test_agent_releases.py \
  tests/unit/test_release_monitor_state.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py
```

Expected: `All checks passed!`

- [ ] **Step 5: Run strict mypy on touched source**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/mypy --strict \
  src/qualock/agents/releases.py \
  src/qualock/release_monitor/models.py \
  src/qualock/release_monitor/commands.py \
  src/qualock/cli.py
```

Expected: no new errors in touched files. The repository/toolchain may still report the known pre-existing missing `types-PyYAML` stubs in:
- `src/qualock/config/io.py`
- `src/qualock/canary/loader.py`
- `src/qualock/project_setup/config.py`

Do not install stubs or dependencies in this batch.

- [ ] **Step 6: Run compile and whitespace gates**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
git diff --check origin/main..HEAD
```

Expected: both commands exit 0 with no error output.

- [ ] **Step 7: Run static security/scope assertions**

Run:

```bash
grep -n 'AntigravityResolver' src/qualock/agents/releases.py && exit 1 || true
grep -n 'stable_versions' src/qualock/agents/releases.py src/qualock/release_monitor/commands.py && exit 1 || true
git diff --name-only origin/main..HEAD | grep -E 'github_pr|version_bisect' && exit 1 || true
```

Expected: no forbidden matches.

Also inspect monitor/schedule docs:

```bash
grep -n -E 'monitor|schedule|Claude|Antigravity|Codex-only' README.md | sed -n '1,220p'
```

Confirm:
- monitor/schedule support Codex + Claude;
- Antigravity monitor/schedule are explicitly unavailable;
- bisect/GitHub PR remain Codex-only.

- [ ] **Step 8: Dispatch fresh whole-branch reviewer**

Use a fresh read-only reviewer against `origin/main..HEAD`. The review prompt must explicitly check:

1. spec compliance;
2. no Codex monitor regression;
3. Claude release discovery uses only existing npm metadata lookup;
4. Antigravity preflight fails before source/state/check and no Antigravity resolver/auth path is touched;
5. `MonitorState` remains schema v1, old Codex state loads, Claude state persists `agent="claude"`, and state reuse checks both baseline SHA and agent;
6. scheduler has no backend/schema change and only inherits monitor capability;
7. CLI exit codes remain 3 input/capability, 1 discovery operational, 2 BLOCK, 4 stale/INCOMPLETE;
8. no `bisect` or GitHub PR behavior expansion;
9. no dependency change.

Require findings grouped as Critical / Important / Minor and a final `READY` or `NEEDS FIXES` verdict.

- [ ] **Step 9: If reviewer finds Critical/Important, fix with TDD and re-review**

For each blocking finding:
1. write a regression test that fails on current HEAD;
2. run it and capture RED;
3. make the minimal fix;
4. run focused GREEN;
5. commit the fix separately;
6. re-run Tasks 6.2–6.8.

Do not waive Critical or Important findings.

- [ ] **Step 10: Record local-ready state**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline origin/main..HEAD
```

Only after fresh verification and reviewer `READY`, report Batch #37 as locally ready. Stop before push/PR/merge unless the user explicitly authorizes those side effects.

