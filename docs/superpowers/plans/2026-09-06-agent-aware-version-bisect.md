# Agent-Aware Version Bisect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable `qualock bisect` for Claude Code through a generic stable-release catalog while preserving Codex behavior and keeping Antigravity fail-closed.

**Architecture:** Extend the existing `qualock.agents.releases` capability boundary with a separate stable-release catalog for Codex and Claude. Make version bisect trust the project/baseline agent, qualify candidates as `<trusted-agent>@<version>`, write agent-identifying v2 evidence for new runs, and render CLI output through the existing display-name helper.

**Tech Stack:** Python 3.11+, pytest, Typer, Pydantic project models, `platformdirs`, npm metadata via existing `run_process`, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-06-agent-aware-version-bisect-design.md`

## Global Constraints

- Support stable-release bisect only for `codex` and `claude`.
- Antigravity must fail before stable catalog access, evidence creation, or candidate qualification.
- Do not import or construct `AntigravityResolver` from `qualock.agents.releases`.
- Stable discovery uses unauthenticated npm metadata only; do not execute or authenticate Claude/Antigravity for discovery tests.
- Do not change monitor, scheduler, GitHub PR qualification, Antigravity runtime/auth behavior, or dependency files.
- Do not install dependencies or agent packages while implementing this batch.
- Historical bisect summary v1 files are not migrated or rewritten.
- New bisect summaries use schema version 2 and trusted `agent` identity.
- Existing forward-scan and exit-code semantics remain unchanged.
- No push, PR, merge, tag, release, or publish without separate user authorization.
- Use `/home/pacmap/qualock-easy/.venv/bin/python`, Ruff, and mypy from the existing sibling toolchain.

---
### Task 1: Claude Stable-Version Catalog

**Files:**
- Modify: `src/qualock/agents/claude_resolver.py`
- Modify: `tests/unit/test_claude_resolver.py`

**Interfaces:**
- Consumes: existing `ClaudeResolver(cache_root, npm_executable="npm", machine=None)` and `run_process(...)`.
- Produces: `ClaudeResolver.stable_versions() -> tuple[str, ...]`.

- [ ] **Step 1: Add focused failing tests for stable catalog parsing**

Add tests that monkeypatch `qualock.agents.claude_resolver.run_process` and assert:

```python
resolver = ClaudeResolver(tmp_path, npm_executable="npm-test")
versions = resolver.stable_versions()
assert versions == ("2.1.9", "2.1.10", "2.1.260")
assert observed_command == [
    "npm-test", "view", "@anthropic-ai/claude-code", "versions", "--json"
]
```

The fake stdout must include duplicates plus prerelease/build strings so the test proves exact `X.Y.Z` filtering, dedupe, and numeric sorting.

- [ ] **Step 2: Add failing error-path tests**

Cover timeout, nonzero exit, malformed JSON, non-list JSON, and a list containing a non-string item. Each must raise `ClaudeResolveError`; timeout/nonzero may preserve stderr text, while malformed payload cases assert the generic unexpected-catalog message.
- [ ] **Step 3: Run Task 1 RED tests**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_claude_resolver.py -k 'stable_versions'
```

Expected: new tests fail because `ClaudeResolver.stable_versions` does not exist.

- [ ] **Step 4: Implement the minimal stable catalog method**

In `claude_resolver.py`, import `json`, add an exact stable regex, and implement the method without calling `resolve()`:

```python
_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _stable_version_key(version: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"not a stable version: {version!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)
```

`stable_versions()` runs the exact npm command from Step 1 with `timeout_seconds=30`, parses JSON, requires `list[str]`, filters exact stable versions, deduplicates, sorts with `_stable_version_key`, and returns a tuple.
- [ ] **Step 5: Run Task 1 GREEN tests and static checks**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_claude_resolver.py
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/agents/claude_resolver.py tests/unit/test_claude_resolver.py
/home/pacmap/qualock-easy/.venv/bin/mypy --strict \
  src/qualock/agents/claude_resolver.py
git diff --check
```

Expected: tests PASS, Ruff PASS, strict mypy PASS, diff-check PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/qualock/agents/claude_resolver.py tests/unit/test_claude_resolver.py
git commit -m "feat: add Claude stable release catalog"
```

---

### Task 2: Generic Stable-Release Capability Boundary

**Files:**
- Modify: `src/qualock/agents/releases.py`
- Modify: `tests/unit/test_agent_releases.py`
**Interfaces:**
- Consumes: `CodexResolver.stable_versions()`, Task 1 `ClaudeResolver.stable_versions()`, existing `ReleaseDiscoveryError`, `default_agent_cache_root()`.
- Produces: `StableReleaseCatalog` protocol and `default_stable_release_catalog(agent_name, *, cache_root=None)`.

- [ ] **Step 1: Write failing factory/adapter tests**

Extend `test_agent_releases.py` with fake Codex and Claude resolvers and assert:

```python
catalog = releases.default_stable_release_catalog("claude", cache_root=tmp_path)
assert catalog.stable_versions() == ("2.1.260", "2.1.261")
```

Add the equivalent Codex mapping test and prove both receive the supplied cache root.

- [ ] **Step 2: Write failing normalization and fail-closed tests**

For both agents, make the fake resolver raise its resolver-specific error and assert `ReleaseDiscoveryError` with the same literal message.

Also assert:

```python
assert not hasattr(releases, "AntigravityResolver")
with pytest.raises(ReleaseDiscoveryError, match="unavailable for Antigravity"):
    releases.default_stable_release_catalog("antigravity")
```

Unknown agent names must also raise `ReleaseDiscoveryError` and must not construct Codex or Claude resolvers.
- [ ] **Step 3: Run Task 2 RED tests**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_agent_releases.py
```

Expected: new stable-catalog tests fail because the protocol/factory are absent.

- [ ] **Step 4: Implement the stable-release adapters and factory**

Add beside `LatestReleaseSource`:

```python
class StableReleaseCatalog(Protocol):
    def stable_versions(self) -> tuple[str, ...]: ...
```

Add `_CodexStableReleaseCatalog` and `_ClaudeStableReleaseCatalog`. Each owns one resolver, calls only `stable_versions()`, and catches only its resolver-specific exception to re-raise `ReleaseDiscoveryError(str(exc)) from exc`.

Implement:

```python
def default_stable_release_catalog(
    agent_name: str,
    *,
    cache_root: Path | None = None,
) -> StableReleaseCatalog:
    ...
```

Use `cache_root or default_agent_cache_root()`. Map `codex` and `claude`; refuse `antigravity` and unknown names. Do not change `default_latest_release_source()`.
- [ ] **Step 5: Run Task 2 GREEN tests and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_agent_releases.py tests/unit/test_release_monitor_flow.py
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/agents/releases.py tests/unit/test_agent_releases.py
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock/agents/releases.py
git diff --check
```

The monitor-flow test is intentional regression coverage proving the existing latest-release boundary is unchanged.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/qualock/agents/releases.py tests/unit/test_agent_releases.py
git commit -m "feat: add stable release discovery capability"
```

---

### Task 3: Trusted Agent-Aware Bisect Core

**Files:**
- Modify: `src/qualock/version_bisect/models.py`
- Modify: `src/qualock/version_bisect/commands.py`
- Modify: `tests/unit/test_version_bisect_models.py`
- Modify: `tests/unit/test_version_bisect_commands.py`
**Interfaces:**
- Consumes: Task 2 `StableReleaseCatalog`, `default_stable_release_catalog()`, existing `execute_check()` and `parse_agent_spec()`.
- Produces: `BisectAgent = Literal["codex", "claude"]` in `models.py`; `BisectPreflight(agent_name, baseline_version)`; `BisectOutcome.agent_name`; `OnStart(agent_name, baseline, upper, run_dir)`.

- [ ] **Step 1: Update test fixtures to carry trusted agent identity**

Change the command-test helper to default to Codex while allowing Claude:

```python
def patch_preflight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_name: BisectAgent = "codex",
    baseline: str = "0.151.0",
) -> None:
    monkeypatch.setattr(
        bisect_commands,
        "bisect_preflight",
        lambda root: BisectPreflight(agent_name=agent_name, baseline_version=baseline),
    )
```

Update existing `BisectOutcome(...)` constructors in model/command tests with `agent_name="codex"` so old Codex tests stay green once the field becomes required.

- [ ] **Step 2: Add failing preflight capability/identity tests**

Use monkeypatched project objects with `config.agent.name` and baseline locks to prove: Codex accepted, Claude accepted, config/lock mismatch rejected, Antigravity rejected, unknown agent rejected, and non-stable baseline rejected.

For capability refusals inject fail-if-called catalog/store/check seams and assert none are reached.
- [ ] **Step 3: Add failing upper-agent and Claude candidate tests**

Add tests proving a trusted Claude preflight with upper `claude@2.1.263` freezes the injected catalog and calls:

```python
[
    "claude@2.1.261",
    "claude@2.1.263",
]
```

for an all-PASS scan from baseline `2.1.260`. Add a BLOCK case proving the scan stops at the first blocking Claude version.

Add a cross-agent test (`claude` project + `codex@...` upper) asserting `CommandError` before `catalog.stable_versions()` is called. Preserve existing tests for `latest`, prerelease, malformed, unpublished, and non-newer upper bounds.

- [ ] **Step 4: Add failing callback/outcome identity tests**

Assert `on_start` receives exactly `(agent_name, baseline, upper, run_dir)` and `BisectOutcome.agent_name` equals the trusted preflight agent on every terminal path. Keep `on_step(step)` unchanged.

- [ ] **Step 5: Run Task 3 RED tests**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_version_bisect_models.py \
  tests/unit/test_version_bisect_commands.py
```

Expected: new identity/capability tests fail against the Codex-specific implementation.
- [ ] **Step 6: Implement trusted agent-aware bisect core**

In `models.py`, import `Literal` from `typing` and define:

```python
BisectAgent = Literal["codex", "claude"]
```

Add `agent_name: BisectAgent` to `BisectOutcome`.

In `commands.py`, replace the local catalog protocol/default Codex resolver construction with Task 2 imports. Extend `BisectPreflight` with `agent_name: BisectAgent`. Preflight must validate freshness, config/lock identity, supported capability, and stable baseline.

Parse `(upper_name, upper_version)` first, validate stable syntax, then preflight, then require `upper_name == context.agent_name`, then obtain/freeze the catalog. Build every candidate with:

```python
check_executor(root, f"{context.agent_name}@{version}")
```

Change `OnStart` to `Callable[[BisectAgent, str, str, Path], None]` and call it with trusted agent identity. Every `BisectOutcome` return includes `agent_name=context.agent_name`.

Do not change the `BisectSummaryStore` API in this task.

- [ ] **Step 7: Run Task 3 GREEN tests and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_commands.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/version_bisect/models.py src/qualock/version_bisect/commands.py tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_commands.py
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock/version_bisect/models.py src/qualock/version_bisect/commands.py
git diff --check
```
Expected: focused tests PASS, Ruff PASS, strict mypy PASS, diff-check PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/qualock/version_bisect/models.py src/qualock/version_bisect/commands.py \
  tests/unit/test_version_bisect_models.py tests/unit/test_version_bisect_commands.py
git commit -m "feat: make version bisect agent-aware"
```

---

### Task 4: Agent-Identifying Bisect Evidence v2

**Files:**
- Modify: `src/qualock/version_bisect/storage.py`
- Modify: `src/qualock/version_bisect/commands.py`
- Modify: `tests/unit/test_version_bisect_storage.py`
- Modify: `tests/unit/test_version_bisect_commands.py`

**Interfaces:**
- Consumes: Task 3 `BisectAgent`, trusted `BisectPreflight.agent_name`.
- Produces: `BisectSummaryStore.create/save(..., agent: BisectAgent, ...)`; all new JSON summaries use `schema_version: 2` plus `agent`.

- [ ] **Step 1: Write failing schema-v2 storage tests**

Update storage tests so `create()` and `save()` pass `agent="codex"` or `agent="claude"` and assert exact payload fields:

```python
assert summary["schema_version"] == 2
assert summary["agent"] == "claude"
```

Keep existing assertions for candidates, steps, last-known-good, first-bad, stop reason, and atomic replacement semantics.
- [ ] **Step 2: Write the historical-v1 immutability test**

Create a legacy run directory and write known bytes such as:

```python
legacy_bytes = b'{"schema_version":1,"bisect_id":"legacy"}\n'
legacy_path.write_bytes(legacy_bytes)
```

Use the store to create/save a different new run, then assert `legacy_path.read_bytes() == legacy_bytes`. Do not add a reader or migration path just to satisfy this test.

- [ ] **Step 3: Update command-test memory store fixtures to require agent**

Change `MemoryStore.create()` and `MemoryStore.save()` signatures in command tests to accept `agent: BisectAgent`. Record the received agent and add an execution assertion that Claude runs pass `"claude"` to create and every save.

- [ ] **Step 4: Run Task 4 RED tests**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_commands.py
```

Expected: storage/API tests fail because the current summary shape is v1 and store methods do not accept agent.

- [ ] **Step 5: Implement the v2 store contract**

Import `BisectAgent` from `version_bisect.models`. Add required `agent` keyword parameters to protocol, file-store methods, and `_payload()`. `_payload()` returns `"schema_version": 2` and `"agent": agent` while preserving all existing fields.

Update every `store.create()` and `store.save()` call in `execute_bisect()` to pass `agent=context.agent_name`. Never derive agent from `upper_spec`, version strings, or callback data.
- [ ] **Step 6: Run Task 4 GREEN tests and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_commands.py
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/version_bisect/storage.py src/qualock/version_bisect/commands.py \
  tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_commands.py
/home/pacmap/qualock-easy/.venv/bin/mypy --strict \
  src/qualock/version_bisect/storage.py src/qualock/version_bisect/commands.py
git diff --check
```

Expected: PASS with new v2 summaries and unchanged historical fixture bytes.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/version_bisect/storage.py src/qualock/version_bisect/commands.py \
  tests/unit/test_version_bisect_storage.py tests/unit/test_version_bisect_commands.py
git commit -m "feat: record agent in bisect evidence"
```

---

### Task 5: Agent-Aware Bisect CLI and Documentation

**Files:**
- Modify: `src/qualock/cli.py`
- Modify: `tests/unit/test_version_bisect_cli.py`
- Modify: `README.md`
**Interfaces:**
- Consumes: Task 3 `BisectOutcome.agent_name`, new four-argument `on_start`, existing `agent_display_name()`, Task 2 `ReleaseDiscoveryError`.
- Produces: agent-correct bisect rendering; stable-catalog failures exit 1; README support matrix/docs updated for Claude bisect.

- [ ] **Step 1: Update CLI test outcome helper for required agent identity**

Change the helper to accept `agent_name: BisectAgent = "codex"` and construct `BisectOutcome(agent_name=agent_name, ...)`. Update fake `on_start` invocation to four arguments.

Existing Codex output assertions must remain in place as regression coverage.

- [ ] **Step 2: Add failing Claude rendering tests**

Add CLI tests for Claude outcomes covering first bad and no-bad terminal paths. Assert literal output includes `Claude Code` for baseline/first-bad/last-known-good/no-bad wording and never prints `Codex` for those identity-bearing lines.

Also update the start-callback test so `_print_bisect_start("claude", "2.1.260", "2.1.263", run_dir)` prints:

```text
Baseline: Claude Code 2.1.260
Searching through: 2.1.263
```

Step lines remain `<version>  <VERDICT>` with no agent prefix.

- [ ] **Step 3: Add failing error-boundary tests**

Make `execute_bisect` raise `ReleaseDiscoveryError("catalog unavailable")` and assert exit 1 with literal text. Keep input/CommandError exit 3, BaselineStaleError exit 4, BLOCK exit 2, WARN/INCOMPLETE exit 4, unexpected operational exit 1.

Add a CLI mapping test that monkeypatches `execute_bisect` to raise `CommandError("version bisect is unavailable for Antigravity")`; assert exit 3 and the literal message. Task 3 command-layer tests already prove the real Antigravity preflight stops before catalog/store/check, so this CLI test verifies only exit mapping.
- [ ] **Step 4: Run Task 5 RED tests**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_version_bisect_cli.py
```

Expected: Claude rendering/callback/discovery tests fail against hard-coded Codex behavior.

- [ ] **Step 5: Implement agent-aware CLI rendering and error mapping**

Change the start callback to:

```python
def _print_bisect_start(agent_name: BisectAgent, baseline: str, upper: str, run_dir: Path) -> None:
    del run_dir
    display_name = agent_display_name(agent_name)
    console.print(f"Baseline: {display_name} {baseline}", markup=False)
    console.print(f"Searching through: {upper}\n", markup=False)
```

Change terminal rendering to accept a resolved `display_name: str` and replace hard-coded `Codex` labels with it.

Inside `bisect_command()`'s existing `try`, immediately after `execute_bisect(...)`, compute `display_name = agent_display_name(outcome.agent_name)`. Add `ReleaseDiscoveryError` to the operational exit-1 path. Keep `_render_bisect_terminal(outcome, display_name)` outside the `try` so its intentional exit 2/4 survives unchanged.

Do not add resolver-specific Claude/Codex catalog catches to the CLI; discovery errors are normalized at the agents boundary.
- [ ] **Step 6: Update README bisect support text**

In `### Find the first bad release`, change Codex-only wording to Codex/Claude support. Keep the forward-scan explanation and cost warning. Include examples for both agents, for example:

```bash
qualock bisect codex@0.160.0
qualock bisect claude@2.1.263
```

State explicitly that the upper agent must match the trusted project/baseline agent. In the Antigravity section, change the sentence saying bisect is Codex-only to say bisect is available for Codex and Claude Code but unavailable for Antigravity; GitHub PR workflow remains Codex-only.

- [ ] **Step 7: Run Task 5 GREEN tests and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_version_bisect_cli.py tests/unit/test_version_bisect_commands.py
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/cli.py tests/unit/test_version_bisect_cli.py
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock/cli.py
git diff --check
```

Expected: CLI/command tests PASS, Ruff PASS, mypy PASS, diff-check PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add src/qualock/cli.py tests/unit/test_version_bisect_cli.py README.md
git commit -m "feat: render agent-aware version bisect"
```

---
### Task 6: Whole-Branch Verification and Review

**Files:**
- Verify all files changed by Tasks 1-5.
- Do not add production behavior in this task unless a reviewer identifies a concrete defect; any reviewer fix uses one scoped fix wave followed by one scoped re-review.

**Interfaces:**
- Consumes: completed Batch #38 branch.
- Produces: fresh verification evidence and a whole-branch reviewer verdict suitable for the local-ready gate.

- [ ] **Step 1: Run the Batch #38 focused suite**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q \
  tests/unit/test_claude_resolver.py \
  tests/unit/test_agent_releases.py \
  tests/unit/test_version_bisect_models.py \
  tests/unit/test_version_bisect_commands.py \
  tests/unit/test_version_bisect_storage.py \
  tests/unit/test_version_bisect_cli.py
```

Expected: all focused tests PASS.

- [ ] **Step 2: Run the full suite**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests
```

Expected: no failures; skips must be reported exactly.
- [ ] **Step 3: Run static quality gates**

Compute the touched Python list from the merge-base and run Ruff on every touched Python file. Then run strict mypy on all touched source files, expected to include:

```text
src/qualock/agents/claude_resolver.py
src/qualock/agents/releases.py
src/qualock/version_bisect/models.py
src/qualock/version_bisect/commands.py
src/qualock/version_bisect/storage.py
src/qualock/cli.py
```

Commands:

```bash
/home/pacmap/qualock-easy/.venv/bin/ruff check <all-touched-python-files>
/home/pacmap/qualock-easy/.venv/bin/mypy --strict \
  src/qualock/agents/claude_resolver.py src/qualock/agents/releases.py \
  src/qualock/version_bisect/models.py src/qualock/version_bisect/commands.py \
  src/qualock/version_bisect/storage.py src/qualock/cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src tests
git diff --check $(git merge-base HEAD main)..HEAD
```

Expected: Ruff PASS, mypy PASS. If dependency traversal exposes the known missing-PyYAML-stub debt, only `src/qualock/canary/loader.py`, `src/qualock/config/io.py`, and `src/qualock/project_setup/config.py` may be waived; report them exactly. Compileall and diff-check must PASS.

- [ ] **Step 4: Run static scope/security assertions**

Use `git diff --name-only $(git merge-base HEAD main)..HEAD` and fail the gate if production changes include scheduler, `github_pr`, Antigravity adapter/resolver/runtime/auth, or dependency files.
The allowed production paths are limited to the six source files listed in this plan. README/spec/plan and the listed unit tests are allowed documentation/test changes. No `version_bisect/__init__.py` change is planned.

Also assert the release boundary remains Antigravity-free:

```bash
! grep -n "AntigravityResolver" src/qualock/agents/releases.py
! git diff --name-only $(git merge-base HEAD main)..HEAD | \
  grep -E '^(src/qualock/(scheduler|github_pr)|src/qualock/agents/antigravity|src/qualock/run/host|pyproject.toml)'
```

If either negative assertion produces a match, stop and inspect scope before declaring ready.

- [ ] **Step 5: Verify repository/worktree hygiene**

```bash
git status --short --branch
git log --oneline $(git merge-base HEAD main)..HEAD
```

Expected: worktree clean; only Batch #38 spec/plan/implementation commits are ahead of base. No SDD report, temporary prompt, credential artifact, downloaded agent, or generated cache file is tracked.

- [ ] **Step 6: Dispatch a fresh whole-branch reviewer**

Create a review package for `$(git merge-base HEAD main)..HEAD`. Give the reviewer the approved spec and this plan, make the seat read-only, and require findings classified Critical / Important / Minor plus a final `READY` or `NOT READY` assessment.

The reviewer must explicitly check: Codex regression safety, Claude catalog parsing, trusted-agent ordering, Antigravity fail-closed ordering, schema-v2 evidence, v1 immutability, CLI exit semantics, and out-of-scope file changes.

- [ ] **Step 7: Handle reviewer findings once**

If the whole-branch reviewer is `READY` with no findings, record the evidence and stop. If findings exist, run one TDD fix wave covering all actionable findings, commit it once, rerun all Step 1-4 gates, then run one scoped re-review over only the fix range. Do not start a second autonomous fix wave; report any residual issue to the user.
- [ ] **Step 8: Record local-ready state**

Report final HEAD SHA, focused/full test counts, Ruff/mypy/compile/diff results, reviewer verdict, worktree cleanliness, and any explicit ruling used during SDD.

Do not push or create a PR at this step. Wait for explicit user authorization such as `push + PR`.

---

## Implementation Order Rationale

1. Task 1 gives Claude the missing resolver capability without touching orchestration.
2. Task 2 exposes that capability through the same agent-level boundary Batch #37 established.
3. Task 3 makes bisect trust and execution agent-aware while leaving persistence unchanged for an isolated review gate.
4. Task 4 upgrades only new evidence to schema v2 and wires trusted identity through persistence.
5. Task 5 changes only presentation/error mapping/docs after the core contract is stable.
6. Task 6 proves the full branch and security boundaries before any external integration.

## Expected Commit Sequence

```text
docs: design agent-aware version bisect
docs: mark version bisect design approved
docs: plan agent-aware version bisect
feat: add Claude stable release catalog
feat: add stable release discovery capability
feat: make version bisect agent-aware
feat: record agent in bisect evidence
feat: render agent-aware version bisect
```

A single additional final-review fix commit is permitted only if Task 6 reviewer findings require it.