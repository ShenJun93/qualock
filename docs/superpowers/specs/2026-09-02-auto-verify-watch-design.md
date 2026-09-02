# Foreground Auto-Verify Watch Design

**Date:** 2026-09-02  
**Status:** Approved in chat; written-spec review pending  
**Branch:** `feat/auto-verify-watch`

## Goal

Add a foreground `qualock watch` mode that automatically reruns the existing signed project verification after meaningful project edits settle.

The product promise is:

> Start QuaLock, let the AI edit, and automatically learn whether protected behavior still works.

This batch adds a trigger/orchestration layer only. The existing signed project lock, `execute_verify`, protection runner, evidence format, and PASS/FAIL/INCOMPLETE semantics remain the source of truth.

## Non-goals

This batch does not add:

- a background daemon or startup service;
- an IDE/editor extension;
- agent-process detection;
- automatic fixes or reverts;
- desktop notifications;
- browser recording;
- monorepo traversal or workspace fan-out;
- a native filesystem-watcher dependency such as `watchdog`;
- agent-release monitoring from the repository roadmap;
- a second verification engine.

`qualock watch` exists only while its foreground terminal process is running.

## User experience

Start:

```text
$ qualock watch

QuaLock Watch

Signed protection lock verified.

Checking current project...

SAFE TO KEEP

Watching for changes...
```

After a burst of edits:

```text
Changes detected...
Waiting for edits to settle...
Checking protected behavior...

SAFE TO KEEP

Watching for changes...
```

Regression:

```text
DON'T KEEP THIS CHANGE

Tests still pass: FAIL

Watching for the next change...
```

Environment/protection incomplete:

```text
CHECK COULD NOT FINISH

Watching for the next change...
```

The terminal output is append-only in V1. QuaLock does not clear the terminal or require an interactive TUI.

## Command surface

V1 adds one command:

```bash
qualock watch
```

There are no public polling/debounce flags in this batch.

Internal defaults:

- poll interval: 0.5 seconds;
- settle interval: 1.0 second;
- maximum consecutive unstable verification cycles before yielding INCOMPLETE: 2.

These values are internal constants/configuration objects so tests can replace them without sleeping in real time.

## Architecture

```text
qualock watch
      |
      v
authenticate frozen watch control state
      |
      v
stable initial execute_verify()
      |
      v
Git-aware project snapshot loop
      |
      +--> control-plane integrity check every poll
      |
      +--> project snapshot changed?
                 |
                 v
             settle timer
                 |
                 v
          stable execute_verify()
                 |
                 +--> project changed during verify -> retry
                 |
                 +--> stable result -> publish + continue
```

The subsystem is isolated under `qualock.project_watch`.

Recommended files:

- `src/qualock/project_watch/models.py` — immutable snapshot/control/watch-state models.
- `src/qualock/project_watch/snapshot.py` — Git-aware project discovery and metadata snapshot.
- `src/qualock/project_watch/control.py` — signed-lock authentication and frozen watch-control identity.
- `src/qualock/project_watch/engine.py` — foreground polling/debounce/verification state machine.
- `src/qualock/project_watch/render.py` — watch-specific plain-language messages.
- `src/qualock/cli.py` — command wiring and exit-code mapping.
- focused unit tests under `tests/unit/`.

No watch state is persisted to disk.

## Project snapshot

### File discovery

Each poll obtains the project file set from Git:

```text
git ls-files -z --cached --others --exclude-standard
```

This gives:

- tracked paths, including tracked paths deleted from the working tree;
- untracked non-ignored files;
- no ignored files.

After parsing the NUL-separated result, QuaLock excludes repository-local control/output paths:

- `.git/`
- `.qualock/`

`.qualock/project.lock` is intentionally excluded from the ordinary project snapshot because it is monitored separately as control-plane state.

V1 is root-repository only. It does not recursively discover nested repositories or monorepo workspaces.

### File identity

For every discovered path, the watcher records metadata using `lstat`, not file contents:

- relative path;
- present/missing state;
- file mode/type;
- size;
- `mtime_ns`.

Missing files remain represented, allowing tracked deletions to trigger a change.

Using metadata avoids hashing every source file twice per second. This watcher is a convenience trigger, not a security boundary. Signed-lock authentication and the protection checks themselves remain the security/correctness boundary.

A file that is modified while deliberately preserving all watched metadata may not trigger V1 watch mode. This limitation is acceptable for the trusted-development-repository scope and must be documented.

### Snapshot failure

If Git file discovery times out, exits nonzero, or cannot run, the watcher can no longer make its monitoring promise. It stops with an operational error rather than silently continuing.

Transient `FileNotFoundError` between Git discovery and `lstat` is represented as a missing file. Other unexpected filesystem errors stop the watcher.

## Frozen watch control state

Watch mode must not silently adopt a new protection baseline while it is running.

At startup, QuaLock:

1. requires `.qualock/project.lock`;
2. loads the configured local signing key;
3. authenticates the signed project lock using existing project-protection I/O/signing code;
4. computes SHA-256 over the raw on-disk `project.lock` bytes after successful authentication;
5. stores that digest as the frozen control identity for this watch session.

Every poll re-authenticates the current signed lock before checking project changes.

The watcher stops fail-closed if:

- the lock disappears;
- the signing key disappears or becomes malformed;
- the HMAC no longer verifies;
- the authenticated raw lock digest differs from the startup digest.

A newly valid, intentionally re-protected lock is still a different watch-control identity. The current watch session stops and tells the user to restart `qualock watch`. This prevents a trusted baseline from changing silently underneath a running watch session.

If an attacker can modify both the repository lock and QuaLock's user-level signing key consistently, the existing signed-lock threat boundary still applies; watch mode does not claim to strengthen that boundary.

## Initial verification

Watch mode never begins by assuming the current tree is safe.

Startup order:

1. authenticate and freeze watch-control identity;
2. capture project snapshot A;
3. call existing `execute_verify`;
4. re-authenticate the frozen control identity;
5. capture project snapshot B.

If A equals B, the verification is stable and becomes the first authoritative watch result.

If the project changes during initial verification, the result is not promoted as authoritative. QuaLock waits for the edits to settle and retries using the same instability rules as later checks.

A stable initial result may be PASS, FAIL, or INCOMPLETE. All three enter watch mode:

- PASS -> show `SAFE TO KEEP`;
- FAIL -> show `DON'T KEEP THIS CHANGE`;
- INCOMPLETE -> show `CHECK COULD NOT FINISH`.

A missing project lock keeps current invalid-input behavior. Signing/integrity failure keeps current integrity behavior.

## Polling and debounce state machine

The engine keeps:

- the last observed stable project snapshot;
- optional pending changed snapshot;
- timestamp of the most recent observed change;
- last authoritative verify result;
- consecutive unstable-verification count.

Polling behavior:

1. sleep until the next poll using an injected clock/sleeper;
2. re-authenticate frozen watch control;
3. capture current project snapshot;
4. if it equals the observed/pending snapshot, continue;
5. if it differs, record it as pending and reset the settle timestamp;
6. do not verify until the pending snapshot stays unchanged for the settle interval;
7. once settled, run a stable verification cycle.

A burst of many edits therefore produces one verification after the burst settles.

## Stable verification cycle

Before verification, capture `pre_verify_snapshot`.

Run the existing `execute_verify(root, ...)`.

After it returns:

1. re-authenticate the frozen control identity;
2. capture `post_verify_snapshot`;
3. compare pre and post snapshots.

If they are equal:

- the verify result is authoritative for that tree;
- render it using the existing project-verify result semantics;
- set it as the last authoritative result;
- reset instability count;
- set the observed snapshot to the stable post snapshot;
- resume polling.

If they differ:

- do not render the PASS/FAIL/INCOMPLETE result as authoritative;
- print a bounded message such as `Project changed while QuaLock was checking; checking again after edits settle.`;
- preserve the evidence already written by `execute_verify`;
- treat the post snapshot as a new pending edit;
- wait for settle and retry.

This prevents a stale `SAFE TO KEEP` message for a tree that changed during the check.

## Protection commands that modify the project

Some user-defined checks or build commands can themselves modify watched non-ignored files.

To avoid an infinite verify loop, V1 allows at most two consecutive verification cycles that end with a changed project snapshot. The first unstable cycle schedules one settled retry.

If that retry is also unstable:

- report `CHECK COULD NOT FINISH`;
- explain that protected checks keep modifying watched files;
- treat this as an INCOMPLETE watch state;
- use the latest post-verification snapshot as the new observed baseline;
- continue watching for a subsequent project change rather than immediately invoking a third verification.

This does not create or change the signed project lock.

Ignored build outputs do not enter the project snapshot and therefore do not cause this condition.

## Control-plane changes during verification

Control integrity is checked both before and after every verification.

Any lock/key authentication failure or lock digest change is fatal for the watch session. Unlike an ordinary project regression or incomplete protection command, the watcher does not continue after the trusted watch control state changes.

Reason: continuing would make it unclear which protection baseline the watch session is enforcing.

## Rendering

Watch-specific messages wrap existing project verification rendering.

Rules:

- use `console.print(..., markup=False)` for project-controlled/display text;
- reuse `render_verify_terminal` or the same status-to-message mapping rather than inventing a new verdict model;
- do not print raw signing keys;
- do not dump full command stdout/stderr in the default watch view;
- evidence remains available under `.qualock/results/` exactly as with manual `verify`.

Suggested watch framing messages:

- `QuaLock Watch`
- `Signed protection lock verified.`
- `Watching for changes...`
- `Changes detected...`
- `Waiting for edits to settle...`
- `Checking protected behavior...`
- `Project changed while QuaLock was checking; checking again after edits settle.`
- `Protection baseline changed. Restart qualock watch after intentionally protecting a new baseline.`

## Exit semantics

Ordinary FAIL or INCOMPLETE verification does not stop the watcher.

`Ctrl+C` stops the foreground session and returns an exit code derived from the last authoritative watch state:

| Last authoritative state | Exit code |
| --- | ---: |
| PASS | 0 |
| FAIL | 2 |
| INCOMPLETE | 4 |
| no authoritative result yet | 4 |

Fatal startup/control errors keep established semantics:

- missing required project lock / invalid input: 3;
- signed-lock/key integrity failure: 4;
- watcher/Git operational failure: 1.

A fatal control-plane change while already watching exits 4.

If `Ctrl+C` arrives during a verification attempt, the session returns the code for the previous authoritative result. If no previous authoritative result exists, it returns 4.

## Testability

The core watch engine must not depend directly on real wall-clock sleeping.

Inject or parameterize:

- monotonic clock;
- sleeper/poll driver;
- project snapshot source;
- control-state authenticator;
- verifier callable.

Unit tests use deterministic fake snapshots/times and do not wait 0.5/1.0 real seconds.

CLI integration tests may patch the engine and verify rendering/exit mapping separately.

## Required TDD coverage

At minimum:

1. initial verify happens before the watcher claims it is watching;
2. stable initial PASS/FAIL/INCOMPLETE all enter watch mode;
3. missing/invalid signed lock preserves current exit semantics;
4. tracked file modification triggers verification;
5. tracked deletion triggers verification;
6. new untracked non-ignored file triggers verification;
7. ignored file does not trigger verification;
8. `.qualock/results` writes do not trigger verification;
9. `.git` and ordinary `.qualock` paths are excluded from project snapshots;
10. lock tampering is detected even when no project file changed;
11. signing-key removal/change stops watch fail-closed;
12. a valid but different newly signed lock stops watch and requires restart;
13. multiple edits within settle time debounce into one verify;
14. edit during verify suppresses stale result and schedules another check;
15. two consecutive verification-induced project changes become INCOMPLETE instead of looping forever;
16. regression result continues watching;
17. incomplete result continues watching;
18. later PASS replaces earlier FAIL/INCOMPLETE as the last authoritative result;
19. `Ctrl+C` returns 0/2/4 according to last authoritative state;
20. snapshot/Git operational failure stops instead of silently continuing;
21. project-protection evidence and signed-lock schemas remain unchanged;
22. qualification/run/evidence engine diff remains empty except ordinary use of existing `execute_verify`.

## Compatibility

This batch does not change:

- `.qualock/config.yaml` schema;
- `.qualock/project.lock` schema or signing format;
- project protection definitions;
- `protect` behavior;
- manual `verify` behavior;
- evidence JSON schema;
- coding-agent qualification baseline/check behavior;
- Docker/canary qualification engine.

`qualock watch` calls the same existing verification path that manual `qualock verify` uses.

## Security and trust boundary

Watch mode improves ergonomics, not the core threat model.

Its metadata polling can miss adversarial edits that preserve watched metadata. A later manual `verify` remains available at any time.

The signed project lock continues to protect repository-local baseline tampering as documented. QuaLock still does not protect against a process that can also rewrite the user-level signing key and re-sign arbitrary lock contents.

Watch mode is intended for trusted development repositories and normal AI coding edits, not arbitrary hostile repositories or hostile local processes.

## Scope after this batch

A later batch may evaluate:

- `qualock guard -- <agent command>` for deterministic CLI-agent lifetimes;
- native filesystem notifications for large repositories;
- desktop/IDE notifications;
- background service mode;
- per-workspace/monorepo watch;
- automatic agent release monitoring.

None of those are prerequisites for this V1 foreground watch mode.
