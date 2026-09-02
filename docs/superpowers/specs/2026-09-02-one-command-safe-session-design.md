# One-command Safe Session Design

**Date:** 2026-09-02  
**Branch:** `feat/start-safe-session`  
**Status:** Approved design, implementation not started

## Summary

Add a low-tech `qualock start` entrypoint that prepares a trustworthy project-protection baseline when necessary and then enters the existing foreground watch mode.

The product promise is:

> Start QuaLock, then let AI code.

`qualock start` is an orchestration layer only. It must reuse the existing project setup, signed project-protection, and watch subsystems. It must not create a fourth verification engine, weaken signed-lock integrity, install dependencies, or silently replace an existing baseline.

## User problem

QuaLock already has the required primitives:

- `qualock setup` detects the project, checks environment readiness, writes recommended protections, and establishes a signed known-good baseline;
- `qualock protect` establishes a signed baseline from explicitly configured protections;
- `qualock watch` authenticates an existing signed baseline and automatically verifies settled Git-visible edits.

The remaining onboarding gap is lifecycle knowledge. A new user still needs to know whether to run `setup`, `protect`, or `watch`.

V1 should reduce the normal workflow to:

```text
cd my-project
qualock start
```

The command decides only which existing primitive is appropriate for the current project state.

## Goals

V1 must:

1. add `qualock start`;
2. preserve existing `setup`, `protect`, `verify`, and `watch` behavior;
3. distinguish a project with an existing control artifact from one that is safe to bootstrap;
4. never treat an invalid, malformed, missing-key, or otherwise unusable existing signed baseline as permission to create a new baseline automatically;
5. preserve existing manually configured protections rather than replacing them with built-in packs;
6. use existing setup planning/readiness for projects without configured protections;
7. require explicit confirmation before trusting the current project state, unless `--yes` is supplied;
8. run a fresh normal watch startup after a baseline is established instead of passing bootstrap results into the watch engine;
9. stop before watch if the baseline cannot be safely established;
10. keep cancellation and readiness failures mutation-free where the existing setup contract is mutation-free.

## Non-goals

V1 does not add:

- a daemon, background process, service, or startup agent;
- desktop or IDE integration;
- agent-process detection;
- auto-fix or auto-revert;
- dependency installation or synchronization;
- browser recording;
- monorepo/workspace fan-out;
- notification delivery;
- a new protection/verification evidence format;
- a new signing mechanism;
- a special watch bootstrap mode that skips initial verification;
- automatic repair of invalid project locks or signing keys;
- automatic deletion/replacement of an existing project lock;
- a no-subcommand default mode for `qualock`.

## CLI

V1 adds:

```text
qualock start
qualock start --yes
qualock start --level minimal
qualock start --level recommended
qualock start --level strong
```

`--level` has the same `ProtectionLevel` semantics as `qualock setup`. It matters only when `start` needs to build a fresh setup plan. It does not rewrite existing manually configured protections and does not alter an already locked project.

`--yes` skips only the user confirmation required before establishing a new trusted baseline. It does not skip:

- environment readiness;
- project health/protection execution;
- signed-lock creation;
- watch control authentication;
- the watch initial verification.

## Primary UX

### Already protected project

```text
$ qualock start

QuaLock

Protected baseline found.
Checking signed protection control...

QuaLock Watch

[normal existing watch output]
```

The command does not run setup or protect before watch.

### Existing manual protections, no lock

```text
$ qualock start

QuaLock

Existing protections
- Tests still pass
- Build still works

No trusted baseline exists yet.

QuaLock will trust the project's CURRENT state only if every protected check passes.

Protect this state and start watching? [y/N]:
```

On acceptance:

```text
[normal protect result]

QuaLock Watch

[normal existing watch output]
```

The configured protections are used exactly as they are. Built-in setup packs are not generated or written.

### Fresh supported project

```text
$ qualock start

QuaLock

Detected: Python, pytest, uv, Git
Protection level: recommended

Environment
- OK: uv is available
- OK: project Python environment is ready

Recommended protection
- Tests still pass
- Python code still compiles
- Git patch has no whitespace errors

Protect the current state and start watching? [y/N]:
```

On acceptance, `start` delegates to the existing setup apply path and then starts normal watch mode.

### Environment not ready

```text
$ qualock start

QuaLock

Detected: Python, pytest, uv, Git

Environment
- OK: uv is available
- NEEDS SETUP: project Python environment is not ready

QuaLock did not change your project.

Recommended action:
Run: uv sync
Then run: qualock start
```

Exit `4`. No setup mutation occurs.

### Cancelled bootstrap

If the user declines a bootstrap confirmation:

```text
Start cancelled. No files changed.
```

Exit `0`.

No baseline or watch session is started.

## State model

The orchestration layer uses three user-facing start states:

```text
LOCKED
CONFIGURED_UNLOCKED
UNCONFIGURED
```

They mean:

### `LOCKED`

A project-lock directory entry is present at `.qualock/project.lock`.

This state is selected by directory-entry presence, not semantic validity. The state inspector must not parse or authenticate the lock in order to decide whether the project is locked.

A present lock that is malformed, has an invalid signature, references a missing key, is a symlink, is a directory, or otherwise cannot be used is still not an `UNCONFIGURED` project.

The existing watch control path decides whether the lock is trustworthy.

### `CONFIGURED_UNLOCKED`

No `.qualock/project.lock` directory entry is present, and the existing valid QuaLock config has at least one project protection.

The command must preserve those protections and establish the baseline with the existing `execute_protect()` path.

### `UNCONFIGURED`

No project-lock directory entry is present, and either:

- no `.qualock/config.yaml` exists; or
- the existing valid config has no project protections.

The command builds the existing setup plan and uses its protection-pack/readiness behavior.

A malformed existing config is not `UNCONFIGURED`. It is an error and must not be overwritten.

## Fail-closed control-artifact detection

State inspection must not use `Path.exists()` alone for `.qualock/project.lock`, because a dangling symlink returns false from normal existence checks.

Use an `lstat`-style directory-entry check:

```text
lstat(.qualock/project.lock)
    success -> LOCKED
    ENOENT  -> no lock entry
    ENOTDIR because .qualock is not a directory -> invalid project state, no bootstrap
    other unexpected I/O -> operational failure, no bootstrap
```

A dangling symlink therefore counts as `LOCKED`.

The command must never follow this decision rule:

```text
try to parse/authenticate lock
if that fails:
    assume fresh project
    establish new baseline
```

That pattern is explicitly forbidden.

## `.qualock` parent-state handling

If `.qualock` exists but is not a directory, `start` must stop before setup/protect mutation. It must not remove or replace the path.

If `.qualock/config.yaml` exists but cannot be read/parsed as a valid QuaLock config, `start` must stop with existing configuration/input semantics and must not call setup pack writing.

Other unrelated valid `.qualock` contents do not by themselves imply `LOCKED`.

## Architecture

Add a small orchestration package:

```text
src/qualock/project_start/
├── __init__.py
├── models.py
└── commands.py
```

Dependency direction:

```text
project_start
    ├── project_setup
    ├── project_protection
    └── project_watch

project_setup       ─┐
project_protection   ├─ must not import project_start
project_watch       ─┘
```

The existing subsystems remain independently usable and independently testable.

No existing protection, signing, readiness, or watch engine moves into `project_start`.

## Models

`models.py` defines immutable orchestration state.

Expected conceptual model:

```text
StartProjectState
- LOCKED
- CONFIGURED_UNLOCKED
- UNCONFIGURED
```

And a frozen plan/value object that carries only the information needed after the read-only preparation phase, for example:

```text
StartPlan
- state
- level
- setup_plan: SetupPlan | None
- configured_protections: tuple[ProjectProtectionConfig, ...]
```

Exact names may change during implementation if type boundaries become clearer, but the three-state semantics are fixed.

The plan must not contain signing secrets or persisted watch state.

## Two-phase orchestration

The start flow is intentionally split into:

```text
prepare
    ↓
render / confirm
    ↓
apply bootstrap if needed
    ↓
normal watch
```

### Preparation phase

Preparation is read-only with respect to QuaLock project state.

It may:

- inspect `.qualock` directory-entry state;
- load and validate existing config;
- call `build_setup_plan()` for an unconfigured project;
- run the existing passive project detector;
- run existing fixed readiness probes;
- inspect Git HEAD through the existing setup path.

It must not:

- write config;
- write `.gitignore`;
- create `.qualock`;
- write/remove `project.lock`;
- run project protections;
- create a signing key.

### Confirmation phase

No confirmation is required for `LOCKED`, because no new trust decision is being made.

`CONFIGURED_UNLOCKED` and `UNCONFIGURED` require confirmation unless `--yes` is supplied.

The confirmation text must make the trust boundary explicit: QuaLock is about to trust the current state only if the selected protections pass.

### Apply phase

For `CONFIGURED_UNLOCKED`:

```text
existing execute_protect(root)
```

For `UNCONFIGURED`:

```text
existing apply_setup_plan(root, setup_plan)
```

For `LOCKED`:

```text
no bootstrap mutation
```

## No-lock precondition re-check

A plan prepared as `CONFIGURED_UNLOCKED` or `UNCONFIGURED` is allowed to establish a new baseline only while `.qualock/project.lock` is still absent.

Immediately before any bootstrap mutation/protection execution, `project_start` must re-check the lock directory entry using the same fail-closed `lstat` semantics.

If a lock entry appeared after preparation/confirmation:

```text
abort
do not overwrite the lock
do not call setup/protect
do not enter watch through the stale plan
tell the user the project protection state changed and to run `qualock start` again
```

This is a state-transition guard, not a claim of an atomic filesystem transaction. V1 does not add a cross-process project lock. The command nevertheless must not knowingly apply a stale “unlocked” plan after detecting that the control state changed.

## Existing-lock path

For `LOCKED`, `qualock start` delegates directly to the existing watch startup.

It must not:

- load the current config to decide whether the lock is acceptable;
- regenerate protections;
- call `execute_protect`;
- call setup;
- rewrite `project.lock`;
- create/replace the signing key.

The watch subsystem remains authoritative for:

- missing signing key;
- malformed key;
- malformed signed lock;
- signature mismatch;
- valid but changed control during the session;
- initial verify PASS/FAIL/INCOMPLETE;
- Ctrl+C watch exit state.

An integrity failure is terminal for this start invocation. It never triggers bootstrap fallback.

## Existing configured protections path

For `CONFIGURED_UNLOCKED`, `start` must display the configured protection names and ask before establishing trust unless `--yes` is supplied.

After confirmation:

1. re-check that no lock directory entry appeared;
2. call the existing `execute_protect(root)`;
3. render the normal protect result/evidence location;
4. require `ProtectionStatus.PASS` and `lock_created=True`;
5. only then enter normal watch.

A FAIL or INCOMPLETE protection result does not start watch and does not create a baseline. It exits `4`, because the requested safe session could not establish a trustworthy baseline.

The command does not silently replace manual protections with recommended packs.

## Fresh setup path

For `UNCONFIGURED`:

1. call existing `build_setup_plan(root, level)`;
2. render the existing setup plan/readiness information;
3. if readiness is `NEEDS_SETUP`, exit `4` before confirmation and mutation;
4. ask for bootstrap confirmation unless `--yes`;
5. re-check no lock directory entry appeared;
6. call existing `apply_setup_plan(root, setup_plan)`;
7. render the normal protect result/evidence location;
8. require a PASS baseline;
9. enter normal watch.

The setup subsystem continues to own:

- capability detection;
- protection recommendation;
- runtime selection;
- environment readiness;
- config preservation;
- stale-lock cleanup on failed accepted setup.

`project_start` does not duplicate those rules.

## Fresh watch verification after bootstrap

After either bootstrap path establishes a signed baseline, `start` invokes normal `run_watch()`.

It does not pass the successful `ProjectProtectResult` into the watcher and does not skip the watch initial verify.

Therefore:

```text
protect/setup PASS
    ↓
signed project.lock
    ↓
normal watch startup
    ↓
authenticate/freeze signed control
    ↓
fresh execute_verify()
```

This deliberately runs protected checks again.

Reasons:

- the watch engine keeps one startup contract;
- the project may change between baseline establishment and watch startup;
- no special “trust the bootstrap result” branch is added;
- `qualock watch` remains independently testable.

Performance optimization of this duplicate first check is out of scope.

## Rendering responsibility

`project_start` owns orchestration data, not terminal presentation.

`src/qualock/cli.py` remains responsible for:

- printing the `QuaLock` start header;
- rendering setup-plan output with existing renderer;
- rendering configured protection names as literal-safe text;
- prompting for confirmation;
- rendering protect results with existing renderer;
- forwarding watch events through the existing watch event/result rendering path;
- mapping exceptions/statuses to CLI exits.

Reusable formatting helpers may be extracted only if needed to avoid copying existing CLI rendering logic. Such extraction must preserve existing `setup`, `protect`, and `watch` output semantics.

Project-controlled names must be printed with Rich markup disabled, consistent with existing Easy output safety.

## Exit semantics

`qualock start` uses these externally visible exits:

| Situation | Exit |
| --- | ---: |
| User cancels new-baseline confirmation | `0` |
| Watch ends after authoritative PASS | `0` |
| Watch ends after authoritative FAIL | `2` |
| Watch ends after authoritative INCOMPLETE/no authoritative result | `4` |
| Fresh setup environment is not ready | `4` |
| Configured/fresh baseline checks do not produce PASS + lock | `4` |
| Existing signed-control integrity failure | `4` |
| Project protection state changes between preparation and bootstrap apply | `4` |
| Missing required/invalid project input or malformed existing config | `3` |
| Operational/internal failure | `1` |

The existing `watch` startup mapping for its own specific error types should be reused rather than reinterpreted when possible.

If implementation discovers a current primitive whose exact exception maps differently, the plan must preserve its safety meaning and add explicit tests rather than silently swallowing it.

## Mutation guarantees

### Cancellation

When `CONFIGURED_UNLOCKED` or `UNCONFIGURED` is cancelled:

- no config changes;
- no `.gitignore` changes;
- no project lock changes;
- no signing-key creation;
- no project protection commands;
- no watch.

### Readiness failure

For the fresh setup path, existing setup readiness guarantees remain:

- no setup/config/lock mutation before readiness passes;
- no dependency installation.

### Existing control

For a project classified `LOCKED`, `start` never mutates the baseline before watch authentication.

### State changed before apply

If a lock appears after preparation, `start` aborts and preserves the newly observed control artifact.

## `--yes` contract

`--yes` means:

> If QuaLock can establish the selected protections successfully, I consent to trust the current state and begin the watch session.

It does not mean:

- ignore readiness;
- ignore failing protections;
- replace an existing lock;
- accept an invalid signature;
- install dependencies;
- skip watch initial verification;
- repair config;
- repair a key.

## Error handling

`project_start` should define only orchestration-specific errors, such as a state-changed-before-apply error.

Existing errors should keep their identity:

- `SetupUnsupportedError`;
- `SetupReadinessError`;
- `ConfigError`;
- `ProjectProtectionConfigError`;
- `ProjectProtectionError`;
- `ProjectLockIntegrityError`;
- `ProjectWatchSnapshotError`;
- `WatchControlChangedError`;
- filesystem errors where already part of existing CLI semantics.

Do not catch `Exception` at the orchestration boundary merely to fall back to another state.

There is no fallback from an attempted `LOCKED` watch to bootstrap.

## Security boundary

This feature improves safe lifecycle selection; it does not expand QuaLock's threat model.

Important properties:

- signed project lock remains the baseline authenticity mechanism;
- local signing key remains outside the project;
- existing warning that a process able to modify the user-level key is outside this hardening still applies;
- the start-state inspector is not a signature verifier;
- directory-entry presence is intentionally conservative to prevent “corrupt lock becomes fresh project” downgrade;
- the no-lock re-check narrows a preview/apply race but is not a cross-process atomic lock;
- setup/readiness and watch remain designed for trusted repositories, not arbitrary hostile code execution.

## Testing strategy

TDD must cover state classification, orchestration ordering, CLI rendering/exit behavior, and preservation of existing subsystem contracts.

### State classification

Tests must prove:

- regular `project.lock` entry -> `LOCKED`;
- valid signed-lock semantics are not required for `LOCKED` classification;
- malformed bytes still classify `LOCKED`;
- dangling symlink at `project.lock` classifies `LOCKED`;
- lock path that is a directory classifies `LOCKED`;
- `.qualock` replaced by a file is an error, never `UNCONFIGURED`;
- no lock + valid config with protections -> `CONFIGURED_UNLOCKED`;
- no lock + valid config without protections -> `UNCONFIGURED`;
- no lock + missing config -> `UNCONFIGURED`;
- malformed config -> error, no setup fallback.

### Locked path

Tests must prove:

- `LOCKED` calls watch without setup/protect;
- invalid signature/missing key from watch propagates fail-closed and does not trigger setup;
- watch PASS/FAIL/INCOMPLETE exit semantics propagate as `0/2/4`;
- no confirmation is requested.

### Configured-unlocked path

Tests must prove:

- existing protections are displayed and preserved;
- no setup pack generation/write occurs;
- cancellation is mutation-free;
- `--yes` skips only confirmation;
- before apply, a newly appeared lock aborts without overwrite;
- protect PASS + lock -> watch;
- protect FAIL -> no watch, exit `4`;
- protect INCOMPLETE -> no watch, exit `4`;
- watch performs its normal fresh initial verify after protect.

### Unconfigured path

Tests must prove:

- setup plan/readiness is reused;
- `NEEDS_SETUP` exits `4` without mutation or prompt;
- cancellation after READY is mutation-free;
- `--yes` still performs readiness and setup/protect;
- a lock appearing between plan and apply aborts without mutation/overwrite;
- setup PASS -> normal watch;
- setup cannot establish lock -> no watch, exit `4`;
- existing unrelated config keys survive through the existing setup writer.

### Integration preservation

Focused regression tests must include current:

- project setup flow;
- project protection flow;
- project watch CLI/engine flow.

No changes to qualification/baseline/canary execution behavior are expected.

## Files expected to change

New:

```text
src/qualock/project_start/__init__.py
src/qualock/project_start/models.py
src/qualock/project_start/commands.py
tests/unit/test_project_start_state.py
tests/unit/test_project_start_flow.py
```

Modified:

```text
src/qualock/cli.py
README.md
```

Potentially small shared CLI rendering helpers may be extracted only if duplication demands it. Such extraction must be listed explicitly in the implementation plan before code changes.

No changes are expected in:

```text
src/qualock/qualification/
src/qualock/run/
src/qualock/evidence/
```

The project protection, setup, and watch engines should remain behaviorally unchanged. A small reusable read-only helper may be added to an existing package only if the implementation plan demonstrates why it avoids duplicating a safety-sensitive rule.

## Review gates

Before merge:

1. TDD for every load-bearing state transition;
2. focused start/setup/protect/watch tests;
3. full repository pytest;
4. ruff on all new/changed code;
5. mypy strict on the new orchestration package;
6. compileall;
7. `git diff --check`;
8. scope audit confirming qualification/run/evidence behavior is untouched;
9. independent review with no Critical/Important findings;
10. exact-head GitHub CI on Python 3.11, 3.12, and 3.13;
11. squash merge with expected head SHA;
12. post-merge `main` CI verification.

## Definition of done

V1 is complete when a user can safely run:

```text
qualock start
```

and obtain exactly one of these outcomes:

- an existing signed baseline is authenticated and watched;
- existing explicit protections are successfully baselined, then watched;
- a fresh supported project is safely setup/baselined, then watched;
- the command stops without weakening trust because readiness, project health, config validity, control integrity, or orchestration state does not permit a safe session;
- the user cancels before a new trust decision and nothing is changed.

At no point may a failed existing control artifact be converted into permission to establish a replacement baseline automatically.
