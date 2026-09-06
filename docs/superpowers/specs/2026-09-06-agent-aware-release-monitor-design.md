# Agent-Aware Release Monitor Design

Date: 2026-09-06
Batch: #37
Status: Draft for user review
Base: `main@2c10e077392adcf0c374640a81480ef185d7ffb8`

## Goal

Make QuaLock release monitoring capability-based instead of Codex-specific, and enable the existing `qualock monitor` flow for Claude Code without weakening the Antigravity runtime contract.

The batch must preserve existing Codex behavior while making release discovery an explicit agent capability. Antigravity remains unsupported for release discovery because its current contract is local-binary-only: QuaLock does not discover, download, or install Antigravity releases.

## Non-goals

- Do not enable Antigravity `monitor`, `bisect`, or GitHub PR qualification.
- Do not enable Claude version bisect or GitHub PR qualification.
- Do not add `stable_versions()` to `ClaudeResolver` in this batch.
- Do not change qualification runtime, sandboxing, credential handling, or authentication flows.
- Do not change monitor state persistence schema or scheduler registration schema.
- Do not add dependencies, tags, releases, or publishing behavior.

## Current state

`qualock baseline`, `qualock check`, and `qualock doctor` are agent-aware for Codex, Claude Code, and Antigravity.

`qualock monitor` is still Codex-specific in four independent places:

1. `release_monitor.commands.monitor_preflight()` rejects every non-Codex baseline.
2. `_default_release_source()` constructs `CodexResolver` directly.
3. `execute_monitor()` builds `codex@<version>` for qualification.
4. CLI monitor rendering hard-codes the display name `Codex`.

The native scheduler calls `monitor_preflight()` when enabling a schedule and later runs only `qualock monitor`. Therefore monitor capability determines which configured agents can be scheduled; the scheduler itself does not own release discovery.

Codex exposes both `latest_version()` and `stable_versions()`. Claude already exposes `latest_version()` but not `stable_versions()`. Antigravity intentionally exposes neither release-discovery API and resolves only an existing local binary.

## Decision

Introduce a small, agent-level **latest-release discovery capability** and make release monitor depend on that capability instead of importing Codex directly.

This batch deliberately models only `latest_version()`. It does not introduce a broad catalog abstraction and does not require every agent to implement `stable_versions()`. That keeps monitor extensible without forcing Antigravity into a contract it cannot safely satisfy and without prematurely redesigning version bisect.

Supported monitor agents after this batch:

| Agent | Latest-release discovery | `qualock monitor` | Scheduled monitor |
| --- | --- | --- | --- |
| Codex | npm via existing `CodexResolver.latest_version()` | supported | supported |
| Claude Code | npm via existing `ClaudeResolver.latest_version()` | supported | supported |
| Antigravity | intentionally unavailable | fail closed | fail closed |

`bisect` and GitHub PR qualification remain Codex-only.

## Architecture

### 1. Shared latest-release source boundary

Create an agent-level module, recommended path:

`src/qualock/agents/releases.py`

It owns:

```python
class ReleaseDiscoveryError(RuntimeError):
    pass

class LatestReleaseSource(Protocol):
    def latest_version(self) -> str: ...

def default_latest_release_source(agent_name: str) -> LatestReleaseSource:
    ...
```

The factory maps:

- `codex` -> an adapter around `CodexResolver`
- `claude` -> an adapter around `ClaudeResolver`
- `antigravity` -> `ReleaseDiscoveryError` stating that release discovery is unsupported

The adapters translate resolver-specific discovery failures (`CodexResolveError`, `ClaudeResolveError`) into `ReleaseDiscoveryError`. This keeps `release_monitor` and the CLI from importing resolver-specific exception types.

The factory may accept an injectable cache root for tests, but production defaults must preserve the current `user_cache_dir("qualock")` location.

No Antigravity resolver, binary probe, authentication path, web lookup, or download may be invoked by this factory.

### 2. Monitor preflight

Extend `MonitorPreflight` to carry:

```python
@dataclass(frozen=True)
class MonitorPreflight:
    agent_name: str
    baseline_version: str
    baseline_sha256: str
```

`monitor_preflight(root)` continues to:

- load config and canaries;
- read `.qualock/baseline.lock`;
- verify suite/config freshness.

It additionally requires the trusted baseline agent to match the configured project agent. A mismatch raises `CommandError` before release discovery, state lookup, or qualification.

Capability rules:

- `codex`: allowed;
- `claude`: allowed;
- `antigravity`: `CommandError` with a clear message that release monitoring requires discoverable releases and Antigravity is local-binary-only.

This check is fail-closed and occurs before any release source is used, including injected test sources.

### 3. Monitor execution

`execute_monitor()` keeps its existing injectable `release_source` and `check_executor` seams.

When no release source is injected, it selects one with `default_latest_release_source(context.agent_name)`.

Version ordering continues to use `packaging.version.Version` for both baseline and discovered latest versions.

When a newer release needs qualification, the candidate is:

```python
f"{context.agent_name}@{latest}"
```

previously `codex@<latest>`.

The existing state machine remains unchanged:

- `NO_NEW_RELEASE[
- `ALREADY_QUALIFIED`
- `NO_DOWNGRADE[
- `CHECKED`

The existing `--force` behavior remains unchanged.

### 4. Monitor outcome and rendering

`MonitorOutcome` must carry the agent identity needed by the CLI, rather than forcing the CLI to assume Codex or reread trusted state for display-only purposes.

Recommended field:

```python
agent_name: str
```

Every outcome constructor must populate it.

CLI rendering uses the existing `agent_display_name()` helper:

- `codex` -> `Codex`
- `claude` -> `Claude Code`

The following currently hard-coded strings become agent-aware:

- baseline/latest lines;
- “new release found” qualification message;
- “no newer release” message;
- “already qualified” message;
- safety-summary display name;
- `--force` help text.

Codex wording and exit semantics should remain equivalent apart from any minimal wording generalization needed to share the renderer.

### 5. Scheduler consequence

`qualock schedule enable` already calls `monitor_preflight()` and the scheduled runner only invokes `qualock monitor`.

Therefore Claude scheduled monitoring becomes supported automatically when Claude monitor preflight becomes valid. This is an intentional consequence of the architecture, not a new scheduler subsystem.

No scheduler backend, registration, state, command shape, launchd/systemd/Task Scheduler integration, or stored schema changes.

Scheduler user-facing text that currently says the scheduled job “does not update Codex” must be generalized so it is correct for Claude as well.

Antigravity schedule enable must fail through the same monitor capability check before native scheduler installation.

## Error contract

Errors are separated by category:

### User/project/capability errors

Raise `CommandError`, preserving monitor CLI exit code 3:

- config/baseline agent mismatch;
- Antigravity release monitoring unsupported;
- malformed or missing project inputs already covered by the existing flow.

### Release-discovery operational errors

Raise `ReleaseDiscoveryError`, handled by the monitor CLI as operational exit code 1:

- npm latest-version query times out;
- npm command fails;
- resolver returns malformed latest-version data.

Resolver-specific error classes must not leak through the release-monitor public boundary.

### Qualification results

Existing behavior remains:

- BLOCK -> exit 2;
- INCOMPLETE -> exit 4;
- baseline stale -> exit 4;
- PASS/WARN follow existing monitor result handling.

No error path may silently fall back from Claude or Antigravity to Codex.

## State and compatibility

`MonitorState` remains unchanged. It stores baseline SHA, candidate version, verdict, qualification id, and completion time.

No new `agent_name` field is required in persisted monitor state because `baseline_sha256` identifies the complete trusted baseline lock, which already contains the agent identity. A baseline-agent change therefore changes the baseline digest and invalidates old monitor state matching.

Existing Codex monitor state remains readable.

`ScheduleRegistration` and scheduler state remain unchanged.

## Security invariants

- Release discovery must not authenticate to Codex, Claude, or Antigravity.
- Discovery may perform only the resolver's existing npm metadata query for Codex/Claude.
- No Antigravity binary is executed or inspected by monitor discovery.
- No Antigravity token/account/auth file is read, copied, hashed, serialized, or printed.
- No Windows Antigravity fallback is introduced.
- A discovered Claude release is qualified only through the existing `execute_check()` path and its existing Docker/runtime security contract.
- Tests for this batch must not run authenticated Claude or Antigravity agents.
- No dependency installation is permitted merely to satisfy local tooling gates.

## Testing strategy

Implementation uses strict TDD.

### Release source tests

Add tests proving:

 - Codex factory returns a latest-release source backed by Codex behavior.
- Claude factory returns a latest-release source backed by Claude behavior.
- Antigravity factory fails closed without constructing or probing `AntigravityResolver`.
- Codex/Claude resolver discovery failures are normalized to `ReleaseDiscoveryError`.
- Factory cache root/default construction preserves current cache semantics.

### Monitor preflight tests

Add or update tests proving:

- Codex fresh baseline still passes.
- Claude fresh baseline passes and returns `agent_name == "claude"`.
- config/baseline agent mismatch fails before release discovery/state access.
- Antigravity baseline fails before release discovery/state access.
- stale baseline behavior remains unchanged.

### Monitor flow tests

Prove:

- Claude baseline + newer Claude release calls `check_executor(root, "claude@<latest>")`.
- Claude no-new-release does not call qualification.
- Claude already-qualified/no-downgrade/force paths preserve existing state semantics.
- Codex candidate spec remains `codex@<latest>`.
- Antigravity cannot be made to run by injecting a fake release source.
- persisted state schema is unchanged.

### CLI tests

Prove:

- Claude monitor output says `Claude Code`, never `Codex`.
- Codex monitor output remains correct.
- `--force` help is agent-neutral.
- release discovery operational failure exits 1.
- unsupported Antigravity monitor exits 3.
- result exit codes for BLOCK/INCOMPLETE remain unchanged.

### Scheduler tests

Because scheduler enable imports `monitor_preflight()`, update tests for the expanded `MonitorPreflight` data shape and add direct coverage that:

- Claude preflight permits schedule enable to proceed to the scheduler backend seam.
- Antigravity preflight failure prevents scheduler backend installation.
- scheduler registration schema and status/disable behavior are unchanged.

### Repository gates

Run:

- focused unit tests for releases, monitor, CLI, scheduler;
- full `pytest`;
- Ruff on touched Python files;
- strict mypy on touched source files, without installing missing dependencies;
- `compileall` for `src` and `tests`;
- `git diff --check`.

Known pre-existing `types-PyYAML` mypy debt is not part of this batch.

## Expected files

Likely production changes:

 - Create `src/qualock/agents/releases.py`
- Modify `src/qualock/release_monitor/commands.py`
- Modify `src/qualock/release_monitor/models.py`
- Modify `src/qualock/cli.py`
- Possibly adjust exports under `src/qualock/agents/` or `release_monitor/` only if required
- No scheduler production change except agent-neutral user-facing wording if that wording lives in CLI

Likely tests/docs:

 - Create or extend tests for the release source factory
- Modify `tests/unit/test_release_monitor_flow.py`
- Modify relevant CLI monitor tests
- Modify `tests/unit/test_scheduler_commands.py`
- Update README support matrix/monitor/schedule wording

Exact file names may vary during implementation if the existing test organization makes a smaller change clearer, but the architecture and scope above are binding.

## Deferred follow-ups

### Batch #38 candidate: agent-aware version bisect

Build a separate stable-release catalog capability. Codex already has `stable_versions()`; Claude needs a deliberately specified implementation and tests before bisect can support it. Antigravity remains excluded unless its release-discovery contract changes.

### Batch #39 candidate: agent-aware GitHub PR qualification

Genericize proposed-lock trust validation and resolver selection separately. This remains a security-sensitive workflow and must not be folded into Batch #37 or #38.

## Acceptance criteria

Batch #37 is complete only when:

1. `qualock monitor` works for existing Codex projects with no functional regression.
2. `qualock monitor` works for Claude projects using Claude npm latest-release discovery and existing Claude qualification.
3. Antigravity monitor fails clearly and before release discovery or qualification.
4. `qualock schedule enable` inherits Claude monitor support without schema/backend changes and remains fail-closed for Antigravity.
5. User-facing monitor/schedule text is agent-correct.
6. Resolver-specific discovery exceptions do not leak through the monitor boundary.
7. Monitor and scheduler persisted schemas remain backward-compatible.
8. No authenticated real-agent test, Antigravity credential access, dependency install, tag, release, publish, or unrelated refactor occurs.
