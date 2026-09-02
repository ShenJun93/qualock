# Foreground Auto-Verify Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a foreground `qualock watch` command that monitors meaningful Git-visible project edits, debounces bursts, reruns the existing signed `execute_verify`, suppresses stale results when the tree changes during verification, and fails closed on watch-control changes.

**Architecture:** Add an isolated `qualock.project_watch` subsystem. Git-aware metadata snapshots decide when verification is needed; frozen signed-lock control identity prevents a watch session from silently adopting a new baseline; a deterministic engine with injected clock/sleep/snapshot/verify callables owns polling, settling, stability retries, and authoritative-result tracking. The existing project-protection engine remains unchanged and is the only source of PASS/FAIL/INCOMPLETE evidence.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, `hashlib`, `os`, `pathlib`, `time`, existing Typer/Rich CLI, existing `run_process`, existing signed project-lock I/O/signing, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-auto-verify-watch-design.md`

## Global Constraints

- `qualock watch` is foreground-only; no daemon, service, IDE extension, notification subsystem, or background process manager.
- Add no native filesystem watcher dependency such as `watchdog`.
- Project discovery uses exactly `git ls-files -z --cached --others --exclude-standard`.
- Ordinary project snapshots exclude `.git/` and `.qualock/`; `.qualock/project.lock` is monitored separately as frozen signed control state.
- File identity is metadata only: relative path, present/missing, mode/type, size, and `mtime_ns`; do not hash project file contents.
- Watch startup authenticates the existing signed lock and freezes SHA-256 of its raw bytes.
- Every poll re-authenticates the lock and requires the raw digest to match the startup digest.
- Missing/malformed key, invalid signature, missing lock, or valid-but-different signed lock stops the watch session fail-closed.
- Initial verification is mandatory; PASS, FAIL, and INCOMPLETE all may become authoritative and enter watch mode.
- Never publish a verify result when the project snapshot changed during that verify cycle.
- At most two consecutive unstable verification cycles are allowed; the first unstable cycle schedules one settled retry, and a second consecutive unstable cycle yields an authoritative synthetic INCOMPLETE watch state instead of looping.
- Burst edits use internal defaults of 0.5-second polling and 1.0-second settle time; no public tuning flags in V1.
- FAIL and INCOMPLETE results do not stop watch mode.
- Ctrl+C exits according to the last authoritative state: PASS=0, FAIL=2, INCOMPLETE=4.
- Existing manual `protect`/`verify`, signed-lock schema, project-protection evidence schema, qualification fingerprints, and qualification engine remain unchanged.
- V1 is root-repository only; no monorepo traversal.
- Unit tests inject clock/sleep/snapshot/verify dependencies and do not perform real-time sleeps.

---

## File Structure

- `src/qualock/project_watch/models.py` — immutable file/project snapshots, frozen control identity, watch timing config, and watch outcome/event models.
- `src/qualock/project_watch/snapshot.py` — Git-visible file discovery plus metadata snapshot capture.
- `src/qualock/project_watch/control.py` — authenticate the signed project lock and compare it with the frozen startup digest.
- `src/qualock/project_watch/engine.py` — stable verification cycle, debounce/poll state machine, instability cap, authoritative-result tracking, Ctrl+C exit mapping support.
- `src/qualock/project_watch/render.py` — watch-specific headers/status messages while delegating authoritative result formatting to existing verify renderer.
- `src/qualock/cli.py` — add `qualock watch` and map fatal startup/integrity/operational errors to existing exit semantics.
- `tests/unit/test_project_watch_snapshot.py` — Git discovery and metadata snapshot behavior.
- `tests/unit/test_project_watch_control.py` — frozen signed-control authentication and change detection.
- `tests/unit/test_project_watch_engine.py` — deterministic stable-cycle and polling/debounce state machine.
- `tests/unit/test_project_watch_cli.py` — command rendering, Ctrl+C exit code, fail-closed startup/control errors.
- `README.md` — foreground watch UX and limitations.

---

### Task 1: Git-aware project snapshots

**Files:**
- Create: `src/qualock/project_watch/__init__.py`
- Create: `src/qualock/project_watch/models.py`
- Create: `src/qualock/project_watch/snapshot.py`
- Create: `tests/unit/test_project_watch_snapshot.py`

**Interfaces:**
- Produces immutable `FileStamp(path: str, present: bool, mode: int | None, size: int | None, mtime_ns: int | None)`.
- Produces immutable `ProjectSnapshot(files: tuple[FileStamp, ...])`.
- Produces `capture_project_snapshot(root: Path) -> ProjectSnapshot`.
- Raises `ProjectWatchSnapshotError(RuntimeError)` when Git discovery cannot complete or an unexpected filesystem error prevents a trustworthy snapshot.

- [ ] **Step 1: Write RED model/discovery tests**

Cover exact behavior:
- tracked and untracked non-ignored files appear;
- ignored files do not appear;
- `.git/**` and `.qualock/**` are excluded even if Git returns them;
- a tracked deleted path remains as `present=False`;
- output ordering is deterministic by relative path;
- file metadata changes produce unequal snapshots;
- Git timeout/nonzero/OSError raises `ProjectWatchSnapshotError`.

Use a monkeypatched `run_process` for parser/error tests and a temporary real Git repository for one integration-shaped discovery test.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_watch_snapshot.py`

Expected: collection/import failure because `qualock.project_watch` does not exist.

- [ ] **Step 3: Implement immutable snapshot models**

Use frozen dataclasses:
```python
@dataclass(frozen=True, order=True)
class FileStamp:
    path: str
    present: bool
    mode: int | None
    size: int | None
    mtime_ns: int | None

@dataclass(frozen=True)
class ProjectSnapshot:
    files: tuple[FileStamp, ...]
```

Do not store absolute paths.

- [ ] **Step 4: Implement Git discovery exactly**

Call:
```python
run_process(
    ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    cwd=root,
    timeout_seconds=5,
)
```

Parse NUL-separated UTF-8 paths. Reject undecodable output as snapshot failure. Normalize separators to `/` for identity; reject absolute paths or `..` traversal if encountered.

- [ ] **Step 5: Implement metadata capture**

Use `Path.lstat()`. Convert `FileNotFoundError` to a missing `FileStamp`; propagate other `OSError` as `ProjectWatchSnapshotError`. Exclude any path equal to `.git`/`.qualock` or starting `.git/`/`.qualock/`.

- [ ] **Step 6: Run snapshot suite and commit**

Expected: PASS.

Commit:
`feat: add git-aware project watch snapshots`

---

### Task 2: Frozen signed watch-control identity

**Files:**
- Create: `src/qualock/project_watch/control.py`
- Create: `tests/unit/test_project_watch_control.py`

**Interfaces:**
- Produces frozen `WatchControlIdentity(lock_sha256: str)`.
- Produces `freeze_watch_control(root: Path, *, key_path: Path | None = None) -> WatchControlIdentity`.
- Produces `assert_watch_control(root: Path, frozen: WatchControlIdentity, *, key_path: Path | None = None) -> None`.
- Raises `WatchControlChangedError(RuntimeError)` only when a currently valid signed lock differs from the startup identity.
- Existing `FileNotFoundError` and `ProjectLockIntegrityError` semantics remain available for missing/integrity failures.

- [ ] **Step 1: Write RED control tests**

Create valid signed locks through existing project-protection signing/I/O helpers. Cover:
- startup authenticates a valid signed lock and returns SHA-256 of raw bytes;
- same lock passes later assertion;
- byte-for-byte lock tamper fails through existing integrity error;
- missing key fails through existing integrity error;
- missing lock remains `FileNotFoundError`;
- replacing the lock with a newly valid differently signed lock raises `WatchControlChangedError`;
- reserializing a logically equivalent lock to different raw bytes also counts as changed control identity.

- [ ] **Step 2: Verify RED**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_watch_control.py`

- [ ] **Step 3: Implement startup freeze**

Read `.qualock/project.lock` as raw bytes, load the existing local signing key, and call existing `read_project_lock(lock_path, key)` before hashing. Only authenticated bytes may become a frozen identity.

- [ ] **Step 4: Implement per-poll assertion**

Re-authenticate first, hash current raw bytes with SHA-256, compare via `hmac.compare_digest` or exact constant-time-safe string comparison, and raise a bounded `WatchControlChangedError` telling the user to restart `qualock watch` after intentionally re-protecting.

- [ ] **Step 5: Run control suite and commit**

Expected: PASS.

Commit:
`feat: freeze signed watch control state`

---

### Task 3: Stable verification cycle and watch engine

**Files:**
- Modify: `src/qualock/project_watch/models.py`
- Create: `src/qualock/project_watch/engine.py`
- Create: `tests/unit/test_project_watch_engine.py`

**Interfaces:**
- Produces frozen `WatchTiming(poll_seconds: float = 0.5, settle_seconds: float = 1.0, max_unstable_cycles: int = 2)`.
- Produces `StableCycle` carrying either an authoritative `ProjectVerifyResult` or an unstable post-snapshot.
- Produces `WatchOutcome(last_result: ProjectVerifyResult | None, exit_status: ProtectionStatus | None, interrupted: bool)`.
- Main engine entry point:
```python
run_watch(
    root: Path,
    *,
    timing: WatchTiming = WatchTiming(),
    key_path: Path | None = None,
    snapshot_fn: Callable[[Path], ProjectSnapshot] = capture_project_snapshot,
    verify_fn: Callable[..., ProjectVerifyResult] = execute_verify,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_event: Callable[[WatchEvent], None] | None = None,
) -> WatchOutcome
```
Exact callable typing may use Protocol aliases if clearer, but tests and CLI must use one public `run_watch`.

- [ ] **Step 1: Write RED stable-cycle tests**

Cover:
- snapshot before verify equals snapshot after -> result authoritative;
- snapshot differs -> result suppressed and cycle marked unstable;
- frozen control is checked before and after every verify;
- a control error after verify suppresses the verify result and propagates fatal error;
- evidence generation remains delegated to existing `execute_verify`; engine does not write project-protection evidence itself.

- [ ] **Step 2: Implement one stable verification cycle**

Order must be:
1. assert frozen control;
2. capture pre snapshot;
3. call verify;
4. assert frozen control again;
5. capture post snapshot;
6. return authoritative only if pre == post.

Do not render inside this primitive.

- [ ] **Step 3: Write RED initial-watch and debounce tests**

Use a fake clock/sleeper and queued snapshots/results. Cover:
- initial verify always happens before entering idle polling;
- stable initial PASS/FAIL/INCOMPLETE becomes authoritative;
- change burst keeps resetting settle deadline and causes exactly one verify after 1.0 second of stability;
- no project change means no extra verify;
- a control error during an idle/no-change poll propagates before the next project snapshot is accepted;
- `.qualock/results` activity is invisible because it is absent from snapshots;
- regression and incomplete results continue polling rather than terminating.

- [ ] **Step 4: Implement polling/debounce loop**

Every poll:
1. assert frozen control;
2. capture current project snapshot;
3. compare with observed snapshot;
4. if changed, update observed snapshot and last-change time;
5. verify only after no new change for `settle_seconds`.

After a stable authoritative verification, replace observed snapshot with the cycle's post snapshot.

- [ ] **Step 5: Write RED race/instability tests**

Cover:
- project changes during verify -> no SAFE/FAIL/INCOMPLETE event for that stale result;
- first unstable cycle schedules exactly one settled retry;
- second consecutive unstable cycle emits an authoritative watch-level INCOMPLETE event/state and resumes polling without a third immediate verify;
- later user edit resets instability count and allows normal verification;
- a protection that changes files once gets a stable retry and then publishes only the retry result.

Represent the instability-limit state separately from `ProjectVerifyResult` if necessary; do not invent a project-protection evidence record for a verification result that never stabilized.

- [ ] **Step 6: Implement instability cap**

Maintain consecutive unstable-cycle count. Reset it on any stable authoritative result. At two unstable cycles:
- emit a bounded `WatchEvent` of kind `INCOMPLETE`;
- set watch exit-state status to INCOMPLETE without fabricating a `ProjectVerifyResult`;
- set observed snapshot to the latest post snapshot;
- resume ordinary polling and wait for a new external change.

- [ ] **Step 7: Write RED Ctrl+C outcome tests**

Monkeypatch `sleep_fn` or event callback to raise `KeyboardInterrupt` after a known authoritative state. Assert `run_watch` returns `interrupted=True` and preserves last authoritative status/instability-INCOMPLETE exit state.

- [ ] **Step 8: Implement graceful KeyboardInterrupt boundary**

Catch `KeyboardInterrupt` only at the outer watch-loop boundary. Do not swallow control/snapshot/integrity exceptions.

- [ ] **Step 9: Run engine suite and commit**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_watch_engine.py`

Expected: PASS.

Commit:
`feat: add foreground auto-verify watch engine`

---

### Task 4: CLI/rendering and user-facing semantics

**Files:**
- Create: `src/qualock/project_watch/render.py`
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_project_watch_cli.py`
- Modify: `README.md`

**Interfaces:**
- `render_watch_event(event: WatchEvent) -> str` renders only watch-specific messages.
- Authoritative verification results reuse `render_verify_terminal`.
- `watch_command()` owns exit-code mapping and console output, not verification logic.

- [ ] **Step 1: Write RED render tests**

Cover exact user-facing events:
- startup header;
- `Watching for changes...`;
- `Changes detected...`;
- `Waiting for edits to settle...`;
- `Checking protected behavior...`;
- stale-cycle message: `Project changed while QuaLock was checking; checking again after edits settle.`;
- instability-limit `CHECK COULD NOT FINISH` explanation;
- valid-control-changed message tells user to restart watch.

- [ ] **Step 2: Implement watch rendering**

Keep terminal append-only and Rich-markup-safe. Do not clear screen or create a TUI.

- [ ] **Step 3: Write RED CLI tests**

Monkeypatch `run_watch` or inject a deterministic watch runner. Cover:
- missing lock -> existing invalid-input exit `3`;
- signing/integrity failure -> exit `4`;
- frozen control changed -> exit `4`;
- snapshot/Git operational failure -> exit `1`;
- Ctrl+C/normal interruption after PASS -> `0`;
- after FAIL -> `2`;
- after project-protection INCOMPLETE or instability-limit INCOMPLETE -> `4`;
- initial PASS/FAIL/INCOMPLETE output reuses existing verify renderer and evidence path format;
- stale verify result is never rendered by CLI because engine never emits it as authoritative.

- [ ] **Step 4: Add `qualock watch` CLI wiring**

Use `Path.cwd()`. Do not expose poll/debounce flags. Catch specific watch/control/integrity errors before generic operational errors, preserving current project-protection exit semantics.

- [ ] **Step 5: Update README**

Document:
- foreground lifecycle and Ctrl+C;
- initial verify;
- Git-visible tracked/untracked non-ignored scope;
- metadata-only trigger limitation;
- `.qualock` exclusion and frozen `project.lock` control;
- debounce behavior;
- no daemon/IDE integration/auto-fix;
- watch continues after FAIL/INCOMPLETE.

- [ ] **Step 6: Run focused watch + existing project-protection flow tests**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_watch_snapshot.py tests/unit/test_project_watch_control.py tests/unit/test_project_watch_engine.py tests/unit/test_project_watch_cli.py tests/unit/test_project_protection_flow.py`

Expected: PASS.

- [ ] **Step 7: Commit**

Commit:
`feat: add qualock watch command`

---

### Task 5: Final safety gates, reviewer, PR, and merge

**Files:**
- No planned production scope expansion.
- Modify only tests/docs required by findings.

- [ ] **Step 1: Run full local suite**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 2: Run compile and diff checks**

Run:
`/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests`
and:
`env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR git diff --check`

Expected: PASS.

- [ ] **Step 3: Run focused static checks**

Use an external temporary ruff/mypy environment if needed. Require:
- `src/qualock/project_watch` + watch tests: ruff PASS;
- `src/qualock/project_watch`: mypy `--strict` PASS;
- `src/qualock/cli.py`: no new lint debt versus `origin/main`.

- [ ] **Step 4: Audit protected scope**

Require empty diff versus `origin/main` for:
- `src/qualock/qualification`
- `src/qualock/run` except imports/use of existing `run_process` if none planned
- `src/qualock/evidence`
- `src/qualock/project_protection`

The watch subsystem must remain an orchestration layer only.

- [ ] **Step 5: Request independent whole-branch review**

Review `origin/main..HEAD` against the written spec. Any Critical/Important/P1/P2 correctness finding blocks merge; fix via TDD and rerun gates/review.

- [ ] **Step 6: Push and open PR #24**

PR summary must explicitly state:
- foreground Git-aware watch;
- initial signed verify;
- debounce;
- stale-result suppression;
- frozen signed lock identity;
- no daemon/dependency/engine changes.

- [ ] **Step 7: Require exact-head GitHub CI**

Wait for Python 3.11/3.12/3.13 success on the exact PR head. Do not merge if head moves.

- [ ] **Step 8: Squash merge with expected head SHA**

After reviewer + exact-head CI pass, squash merge to `main`.

- [ ] **Step 9: Post-merge verification**

Confirm `main` points to merge SHA and push CI 3.11/3.12/3.13 succeeds. Run a fresh local full suite on the merge commit if worker capacity permits; if local scheduling blocks before execution, report that honestly and rely on exact merge-commit CI rather than claiming a local pass.
