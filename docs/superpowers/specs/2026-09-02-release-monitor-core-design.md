# Release Monitor Core Design

**Date:** 2026-09-02
**Branch:** `feat/release-monitor-core`
**Status:** Approved design, implementation not started

## Summary

Add a one-shot `qualock monitor` command that answers the next low-tech question after QuaLock has a trusted Codex baseline:

> Has a newer Codex release appeared, and if so, is it safe for this repository?

`qualock monitor` is a thin orchestration layer. It discovers the current npm `latest` Codex version without installing it. If there is a newer release that has not already received an authoritative qualification for the current project baseline, it delegates to the existing `execute_check()` qualification path and renders the existing QuaLock safety result.

The command is intentionally one-shot and sequentially idempotent. Batch #26 does not install a daemon, cron job, launch agent, Windows scheduled task, or GitHub workflow. A later scheduler batch can invoke this command safely on a cadence.

## User problem

QuaLock already supports:

- `qualock baseline codex@X.Y.Z` to pin a known-good Codex release;
- `qualock check codex@latest` to resolve and qualify the current npm release;
- repository-specific canaries and contemporaneous baseline/candidate execution;
- PASS/WARN/BLOCK/INCOMPLETE evidence and low-tech safety summaries.

The remaining workflow still depends on the user remembering to check for new agent releases. A low-tech user should not need to repeatedly ask npm whether Codex changed or manually decide whether the same candidate has already been qualified.

V1 reduces the manual release loop to:

```text
cd my-project
qualock monitor
```

When nothing changed, the command is cheap and does not download a candidate. When a genuinely new release appears, it uses the existing qualification engine exactly once for that release/baseline state unless the user explicitly asks to rerun it.

## Goals

V1 must:

1. add one-shot `qualock monitor` and `qualock monitor --force`;
2. discover npm `latest` metadata without installing Codex;
3. validate that the current baseline/config/canary suite is fresh before using monitor state to suppress work;
4. never auto-upgrade or rewrite `baseline.lock`;
5. never create a second qualification engine;
6. delegate new-release qualification to existing `execute_check(root, "codex@<exact-version>")`;
7. preserve existing PASS/WARN/BLOCK/INCOMPLETE qualification semantics and evidence;
8. suppress repeated sequential qualification of an already-authoritatively-qualified release for the same fresh baseline;
9. retry INCOMPLETE qualifications on a later monitor invocation rather than marking them complete;
10. keep monitor/deduplication state outside the repository;
11. make monitor-state loss/corruption cause extra work, never a fabricated PASS or baseline change;
12. expose enough stable one-shot behavior for a later scheduler layer to invoke without reimplementing release discovery or qualification policy.

## Non-goals

Batch #26 does not add:

- a daemon, background service, resident process, or filesystem watcher;
- cron, systemd timers, launchd, Windows Task Scheduler, or GitHub Actions installation;
- notifications, email, Slack, desktop alerts, or webhooks;
- automatic baseline advancement after PASS/WARN;
- automatic agent installation or user environment upgrade;
- qualification of arbitrary historical versions;
- release-channel selection beyond npm `latest`;
- prerelease-channel monitoring;
- multiple agent adapters;
- version bisect;
- hosted state, dashboards, accounts, or team policy;
- cross-process monitor locking;
- a new canary, grader, execution backend, report format, or verdict policy.

## CLI

V1 adds:

```text
qualock monitor
qualock monitor --force
```

`qualock monitor` has no candidate argument. The candidate source is the npm `latest` dist-tag for the agent named in the current v0.1 baseline, which must be Codex.

`--force` bypasses only the "already authoritatively qualified for this baseline" deduplication decision. It does not:

- bypass baseline/config/canary freshness;
- qualify a release older than or equal to the baseline;
- bypass resolver validation;
- change the baseline;
- convert INCOMPLETE into a completed monitor result;
- change qualification verdict or exit semantics.

## User-visible flows

### No newer release

```text
$ qualock monitor

QuaLock Release Monitor
Baseline: Codex 0.151.0
Latest:   Codex 0.151.0

No newer Codex release needs qualification.
```

Exit `0`.

If npm `latest` is older than the pinned baseline, QuaLock must not run a downgrade qualification:

```text
Baseline: Codex 0.152.0
Latest:   Codex 0.151.0

Your baseline is newer than npm latest. No downgrade qualification was run.
```

Exit `0`.

### New release

```text
$ qualock monitor

QuaLock Release Monitor
Baseline: Codex 0.151.0
Latest:   Codex 0.152.0

New Codex release found. Qualifying 0.152.0 against baseline 0.151.0.

[existing QuaLock safety summary]
```

The exact candidate passed to the engine is `codex@0.152.0`, never `codex@latest`. Discovery and qualification are therefore separated: npm metadata determines the version first; only the existing qualification path may install/cache the exact candidate when it executes.

### Already qualified release

For a terminal authoritative verdict recorded for the same project + fresh baseline identity:

```text
$ qualock monitor

QuaLock Release Monitor
Baseline: Codex 0.151.0
Latest:   Codex 0.152.0

Codex 0.152.0 was already qualified for this baseline: BLOCK
Run `qualock monitor --force` to qualify it again.
```

No resolver install and no qualification engine call occurs in this path.

The exit code is derived from the recorded terminal verdict:

- PASS -> `0`;
- WARN -> `0`;
- BLOCK -> `2`.

This is a deduplication/status convenience only. It does not alter or authenticate `baseline.lock`.

### Previous INCOMPLETE

INCOMPLETE is not a terminal deduplication record. A later invocation sees the release as needing qualification again:

```text
previous monitor -> INCOMPLETE, exit 4
next monitor     -> retries the same exact latest release
```

This permits transient auth, runner, infrastructure, or validity problems to recover naturally.

### Force rerun

If the current latest release is newer than the baseline and already has PASS/WARN/BLOCK monitor state:

```text
qualock monitor --force
```

runs the existing exact-version qualification again and replaces the terminal monitor record only after the new qualification returns PASS/WARN/BLOCK.

If latest is not newer than baseline, `--force` remains a no-op. It is not a downgrade/same-version qualification switch.

## Architecture

Add a small package:

```text
src/qualock/release_monitor/
    __init__.py
    models.py
    state.py
    commands.py
```

Responsibilities are deliberately separated:

- `models.py` — immutable monitor outcomes and operational state schema;
- `state.py` — project-key derivation plus read/write of per-user deduplication state;
- `commands.py` — read-only preparation, version decision, existing `execute_check()` delegation, and terminal-state update;
- `cli.py` — rendering and exit-code mapping only.

A minimal public read-only latest-version API is added to the Codex resolver rather than duplicating the npm command in `release_monitor`.

## Release discovery without installation

Today `CodexResolver.resolve("latest")` performs two logically different operations:

1. query npm for the latest version;
2. resolve/install/cache the native Codex binary for that version.

Monitor needs operation 1 without operation 2.

Expose the public read-only interface `CodexResolver.latest_version() -> str`.

It reuses the existing npm command and version validation:

```text
npm view @openai/codex version
```

`resolve("latest")` must delegate to this same method so there remains exactly one npm-latest parsing rule.

The discovery method must never create the version cache directory and must never execute `npm install`.

For testability, release-monitor orchestration consumes a `ReleaseSource` protocol with exactly one operation: `latest_version() -> str`. The production default is the existing `CodexResolver` instance configured with QuaLock's normal user cache root.

## Fresh baseline preflight

Monitor must not let stale deduplication state hide a project whose canary/config suite changed after the last qualification.

Before making any state-based no-op decision, V1 validates the same local qualification context used by `execute_check()`:

```text
load current project config + canaries
read .qualock/baseline.lock
assert suite fingerprint matches baseline.lock
assert config fingerprint matches baseline.lock
require baseline agent == codex
```

Monitor preparation calls the existing public rules directly: `load_project()`, `read_baseline_lock()`, `suite_fingerprint()`, `config_fingerprint()`, and `assert_suite_fresh()`. The actual `execute_check()` call performs its normal freshness check again when a qualification is required. No new freshness policy is introduced and `execute_check()` is not refactored in this batch.

This preflight does not resolve/install agent binaries and does not execute canaries.

Missing baseline continues to use missing-input semantics. Suite/config mismatch continues to use existing `BaselineStaleError` semantics.

## Baseline identity

Operational deduplication is bound to the parsed baseline state, not merely the baseline version string.

Compute `baseline_sha256` from canonical JSON of the already parsed `BaselineLock` (`model_dump(mode="json")`, sorted keys, compact separators, UTF-8), then hash those bytes with SHA-256. This binds state to the same parsed object used for freshness validation and avoids a second baseline-file read solely for hashing.

Only after the baseline has parsed successfully and freshness checks have passed may this fingerprint participate in monitor state.

Re-baselining normally changes at least `created_at` and therefore invalidates the prior monitor record. Any semantic change to the parsed baseline changes the fingerprint. Pure whitespace/key-order edits that parse to the same baseline do not invalidate deduplication.

## Project identity and state location

Monitor state must live outside the repository so ordinary AI edits to `.qualock/` cannot directly mark a release as already checked.

Use exactly:

```text
Path(user_state_dir("qualock")) / "release-monitor" / "projects" / f"{project_key}.json"
```

Compute `project-key` as SHA-256 of `os.path.normcase(str(root.resolve()))` encoded as UTF-8. This provides stable normalization on the current platform without storing the raw project path in the filename. The raw project path does not need to appear in the filename.

Moving/copying the project produces a different key and therefore causes conservative requalification. Deleting user state also causes conservative requalification.

The monitor state is explicitly **not a trust artifact**. It is only a cost/deduplication hint. QuaLock's trust remains the repository baseline lock + actual qualification evidence/policy.

## Monitor state schema

V1 state stores only the last authoritative candidate for the current baseline identity:

```text
schema_version: 1
baseline_sha256: <hex>
agent: codex
candidate_version: 0.152.0
verdict: pass | warn | block
qualification_id: check-20260902T120000Z-a1b2c3d4
completed_at: <UTC ISO-8601 timestamp generated when state is written>
```

INCOMPLETE is intentionally not valid as a terminal stored verdict.

The state does not contain signing material, baseline authority, canary definitions, or executable commands.

## State read policy

State is operational and fail-safe for trust:

- missing state -> treat as no previous terminal qualification;
- unreadable/malformed/unknown-schema state -> ignore for deduplication and run a real qualification when otherwise needed;
- baseline fingerprint mismatch -> ignore old state;
- different candidate version -> do not suppress a genuinely newer version;
- current latest lower than a previously recorded candidate -> do not automatically run a downgrade qualification;
- `--force` -> ignore matching terminal candidate state only.

An ignored malformed state is accompanied by a monitor-state warning and is replaced after the next terminal qualification if persistence succeeds.

State corruption must never produce a synthetic PASS. The only no-op verdict reuse accepted by V1 comes from a successfully parsed monitor record matching the fresh current baseline identity and exact candidate version.

This remains an operational convenience rather than a cryptographic boundary; a process with access to the user's QuaLock state directory is outside the V1 repository-edit threat boundary.

## Version ordering

Use `packaging.version.Version`, already available in the project dependencies, for ordering validated Codex versions.

Let:

```text
B = baseline version
L = npm latest version
R = recorded terminal candidate version, if matching baseline state exists
```

Normal monitor decision:

```text
if L <= B:
    NO_NEW_RELEASE
elif matching state exists and L == R:
    ALREADY_QUALIFIED
elif matching state exists and L < R:
    NO_DOWNGRADE
else:
    QUALIFY L
```

`--force` changes only the `L == R` branch to `QUALIFY L`. It does not change `L <= B`.

A missing/ignored state does not prevent `QUALIFY L` when `L > B`.

## Orchestration model

Use immutable outcomes such as:

```text
MonitorAction:
- NO_NEW_RELEASE
- NO_DOWNGRADE
- ALREADY_QUALIFIED
- CHECKED

MonitorOutcome:
- action
- baseline_version
- latest_version
- qualification_result: QualificationResult | None
- recorded_verdict: Verdict | None
- state_persisted: bool | None
- state_warning: str | None
```

The orchestration API is `execute_monitor(root: Path, *, force: bool = False, release_source: ReleaseSource | None = None, state_store: MonitorStateStore | None = None, check_executor: CheckExecutor = execute_check) -> MonitorOutcome`.

The injected interfaces are fixed:

- `ReleaseSource.latest_version() -> str`;
- `MonitorStateStore.load(root: Path) -> tuple[MonitorState | None, str | None]`, returning `(state, warning)`;
- `MonitorStateStore.save(root: Path, state: MonitorState) -> None`, raising on persistence failure;
- `CheckExecutor(root: Path, candidate_spec: str) -> QualificationResult`.

The implementation plan may introduce private helper functions, but public orchestration behavior remains one release-source dependency, one state-store dependency, and exact delegation to the existing checker.

## Qualification delegation

For `QUALIFY L`, V1 calls exactly:

```text
execute_check(root, f"codex@{L}")
```

No monitor-specific backend, repetition policy, resolver install policy, schedule, canary runner, grader, evidence writer, or verdict calculator is added.

The monitor must not pass `codex@latest` into `execute_check()`, because that would perform a second latest-resolution step and create a discovery/qualification race. The candidate version is frozen to the exact version discovered before qualification begins.

The existing check result remains authoritative.

## Terminal state update

After `execute_check()` returns:

- PASS -> write/replace terminal monitor state;
- WARN -> write/replace terminal monitor state;
- BLOCK -> write/replace terminal monitor state;
- INCOMPLETE -> do not create/replace terminal monitor state.

Existing qualification evidence is written by `execute_check()` before monitor state handling.

Monitor state writes should be atomic within the state directory (temporary file + replace) so interruption does not normally leave half-written JSON.

## State write failure

A failure to persist deduplication state after a completed qualification must not rewrite or downgrade the real qualification verdict.

V1 behavior:

```text
real qualification result remains authoritative
render/return PASS, WARN, or BLOCK normally
surface a warning that monitor state could not be saved
next invocation may repeat the same expensive qualification
```

Exit status remains determined by the actual qualification verdict, not by operational state persistence.

This preserves safety and evidence semantics while making state-store availability a cost concern rather than a false qualification failure.

## State load failure

Unreadable/corrupt monitor state must not block a new qualification or cause exit `1` by itself. Treat it as absent for deduplication, set `state_warning`, render that warning, and proceed with real discovery/qualification logic.

A fundamental failure to determine the per-user state location before a path can be formed is an operational error and exits `1`.

## Rendering

New monitor-specific rendering is limited to:

- header;
- baseline/latest versions;
- no-new/no-downgrade/already-qualified messages;
- monitor-state warning when `state_warning` is present;
- a short transition message before qualification.

When a real qualification runs, reuse the same low-tech safety-summary builder/rendering path as `qualock check` rather than inventing a monitor report format.

Extract a private CLI helper that renders a `QualificationResult` through the existing `load_project()` display-name lookup, `build_safety_summary()`, and `render_safety_terminal()` path. Both `check_command` and `monitor_command` use that helper; `check` output must remain byte-for-byte equivalent for the same result.

## Exit codes

V1 preserves the existing CLI vocabulary:

| Outcome | Exit |
|---|---:|
| No newer release | `0` |
| No downgrade qualification | `0` |
| Previously qualified PASS | `0` |
| Previously qualified WARN | `0` |
| Previously qualified BLOCK | `2` |
| Fresh qualification PASS | `0` |
| Fresh qualification WARN | `0` |
| Fresh qualification BLOCK | `2` |
| Fresh qualification INCOMPLETE | `4` |
| Missing baseline, invalid config/canary input, unsupported non-Codex baseline | `3` |
| Stale baseline suite/config | `4` |
| Malformed baseline model, discovery/runner/unexpected operational failure | `1` |

A monitor-state save warning does not override an authoritative qualification exit code.

## Safety invariants

V1 must preserve all of the following:

1. `baseline.lock` is never modified by monitor.
2. npm metadata discovery never installs Codex.
3. Only an exact discovered version is passed into the existing qualification engine.
4. Existing suite/config freshness rules are checked before monitor state can suppress qualification.
5. A changed/recreated baseline invalidates prior deduplication state.
6. INCOMPLETE never becomes a terminal "already qualified" record.
7. BLOCK remains BLOCK on repeated no-op monitor invocations.
8. State deletion/corruption causes requalification, not PASS.
9. A state-write failure cannot change a real qualification verdict.
10. `--force` cannot bypass stale baseline or create same-version/downgrade qualification.
11. No auto-baseline or auto-upgrade path exists.
12. Qualification, grading, evidence, source preparation, Docker execution, and canary behavior remain delegated to existing code.

## Concurrency boundary

Batch #26 guarantees sequential idempotence, not a cross-process transaction.

Two `qualock monitor` processes started at the same time may both decide a release is new and both run qualifications with different qualification IDs. Atomic state replacement prevents partial JSON but does not serialize expensive checks.

This is acceptable for the one-shot core. Batch #27's scheduler should avoid overlapping scheduled runs. A future cross-process monitor lock may be added only if real scheduler behavior requires it.

## Threat boundary

Release-monitor state is deliberately weaker than project baseline trust.

Protected/trusted facts remain established by:

- current project canaries/config;
- `baseline.lock` freshness and binary fingerprint validation inside existing qualification;
- actual baseline/candidate runs;
- existing policy and evidence.

Monitor state only answers "may QuaLock skip re-running this exact terminal candidate for this exact fresh baseline?"

An ordinary AI edit confined to the repository should not be able to directly edit the per-user state file. A process with arbitrary access to the user's home/state directories remains outside this V1 hardening boundary.

## Compatibility and migrations

No project config schema or baseline schema changes are required.

The new user-state JSON has its own schema version. Unknown versions are ignored for deduplication rather than migrated in V1.

Existing users can upgrade without setup changes. The first `qualock monitor` invocation has no state and therefore performs normal discovery and, if needed, one real qualification.

## Testing strategy

TDD must cover the load-bearing boundaries.

### Discovery

- `latest_version()` returns validated npm latest without install/cache mutation;
- malformed npm output -> resolver error;
- npm timeout/nonzero -> resolver error;
- `resolve("latest")` continues to reuse the same latest parser and existing install behavior.

### Freshness/preflight

- missing baseline -> input failure;
- malformed baseline model -> existing `check` generic failure semantics (`1`);
- suite fingerprint changed -> `BaselineStaleError`;
- config fingerprint changed -> `BaselineStaleError`;
- non-Codex baseline -> unsupported/input failure;
- no release/state suppression decision occurs before freshness succeeds.

### Version decision

- latest == baseline -> no qualification;
- latest < baseline -> no downgrade;
- latest > baseline, no state -> qualify exact latest;
- latest == matching terminal state -> dedupe;
- latest < matching recorded newer terminal candidate -> no downgrade;
- latest > recorded terminal candidate -> qualify new latest;
- `--force` reruns matching newer candidate;
- `--force` does not rerun same/older-than-baseline candidate.

### Delegation

- qualification receives exact `codex@X.Y.Z`, never `codex@latest`;
- PASS/WARN/BLOCK returned unchanged;
- INCOMPLETE returned unchanged;
- monitor does not construct/run backend, canaries, graders, or evidence directly.

### State

- path is outside project and keyed by normalized absolute root;
- matching baseline/candidate terminal state dedupes;
- baseline fingerprint mismatch does not dedupe;
- missing state does not dedupe;
- malformed/unknown state does not dedupe;
- PASS/WARN/BLOCK are persisted;
- INCOMPLETE is not persisted;
- atomic replacement leaves valid state;
- save failure preserves authoritative qualification result and surfaces warning.

### CLI

- no-new output and exit `0`;
- no-downgrade output and exit `0`;
- already PASS/WARN exit `0`;
- already BLOCK exit `2`;
- new PASS/WARN exit `0` with existing safety summary;
- new BLOCK exit `2` with existing safety summary;
- new INCOMPLETE exit `4` with existing safety summary;
- stale baseline exit `4`;
- input/config failure exit `3`;
- discovery/operational error exit `1`;
- state warning is literal-safe and does not alter verdict exit.

### Preservation

Run existing baseline/check qualification suites unchanged. Confirm zero intended behavior diff in:

```text
src/qualock/qualification/
src/qualock/run/
src/qualock/evidence/
src/qualock/source/
```

Any shared read-only preflight or CLI rendering extraction must have regression tests proving existing `baseline`/`check` behavior and output remain unchanged.

## Files and scope

Expected new files:

```text
src/qualock/release_monitor/__init__.py
src/qualock/release_monitor/models.py
src/qualock/release_monitor/state.py
src/qualock/release_monitor/commands.py
tests/unit/test_release_monitor_state.py
tests/unit/test_release_monitor_flow.py
tests/unit/test_release_monitor_cli.py
```

Expected modifications:

```text
src/qualock/agents/codex.py
src/qualock/cli.py
README.md
```

No `src/qualock/commands.py` refactor is required: monitor performs its own read-only preflight with the existing public freshness helpers, and any real qualification delegates to `execute_check()`, which rechecks freshness.

No behavior changes are expected in qualification policy, grader/canary execution, evidence formats, source preparation, or Docker/run execution.

## Review gates

Before merge:

1. TDD for discovery, freshness, state, orchestration, and CLI exit behavior;
2. focused release-monitor + existing resolver/check tests;
3. full repository pytest;
4. ruff on all new/changed code;
5. mypy strict on the new monitor package and modified typed helpers;
6. compileall;
7. `git diff --check`;
8. scope audit confirming qualification/run/evidence/source execution behavior is untouched;
9. independent review with no unresolved Critical/Important findings;
10. exact-head GitHub CI on Python 3.11, 3.12, and 3.13, including existing Docker tmpfs smoke where configured;
11. squash merge with expected head SHA;
12. post-merge `main` CI verification.

## Definition of done

Batch #26 is complete when a user with a fresh Codex baseline can run:

```text
qualock monitor
```

and obtain exactly one of these safe outcomes:

- no newer release exists, so no candidate is downloaded or checked;
- npm latest is not newer than the baseline, so no downgrade/same-version qualification runs;
- a previously terminally qualified latest release is reported without repeating expensive qualification;
- a genuinely newer release is frozen to an exact version and qualified through the existing checker;
- INCOMPLETE is reported and remains eligible for retry;
- stale/missing baseline context stops according to existing semantics;
- monitor operational state failure causes extra work/warning rather than false trust;
- the baseline remains unchanged in every path.

The resulting command is a stable one-shot primitive for Batch #27 scheduled release checks without adding scheduler/platform complexity to this batch.
