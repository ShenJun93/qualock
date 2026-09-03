# QuaLock Version Bisect Design

Date: 2026-09-03
Status: Approved design, implementation not started
Batch: #28
Branch: `feat/version-bisect`
Base: `cee40d6b5475f6496df2980692a3748ba4020934`

## Goal

Add a low-tech `qualock bisect codex@<upper-version>` command that identifies the first
**confirmed bad stable Codex release** after the currently locked QuaLock baseline.

The feature is intentionally conservative. Coding-agent quality is not assumed to regress
monotonically across releases, so V1 uses an ordered forward scan of published stable
versions rather than mathematical binary search. The command keeps the roadmap name
"bisect" because its user goal is to locate the first bad version in a release interval.

Each candidate is qualified through the existing `execute_check()` path. Version bisect is
an orchestration layer only; it does not create a second qualification engine, verdict
policy, canary runner, evidence format for individual checks, or baseline mechanism.

## User story

Given a fresh QuaLock baseline at Codex `0.150.0`, a user who knows that a later
stable release is problematic can run:

```bash
qualock bisect codex@0.160.0
```

QuaLock enumerates published stable Codex versions in `(0.150.0, 0.160.0]`, sorts them by
numeric semantic-version components, and qualifies them from oldest to newest.

Example:

```text
QuaLock Version Bisect

Baseline: Codex 0.150.0
Searching through: 0.160.0

0.151.0  PASS
0.152.0  PASS
0.153.0  BLOCK

FIRST BAD RELEASE
Codex 0.153.0

Last known good: 0.152.0
Technical evidence: .qualock/results/bisect-.../
```

If every release through the upper bound passes, QuaLock reports that no confirmed bad
release was found through that version.

## Definition of "first bad"

V1 defines a bad release narrowly as an existing QuaLock qualification whose suite verdict
is `BLOCK`.

The first bad release is therefore the earliest published stable version after the baseline
for which the normal `execute_check()` result is `BLOCK`, provided every earlier scanned
version returned `PASS`.

`WARN` is not silently treated as good or bad. If a scanned version returns `WARN`, the
search stops because QuaLock can no longer prove that a later `BLOCK` is the first bad
release. `INCOMPLETE` stops for the same reason.

This preserves the existing qualification policy:

- `PASS`: candidate matches the policy well enough to continue the scan;
- `BLOCK`: confirmed first bad release, stop with a regression finding;
- `WARN`: unresolved quality regression, stop without claiming a first bad release;
- `INCOMPLETE`: insufficient authoritative evidence, stop without claiming a first bad release.

No existing verdict thresholds or canary semantics change.

## Scope

V1 supports only:

- agent name `codex`;
- a currently valid, fresh QuaLock baseline lock;
- baseline versions in stable `X.Y.Z` form;
- an exact stable `codex@X.Y.Z` upper bound;
- published npm versions of `@openai/codex`;
- sequential execution;
- the current configured canary suite and existing qualification policy.

The command does not accept `codex@latest` in V1.
The upper bound is exact so a run has a reproducible declared endpoint even if npm
publishes another release while the scan is running.

## Non-goals

This batch does not add:

- binary-search assumptions over non-monotonic behavioral results;
- prerelease, beta, rc, nightly, or alternate dist-tag scanning;
- additional coding-agent adapters;
- parallel qualifications or result reuse/caching;
- automatic baseline movement or Codex updates;
- scheduler or monitor-triggered bisect integration;
- new canaries, graders, execution backends, or verdict policy;
- hosted history, dashboards, or GitHub PR qualification reports.

## Version catalog

`CodexResolver` gains a metadata-only stable-version catalog method using the equivalent of:

```text
npm view @openai/codex versions --json
```

Catalog retrieval must not install Codex. The JSON must be a list of strings. QuaLock
filters to exact stable `X.Y.Z`, deduplicates, converts each to numeric
`(major, minor, patch)` components, and sorts ascending.

Lexicographic ordering is forbidden because `0.9.0` must sort before `0.10.0`.
Timeouts, non-zero npm exits, malformed JSON, or non-list payloads raise
`CodexResolveError` before candidate qualification begins.

## Range validation

Before scanning, `execute_bisect()` performs all cheap validation first:

1. parse the requested spec and require `codex`;
2. require an exact stable `X.Y.Z` upper bound;
3. load the existing baseline lock;
4. require the locked baseline version to be stable `X.Y.Z`;
5. run the existing suite/config freshness checks;
6. fetch and freeze the stable published catalog;
7. require the exact upper bound to exist in that catalog;
8. require the upper bound to be numerically greater than the baseline;
9. select all published stable versions in `(baseline, upper]`.

The frozen list is authoritative for the entire command. New versions published after the
catalog fetch are not added mid-run.

The baseline itself need not remain present in npm metadata. Its trust remains governed by
the existing baseline lock and the binary fingerprint validation inside `execute_check()`.

## Execution algorithm

For each frozen candidate from oldest to newest, call:

```text
execute_check(root, "codex@" + version)
```

Each returned result is recorded before the next decision. `PASS` advances
`last_known_good` and continues. `BLOCK` stops with that candidate as `first_bad`. `WARN`
or `INCOMPLETE` stops unresolved and never examines later versions.

If all candidates pass, the outcome is "no confirmed bad release through upper bound".

Every candidate check reruns the locked baseline contemporaneously. This is intentionally
more expensive than sharing one baseline run, but it preserves the core QuaLock guarantee
and avoids a special bisect execution engine.

## Components

### `src/qualock/agents/resolver.py`

Extend `CodexResolver` with stable catalog retrieval and numeric stable-version ordering.
Existing exact-version `resolve()` behavior remains unchanged.

### `src/qualock/version_bisect/models.py`

Add immutable orchestration models:

- `BisectStep(version, qualification_id, verdict)`;
- `BisectStop`: `NO_BAD_FOUND`, `FIRST_BAD_FOUND`, `WARN_UNRESOLVED`, `INCOMPLETE`;
- `BisectOutcome`: bisect id, baseline, upper bound, ordered steps, last-known-good,
  optional first-bad, and stop reason.

The models reuse the existing `Verdict`; they do not define a second qualification policy.

### `src/qualock/version_bisect/commands.py`

Add `execute_bisect()` with injectable catalog provider and check executor for unit tests.
Production defaults use `CodexResolver` plus the existing `execute_check()` flow.

### `src/qualock/version_bisect/storage.py`

Write orchestration provenance under:

```text
.qualock/results/bisect-<UTC timestamp>-<random>/summary.json
```

The summary records schema version, bisect id, baseline, exact upper bound, frozen candidate
list, completed steps with qualification ids and verdicts, last-known-good, optional
first-bad, and terminal stop reason.

Write the initial summary before the first check, then atomically refresh it after each
returned qualification. Completed progress therefore remains discoverable if a later check
raises or the process is interrupted. Individual qualification evidence remains unchanged
in the normal `check-*` directories created by `execute_check()`.

### `src/qualock/cli.py`

Add:

```bash
qualock bisect codex@<exact-stable-version>
```

Output is progressive and low-tech: baseline and upper bound once, then one line per
completed candidate. V1 adds no `--technical`; qualification ids in `summary.json` point to
normal technical evidence.

## Exit behavior

- first `BLOCK` after an all-PASS prefix: report first bad and last known good; exit `2`;
- all candidates PASS: report no confirmed bad through upper; exit `0`;
- `WARN`: stop unresolved, do not claim first bad; exit `4`;
- `INCOMPLETE`: stop unresolved, do not claim first bad; exit `4`;
- invalid input/configuration: existing convention, exit `3`;
- stale/incomplete baseline safety condition: existing convention, exit `4`;
- npm/catalog resolution failure (`CodexResolveError`): exit `1`, matching existing resolver failure behavior;
- unexpected internal failure: exit `1`.

Using exit `4` for WARN means only that the **bisect search** is incomplete. The underlying
qualification remains an unchanged WARN result and its normal evidence is preserved.

## Safety invariants

The feature must preserve all of these properties:

1. Never write or replace `baseline.lock`.
2. Never change the configured canary suite, repetitions, or verdict policy.
3. Never classify `WARN` as PASS when locating a first bad release.
4. Never continue past `WARN` or `INCOMPLETE`.
5. Never claim first bad unless all earlier scanned versions returned PASS.
6. Never infer behavioral quality from npm metadata alone.
7. Never install a candidate merely to enumerate the catalog.
8. Never auto-update a user's globally installed Codex.
9. Never bypass baseline binary SHA-256 validation.
10. Never bypass suite/config freshness validation.
11. Never reuse old qualification results in V1.
12. Never mutate release-monitor or scheduler state.
13. Preserve normal per-check evidence exactly as `execute_check()` produces it.

## Errors and interruption

Known resolver, project, canary, baseline, and qualification errors stay as existing
exception types wherever possible; they must not be converted into false bisect outcomes.

If a candidate check raises instead of returning a `QualificationResult`, scanning stops
immediately. Already completed steps remain in `summary.json`, and no `first_bad` claim is
written.

Keyboard interruption follows normal CLI interruption behavior. A partial summary may show
a prefix of completed checks but must not imply a terminal conclusion.

## Testing strategy

Tests use TDD and must not hit npm, Docker, or real Codex installations.

### Resolver tests

Cover stable JSON parsing, prerelease exclusion, deduplication, numeric ordering such as
`0.9.0 < 0.10.0`, timeout/non-zero failures, malformed/non-list JSON, and proof that catalog
retrieval does not invoke npm install.

### Orchestration tests

Cover:

- interval excludes baseline and includes upper;
- upper must be stable, published, and newer than baseline;
- baseline version must be stable;
- exact ordered candidate specs reach the injected check executor;
- PASS continues and updates last-known-good;
- BLOCK stops immediately and reports the previous last-known-good;
- WARN and INCOMPLETE stop immediately without checking later versions;
- all PASS returns `NO_BAD_FOUND`;
- a raised check exception never fabricates first-bad;
- baseline/suite freshness failures happen before candidate execution;
- summary step order matches execution order.

### Storage and CLI tests

Cover initial frozen-range persistence, per-step refresh, terminal fields, absence of
`first_bad` for unresolved runs, complete JSON replacement, command wiring, progressive
output, and exit codes `0/2/3/4/1` for the cases defined above.

### Regression gate

Run the full existing test suite, scoped Ruff, strict mypy for touched source, compileall,
`git diff --check`, protected-path checks, and an independent whole-branch review before
any push or PR request.

## Documentation

Update README after implementation with a short section explaining:

- when to run `qualock bisect`;
- why V1 scans forward instead of assuming monotonic regressions;
- the exact-upper-bound requirement;
- PASS/BLOCK/WARN/INCOMPLETE stopping semantics;
- expected cost: every candidate performs a normal contemporaneous baseline-vs-candidate
  qualification.

Update ROADMAP to mark version bisect delivered only after implementation and review are
complete. The spec-only commit must not edit ROADMAP.

## Expected implementation footprint

Expected new files:

```text
src/qualock/version_bisect/__init__.py
src/qualock/version_bisect/models.py
src/qualock/version_bisect/commands.py
src/qualock/version_bisect/storage.py
tests/unit/test_version_bisect_models.py
tests/unit/test_version_bisect_commands.py
tests/unit/test_version_bisect_storage.py
tests/unit/test_version_bisect_cli.py
```

Expected modified files:

```text
src/qualock/agents/resolver.py
src/qualock/cli.py
tests/unit/test_agent_resolver.py
README.md
ROADMAP.md
```

The final implementation may combine tiny model/storage tests where clarity improves, but
the architectural boundaries above remain authoritative.

## Acceptance criteria

Batch #28 is complete only when:

1. an exact stable upper bound can be scanned from the locked stable baseline;
2. published stable versions are enumerated and numerically ordered correctly;
3. every candidate delegates to existing `execute_check()`;
4. scanning continues only across PASS results;
5. the earliest BLOCK after an all-PASS prefix is reported as first bad;
6. WARN and INCOMPLETE stop without a first-bad claim;
7. all-PASS intervals report no confirmed bad release;
8. summary provenance links every completed version to its qualification id;
9. existing baseline, monitor, scheduler, project-protection, and qualification behavior
   remains unchanged;
10. full local gates and independent whole-branch review pass on the exact final head;
11. remote CI, if a PR is later authorized, passes on that exact reviewed head.

## Authorization boundary

Approval of this design authorizes the local design/spec workflow only. The normal later
implementation workflow may proceed only after the written-spec review gate and plan.

No push, pull request creation/update, merge, tag, GitHub Release, or PyPI publication is
implied by approval of this design. Those external side effects require explicit user
authorization at the appropriate boundary.
