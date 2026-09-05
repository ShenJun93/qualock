# Windows Contributor Compatibility Design

**Batch:** #33
**Branch:** `feat/windows-contributor-compatibility`
**Base:** `9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e`
**Status:** Written spec approved by the user on 2026-09-05.

## Problem

A fresh Windows clone can install and invoke QuaLock, but the unit suite is red because many tests encode Linux-only assumptions: executable Python scripts are launched directly, virtualenv interpreters are hard-coded as `bin/python`, scheduler fixtures use POSIX absolute paths, and backend-specific tests assert POSIX file-mode or rendering behavior on Windows.

Issue #26 originally reported 16 Windows failures. Reproduction on Batch #33's exact base with Windows 11 / CPython 3.13.15 confirmed those classes of failure. After fixing only the first reported groups in an uncommitted TDD spike, a full native-Windows run exposed a broader total of 20 failures and 7 errors outside the original issue list. The batch therefore upgrades from a bounded test fix to an architectural test-portability change.

## Goals

1. A fresh Windows contributor checkout can run the repository's generic unit suite without false failures caused by POSIX-only test fixtures.
2. Codex and Claude resolver/adapter tests use cross-platform fake executables rather than relying on shebang execution.
3. Python project-setup/readiness tests create and assert the host platform's real virtualenv interpreter layout.
4. Generic scheduler models, state, and CLI tests use host-native absolute paths.
5. systemd and launchd tests remain authoritative for their native POSIX backends without pretending those backends run on Windows.
6. Add a Windows Python 3.13 CI job so portability regressions cannot silently return.
7. Preserve the existing Linux Python 3.11/3.12/3.13 CI matrix and Docker smoke unchanged.
8. Fix the one production behavior already proven wrong by native-Windows RED evidence: replacing `project.lock` with a directory must still fail closed as `WatchControlChangedError` rather than leaking `PermissionError`.

## Non-goals

- Claiming that the systemd user backend is supported on Windows.
- Claiming that the launchd backend is supported on Windows.
- Adding a Windows scheduler backend.
- Making the Linux-native Codex or Claude runtime itself execute on Windows.
- Changing Docker, sandbox, credentials, agent evidence, qualification policy, budgets, or report schemas.
- Fixing `qualock doctor` first-run UX; that remains a separate Batch #34 candidate.
- Skipping broad test modules merely to make Windows CI green.

## Evidence Before Design

Fresh Linux baseline on the Batch #33 worktree:

```text
858 passed, 1 skipped
```

Native Windows reproduction on CPython 3.13.15:

- Codex resolver: 6 failures from direct execution of fake POSIX `npm` scripts.
- Codex adapter: 3 failures from direct execution of fake POSIX `codex` scripts.
- project setup/protection/readiness/watch: 9 failures from POSIX venv paths, shell scripts, and one Windows-specific `PermissionError` for a directory replacing `project.lock`.

A TDD spike fixed those reported groups and proved the approach on Windows:

```text
agent_resolver: 15 passed
codex_adapter:    7 passed
setup/etc:       73 passed
```

A subsequent full Windows run exposed the hidden broader scope:

```text
20 failed, 827 passed, 5 skipped, 7 errors
```

The remaining failures are concentrated in Claude resolver fake executables and scheduler tests that encode POSIX path/mode/backend assumptions.

## Architecture

### 1. Test-only platform helpers

Add a small private helper module under `tests/unit/`, for example `tests/unit/_platform_helpers.py`. It owns test fixture portability only and must never be imported by production code.

It provides three concepts:

- a Python-script launcher that returns an actually executable path on the current host;
- the relative Python interpreter path for a virtualenv on the current host;
- helpers to create fake venv interpreters and host-native absolute paths.

On POSIX, a fake Python executable may remain a chmodded script. On Windows, the launcher must use a Windows-executable wrapper that delegates to `sys.executable`; test semantics remain the same while the transport becomes valid for `subprocess.Popen(..., shell=False)`.

### 2. Generic agent tests are cross-platform

Codex and Claude resolver tests continue to exercise their real production resolver logic, including Linux package names and native-binary path construction. Only the fake `npm`/CLI transport changes.

The tests must still prove:

- exact version selection;
- latest-version resolution;
- cache reuse without npm;
- x86_64/arm64 package path selection;
- required CLI contract/version validation;
- missing-install-artifact rejection.

No resolver production code changes are expected for Windows test portability.

### 3. Python venv fixtures follow host layout

Tests that mean "a valid local venv exists" must construct:

- POSIX: `<env>/bin/python`
- Windows: `<env>/Scripts/python.exe`

Expected `python_executable` and generated protection commands must use that same relative path.

Tests that deliberately mean "wrong-platform venv" continue to construct the opposite layout explicitly. This keeps the existing production detection contract meaningful instead of weakening it.

### 4. Scheduler tests split generic behavior from native-backend behavior

Generic scheduler tests remain enabled on Windows:

- registration model validation;
- state persistence and key matching;
- CLI rendering/status logic that does not require a native scheduler;
- operational equality and immutability.

Their fixtures must use host-native absolute paths derived from `tmp_path` rather than literals such as `/missing/qualock-python`.

Backend-specific semantics are platform-scoped:

- systemd unit escaping/rendering and systemd-native integration tests are not asserted on Windows;
- launchd plist mode/launchctl semantics are not asserted on Windows.

These tests receive narrow, explicit `pytest.mark.skipif` conditions with reasons stating that the backend is POSIX-native. Linux CI continues to cover systemd behavior; launchd tests that are pure serialization may continue cross-platform only when they do not depend on Unix permissions or host path semantics.

The rule is: skip only assertions whose meaning is inherently backend/OS-specific, never whole generic scheduler modules for convenience.

### 5. Watch-control fail-closed normalization

`assert_watch_control()` already normalizes disappeared/replaced lock-path conditions such as `FileNotFoundError`, `NotADirectoryError`, and `IsADirectoryError` into `WatchControlChangedError`.

On Windows, `Path.read_bytes()` against a directory can raise `PermissionError` instead of `IsADirectoryError`. Native RED evidence proves this leaks a platform syscall detail and breaks the existing fail-closed contract.

Batch #33 extends the same normalization boundary to `PermissionError`. The error message and recovery recommendation remain unchanged. No broader exception swallowing is allowed.

### 6. Windows CI

Keep the existing Ubuntu matrix unchanged. Add a separate Windows job using Python 3.13.

The job must exercise the contributor path, not a special reduced test command:

```text
python -m pip install uv
uv sync --dev
uv run pytest -q
uv run python -m compileall -q src tests
```

The Windows job must not run the Linux Docker tmpfs smoke. Native-backend tests excluded by precise platform markers appear as skips, not failures.

## Test Strategy

### TDD sequence

1. Preserve the already captured native-Windows RED outputs as evidence.
2. Restore the pre-spec TDD stash only after this written spec is approved and the implementation plan is committed.
3. Port Codex fake executable fixtures and prove focused Linux + Windows GREEN.
4. Port project setup/readiness/protection fixtures and prove focused Linux + Windows GREEN.
5. Normalize Windows `PermissionError` in watch-control and prove the existing directory-replacement regression test passes on both platforms.
6. Port Claude fake executable fixtures with focused native-Windows RED then GREEN.
7. Port generic scheduler fixtures to host-native absolute paths.
8. Add narrow Windows skips only for genuinely systemd/launchd-specific semantics.
9. Run the complete native Windows suite until it is green apart from intentional documented skips.
10. Run the complete Linux suite and static gates to prove no compatibility regression.
11. Add the Windows CI job last, after local Windows acceptance already passes.

### Required final local gates

Linux:

```text
python -m pytest -q
ruff check <changed Python files>
mypy --strict src/qualock
python -m compileall -q src tests
git diff --check
```

Windows fresh-clone equivalent:

```text
uv sync --dev
uv run pytest -q
uv run python -m compileall -q src tests
```

The final review must report the exact intentional Windows skip set and verify that no skip masks a generic resolver, project-setup, scheduler-model/state, or watch-control test.

## Compatibility and Safety Invariants

- Linux test count may increase or remain stable; existing Linux behavioral tests must not be removed to obtain Windows green.
- Windows skips must be narrow and justified by native backend semantics.
- Production changes are limited to the proven watch-control exception normalization unless a new native-Windows RED demonstrates another real cross-platform production defect.
- No agent resolver behavior, qualification behavior, scheduler registration schema, report schema, Docker behavior, credential handling, or release-monitor semantics change in this batch.
- Test helpers remain test-only.
- No remote mutation, PR, tag, release, or PyPI action is part of local Batch #33 implementation.

## Documentation

Update contributor/testing documentation only if needed to state that the test suite is supported on Windows while native scheduler backends remain platform-specific. Do not claim full QuaLock runtime parity across operating systems.

## Acceptance Criteria

Batch #33 is locally complete only when:

1. native Windows full pytest is green except for an explicit, reviewed set of platform-native backend skips;
2. Linux full pytest remains green;
3. static/type/compile/diff gates are green;
4. Windows CI configuration runs the full contributor test command on Python 3.13;
5. independent whole-branch review has no Critical or Important findings;
6. the branch remains local until separately authorized for push/PR/merge.
