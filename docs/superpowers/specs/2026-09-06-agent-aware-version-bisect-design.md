# Agent-aware Version Bisect — Design

Date: 2026-09-06
Batch: #38
Status: Draft for user review
Base: `main@d534b65f913326cc8d53805c6bf8e6268e2da07b`

## Goal

Make QuaLock version bisect capability-based instead of Codex-specific, and enable the existing `qualock bisect` flow for Claude Code without weakening the Antigravity runtime contract.

Batch #38 extends the agent-level release-discovery boundary created in Batch #37 with a deliberately narrower **stable-release catalog** capability. Codex and Claude Code can provide published stable versions through unauthenticated npm metadata. Antigravity remains excluded because its contract is local-binary-only and QuaLock must not discover, download, or install Antigravity releases.

The existing bisect algorithm remains a forward scan, not a binary search. Coding-agent regressions are not assumed to be monotonic.

## Non-goals

- Do not enable Antigravity version bisect.
- Do not enable Claude or Antigravity GitHub PR qualification.
- Do not change release-monitor or scheduler behavior.
- Do not unify `latest_version()` and `stable_versions()` into one broad mandatory capability.
- Do not change qualification runtime, Docker isolation, host isolation, credential handling, or authentication flows.
- Do not execute authenticated Claude or Antigravity processes in Batch #38 tests.
- Do not add dependencies or install missing development dependencies.
- Do not tag, release, publish, push, open a PR, or merge as part of implementation unless separately authorized.
- Do not migrate or rewrite historical bisect summary files.

## Current state

Batch #37 made release monitoring agent-aware through `qualock.agents.releases`, with `LatestReleaseSource`, `ReleaseDiscoveryError`, `default_latest_release_source()`, and shared `default_agent_cache_root()`.

Version bisect is still Codex-specific in seven places:

1. `_default_catalog()` always constructs `CodexResolver`.
2. `bisect_preflight()` rejects every non-Codex baseline.
3. `execute_bisect()` ignores the parsed upper agent name.
4. Every candidate is qualified as `codex@<version>`.
5. CLI bisect rendering hard-codes `Codex`.
6. CLI has a Codex-specific resolver-error branch.
7. Bisect evidence schema v1 records versions but not agent identity.

Codex already implements `stable_versions()`. Claude Code implements `latest_version()` but not `stable_versions()`.

A read-only design probe confirmed that `npm view @anthropic-ai/claude-code versions --json` returns a JSON list of published versions; the observed package versions are exact stable `X.Y.Z` strings. The probe did not execute Claude Code, authenticate, download, or install an agent.

## Decision

Introduce a small **stable-release catalog capability** beside the existing latest-release capability and make version bisect depend on it.

| Agent | Stable-release catalog | `qualock bisect` |
| --- | --- | --- |
| Codex | existing npm catalog | supported |
| Claude Code | new npm catalog | supported |
| Antigravity | intentionally unavailable | fail closed |

GitHub PR qualification remains Codex-only. The monitor boundary remains unchanged; Batch #38 does not merge latest-release and stable-release discovery into one mandatory interface.

## Architecture

### 1. Stable-release discovery boundary

Extend `src/qualock/agents/releases.py` with:

```python
class StableReleaseCatalog(Protocol):
    def stable_versions(self) -> tuple[str, ...]: ...


def default_stable_release_catalog(
    agent_name: str,
    *,
    cache_root: Path | None = None,
) -> StableReleaseCatalog:
    ...
```

Factory mapping:

- `codex` -> adapter around `CodexResolver`;
- `claude` -> adapter around `ClaudeResolver`;
- `antigravity` -> `ReleaseDiscoveryError`;
- unknown agent -> `ReleaseDiscoveryError`.

Adapters call only `stable_versions()` and normalize `CodexResolveError` / `ClaudeResolveError` to `ReleaseDiscoveryError`. The factory reuses `default_agent_cache_root()`.

The release-discovery module must not import or construct `AntigravityResolver`; Antigravity behavior is capability refusal only.

### 2. Claude stable-version discovery

Add `stable_versions()` to `src/qualock/agents/claude_resolver.py`.

It queries:

```text
npm view @anthropic-ai/claude-code versions --json
```

using the resolver's npm executable with a 30-second timeout.

It must:

1. reject timeout/nonzero exit with `ClaudeResolveError`;
2. parse stdout as JSON;
3. require a JSON list containing only strings;
4. keep only exact stable `X.Y.Z`;
5. deduplicate;
6. sort numerically by `(major, minor, patch)`;
7. return `tuple[str, ...]`.

Malformed JSON, non-list payloads, or non-string items raise `ClaudeResolveError`. Prerelease/build strings are excluded from an otherwise valid string list.

This method must not call `resolve()`, install npm packages, execute Claude, inspect credentials, or authenticate.

### 3. Trusted bisect preflight

Use `BisectAgent = Literal["codex", "claude"]` and extend `BisectPreflight` with `agent_name: BisectAgent` plus `baseline_version`.

`bisect_preflight(root)` must:

1. load config/canaries;
2. read `baseline.lock`;
3. run existing freshness validation;
4. require config agent == lock agent;
5. accept only `codex|claude`;
6. reject Antigravity clearly;
7. reject unknown agents;
8. require baseline exact stable `X.Y.Z`.

Freshness semantics remain authoritative. The explicit identity check is defense in depth for inconsistent or hand-edited state.

Capability refusal must occur before catalog access, summary creation, or candidate qualification, even with injected catalog/store/check seams.

### 4. Upper-bound trust and validation

`execute_bisect()` continues to accept `<agent>@<X.Y.Z>`.

Processing order:

1. parse upper spec;
2. require upper exact stable `X.Y.Z`;
3. run trusted preflight;
4. require upper agent == trusted agent;
5. only then access stable catalog.

Cross-agent upper specs fail with exit 3 before catalog. Antigravity baseline fails on capability before catalog. No fallback to Codex.

Existing checks remain: upper must be published and numerically newer than baseline. Error text must name the trusted agent rather than hard-code `codex@...`.

### 5. Catalog selection and candidate construction

Keep the existing injection seam:

```python
catalog: StableReleaseCatalog | None = None
```

If absent, select with `default_stable_release_catalog(context.agent_name)` and freeze one tuple of `stable_versions()` for the run.

Candidate selection remains `baseline < candidate <= upper`, with stable validation, dedupe, and numeric ordering.

Every candidate qualification is built from trusted preflight identity:

```python
result = check_executor(root, f"{context.agent_name}@{version}")
```

The parsed upper name is only equality-validated; it is not the source of truth. `execute_check()` and qualification backends remain unchanged.

### 6. Outcome identity

Extend `BisectOutcome` with `agent_name: BisectAgent`. Every return path carries `context.agent_name`.

Stop values remain unchanged: `NO_BAD_FOUND`, `FIRST_BAD_FOUND`, `WARN_UNRESOLVED`, `INCOMPLETE`.

### 7. Start callback identity

Change `on_start` from `Callable[[str, str, Path], None]` to `Callable[[BisectAgent, str, str, Path], None]`, with arguments `agent_name, baseline_version, upper_version, run_dir`.

`on_step` remains unchanged.

### 8. Agent-aware CLI rendering

Replace hard-coded Codex text with `agent_display_name()` for:

- baseline;
- first bad release;
- last known good;
- no-bad-found message.

Step lines remain version + verdict.

For terminal rendering, compute `display_name` inside `bisect_command()`'s existing `try` immediately after `execute_bisect()` returns, then pass it into `_render_bisect_terminal()`.

Do not put terminal rendering inside the broad `try`, because it intentionally raises `typer.Exit(2)` or `typer.Exit(4)`.

The start callback runs inside `execute_bisect()`, so any display-name `CommandError` remains inside the existing input-error boundary.

### 9. Discovery error boundary

`version_bisect` must not directly construct Codex or Claude resolvers for stable discovery.

Stable-catalog failures are exposed as `ReleaseDiscoveryError`; CLI maps that category to exit 1.

Resolver-specific catalog exceptions must not leak through the version-bisect public boundary.

Candidate qualification may retain its existing operational errors through the generic exit-1 path; Batch #38 does not redesign `execute_check()` error types.

## Evidence compatibility

### 10. New summary schema v2

New Batch #38 runs write:

```json
{
  "schema_version": 2,
  "agent": "claude",
  "bisect_id": "bisect-...",
  "baseline_version": "2.1.260",
  "upper_version": "2.1.263",
  "candidates": ["2.1.261", "2.1.263"],
  "steps": [],
  "last_known_good": "2.1.260",
  "first_bad": null,
  "stop_reason": null
}
```

Only shape changes are schema version `1` -> `2` and the new `agent` field. Agent must be trusted `codex|claude`, never inferred from versions or unvalidated CLI text.

### 11. Summary store API

Extend `BisectSummaryStore.create()` and `save()` with `agent`. `execute_bisect()` passes `context.agent_name` to create, every step save, and final save.

The file store writes v2 for all newly created runs and never infers agent identity from version strings.

### 12. Historical v1 evidence

Existing v1 files remain historical evidence. Batch #38 adds no summary reader, migration command, or rewrite.

Result layout remains `.qualock/results/<bisect-id>/summary.json`.

Fresh run directories remain unique with `exist_ok=False`, so normal execution cannot silently convert an old v1 run.

A storage test must create a pre-existing v1 file and prove it remains byte-for-byte unchanged.

## Error and exit contract

### Exit 3 — input/project/capability

Includes malformed upper spec, non-stable upper, cross-agent upper, config/lock mismatch, unsupported/unknown agent, Antigravity capability refusal, missing/malformed project input, unpublished upper, or upper not newer than baseline.

### Exit 4 — stale/inconclusive

Unchanged: stale baseline, candidate `WARN`, candidate `INCOMPLETE`.

### Exit 2 — regression confirmed

Unchanged: first candidate with `BLOCK`.

### Exit 1 — operational

Includes npm catalog timeout/nonzero, malformed catalog JSON, catalog operational failure, or unexpected qualification operational failure.

Stable-catalog failures are normalized as `ReleaseDiscoveryError`.

## Forward-scan semantics

Batch #38 does not change the algorithm:

1. freeze the stable catalog;
2. select `baseline < version <= upper`;
3. sort numerically;
4. scan every candidate in order.

For each qualification:

- `PASS`: advance `last_known_good`;
- `BLOCK`: record first bad and stop;
- `WARN`: stop unresolved with no first-bad claim;
- `INCOMPLETE`: stop unresolved with no first-bad claim.

If all candidates pass, stop with `NO_BAD_FOUND`.

No binary-search or monotonic-regression assumption is introduced.

## Security contract

### Codex and Claude

Stable discovery is unauthenticated npm metadata only. It does not execute an agent, authenticate, read credentials, install an agent, or mutate baseline.

Candidate qualification reuses `execute_check()` and existing Docker/runtime security.

### Antigravity

Antigravity remains unsupported for bisect.

Stable catalog/bisect must not:

- import or construct `AntigravityResolver` from the release boundary;
- execute `agy`;
- resolve an Antigravity local binary;
- inspect, read, hash, copy, serialize, or print credential/token contents;
- inspect Antigravity auth state;
- discover, download, or install Antigravity releases;
- use a Windows `.exe` fallback;
- retry unsandboxed.

Capability refusal must happen before catalog access, summary creation, or qualification.

The already-certified authenticated Antigravity contract is not rerun for Batch #38.

## Compatibility

### Codex

Existing Codex behavior remains semantically unchanged: exact stable upper, published/newer validation, forward ordering, `codex@<version>` qualification specs, stop/exit semantics, and result path.

CLI Codex wording remains substantively identical, produced through generic display-name logic. New Codex bisects write schema v2 with `"agent": "codex"`.

### Claude

Claude gains bisect for exact published stable releases.

Real candidates still use existing `execute_check()` and current Claude resolver/runtime minimum contract. Batch #38 introduces no bypass.

### Monitor and scheduler

No behavior change. `LatestReleaseSource` remains intact.

### GitHub PR qualification

Remains Codex-only. No `github_pr` production file is modified.

## Testing strategy

All behavior changes are TDD-first. No test requires authenticated Claude or Antigravity access.

### 1. Claude resolver stable catalog

Cover valid JSON, stable filtering, dedupe, numeric sorting, timeout, nonzero exit, malformed JSON, non-list payload, and non-string list item.

Use fake process results; implementation tests do not access live npm.

### 2. Stable-catalog boundary

Cover Codex/Claude mapping, resolver-error normalization, shared cache root, Antigravity/unknown fail-closed, and absence of `AntigravityResolver` from the releases module.

### 3. Bisect preflight

Cover Codex and Claude accepted, config/lock mismatch, non-stable baseline, Antigravity/unknown refused before injected seams, and stale baseline before catalog.

The mismatch fixture should preserve freshness when needed so the explicit identity guard is exercised instead of being masked by stale detection.

### 4. Upper validation

Cover agent match, mismatch before catalog, exact stable requirement, `latest`/prerelease/build/malformed rejection, published upper, and newer-than-baseline requirement.

### 5. Candidate execution

For Claude, prove `claude@<version>` calls, numeric ordering, first-BLOCK stop, WARN/INCOMPLETE unresolved stop, and full PASS scan.

For Codex, retain existing `codex@<version>` calls and stop behavior.

### 6. Callback and outcome identity

Cover `BisectPreflight.agent_name`, `BisectOutcome.agent_name`, new `on_start(agent, baseline, upper, run_dir)` values/order, and unchanged `on_step`.

### 7. Evidence

Cover schema-v2 Codex/Claude agent, create/save preservation, existing field semantics, atomic replace behavior, and a byte-identical pre-existing v1 file.

### 8. CLI

Cover:

- Codex wording remains substantively unchanged;
- Claude renders `Claude Code`;
- first-bad/no-bad/last-good labels are agent-aware;
- Antigravity capability error -> exit 3;
- upper-agent mismatch -> exit 3;
- `ReleaseDiscoveryError` -> exit 1;
- stale baseline -> exit 4;
- `BLOCK` -> exit 2;
- `WARN` / `INCOMPLETE` -> exit 4;
- unexpected operational failure -> exit 1.

### 9. Documentation

Update README bisect documentation to say Codex and Claude Code are supported, require the upper spec to match the trusted project agent, preserve the forward-scan explanation, keep Antigravity unavailable, and keep GitHub PR qualification Codex-only.

Do not rewrite historical Batch #37 specs to pretend Batch #38 existed earlier.

## Verification gates

Before Batch #38 is local-ready:

1. focused stable-catalog + version-bisect tests pass;
2. full suite passes;
3. Ruff passes on touched Python files;
4. strict mypy passes on touched source, allowing only explicitly known repo-wide debt if still exposed;
5. compileall passes;
6. `git diff --check` passes;
7. static scope/security assertions pass;
8. a fresh whole-branch reviewer finds no blocking issue.

Static assertions must verify at minimum:

- no production `github_pr` file changed;
- no scheduler production file changed;
- no Antigravity resolver/auth/runtime production file changed;
- `qualock.agents.releases` does not import `AntigravityResolver`;
- no dependency file changed;
- no authenticated-agent test was added to normal test execution.

## Expected production files

Likely:

- `src/qualock/agents/claude_resolver.py`
- `src/qualock/agents/releases.py`
- `src/qualock/version_bisect/commands.py`
- `src/qualock/version_bisect/models.py`
- `src/qualock/version_bisect/storage.py`
- `src/qualock/cli.py`

Possible export-only change:

- `src/qualock/version_bisect/__init__.py` only if existing public API patterns require it.

Likely tests/docs:

- `tests/unit/test_claude_resolver.py`
- `tests/unit/test_agent_releases.py`
- `tests/unit/test_version_bisect_commands.py`
- `tests/unit/test_version_bisect_models.py`
- `tests/unit/test_version_bisect_storage.py`
- `tests/unit/test_version_bisect_cli.py`
- `README.md`

No scheduler, monitor, GitHub PR, Antigravity runtime/resolver, dependency, release, or publishing file belongs in scope.

## Deferred follow-up

### Batch #39 candidate: agent-aware GitHub PR qualification

Genericize proposed-lock trust validation and resolver selection separately under its own security-sensitive design.

## Acceptance criteria

Batch #38 is complete only when:

1. existing Codex bisect has no functional regression;
2. Claude projects can bisect exact published stable Claude releases;
3. stable-release discovery is an explicit Codex/Claude capability under `qualock.agents.releases`;
4. Claude stable catalog uses unauthenticated npm metadata only;
5. Antigravity fails before catalog, summary, or qualification;
6. upper spec agent must match trusted project agent;
7. candidate specs use trusted preflight identity;
8. CLI bisect output is agent-correct;
9. new summaries use schema v2 and trusted agent identity;
10. historical v1 evidence is not migrated or rewritten;
11. resolver-specific stable-catalog errors normalize to `ReleaseDiscoveryError`;
12. monitor, scheduler, GitHub PR qualification, and Antigravity runtime/auth behavior remain unchanged;
13. no authenticated-agent run, dependency install, tag, release, publish, push, PR, or merge occurs without separate authorization.

## Binding clarification: shared type ownership

`BisectAgent = Literal["codex", "claude"]` is owned by `src/qualock/version_bisect/models.py`.

`commands.py`, `storage.py`, CLI code, and tests import the alias from the models module as needed. No module may define a second incompatible bisect-agent alias or import the alias from `commands.py`; this avoids a `commands`/`models` dependency cycle.
