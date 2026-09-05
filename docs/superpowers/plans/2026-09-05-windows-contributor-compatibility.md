# Windows Contributor Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make QuaLock's generic contributor test suite green on native Windows while keeping systemd/launchd semantics platform-scoped and preserving Linux behavior.

**Architecture:** Put host-specific test transport in one private `tests/unit/_platform_helpers.py` module, port generic agent/project/scheduler fixtures to those helpers or host-native `tmp_path` values, and skip only assertions whose meaning is intrinsically POSIX-backend-specific. Production code changes are limited to normalizing Windows `PermissionError` at the existing watch-control fail-closed boundary.

**Tech Stack:** Python 3.11-3.13, pytest, Pydantic, Typer, uv, GitHub Actions, Windows 11 / CPython 3.13.15 acceptance, WSL/Linux local verification.

**Spec:** `docs/superpowers/specs/2026-09-05-windows-contributor-compatibility-design.md`

## Global Constraints

- Base commit is `9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e`; written spec commit is `19d627157188f88272ddc136d015539ef0097de7`.
- Generic resolver, project-setup, scheduler-model/state/CLI, and watch-control tests must run on Windows; do not skip broad modules.
- systemd user and launchd backends are not Windows runtime targets; only genuinely native-backend assertions may receive narrow `pytest.mark.skipif(os.name == "nt", ...)` markers.
- Preserve the existing Ubuntu Python 3.11/3.12/3.13 CI matrix and Docker tmpfs smoke unchanged.
- Add a separate Windows Python 3.13 contributor CI job using `uv sync --dev`, full `uv run pytest -q`, and compileall.
- Production changes are limited to adding `PermissionError` to the existing watch-control replaced/disappeared-lock normalization unless a new native-Windows RED proves another real production defect.
- Do not change resolver behavior, qualification behavior, scheduler registration schema, reports, Docker, credential handling, budgets, or release-monitor semantics.
- Pre-spec TDD work is preserved in `stash@{0}` named `batch33-pre-spec-tdd-edits`; recover only task-owned files, never `git stash pop` the whole stash.
- No push, PR, merge, tag, release, or PyPI action is part of this plan.

---

### Task 1: Cross-platform executable fixtures for Codex and Claude

**Files:**
- Create: `tests/unit/_platform_helpers.py`
- Modify: `tests/unit/test_agent_resolver.py`
- Modify: `tests/unit/test_codex_adapter.py`
- Modify: `tests/unit/test_claude_resolver.py`

**Interfaces:**
- Produces: `write_python_launcher(path: Path, source: str) -> Path`
- Produces: `venv_python_path(root: Path, name: str = ".venv") -> Path`
- Produces: `venv_python_relative(name: str = ".venv") -> str`
- Constraint: production resolver modules remain unchanged.

- [ ] **Step 1: Recover only the already-proven Codex helper work from the pre-spec stash**

```bash
git checkout 'stash@{0}' -- \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_codex_adapter.py
git checkout 'stash@{0}^3' -- tests/unit/_platform_helpers.py
git status --short
```

Expected dirty set: exactly the three files above. Do not restore the project-setup/watch files yet.

The recovered helper must have this contract:

```python
import os
import sys
from pathlib import Path


def write_python_launcher(path: Path, source: str) -> Path:
    if os.name == "nt":
        script = path.with_suffix(".py")
        script.write_text(source, encoding="utf-8")
        launcher = path.with_suffix(".cmd")
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return launcher

    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(0o755)
    return path


def venv_python_path(root: Path, name: str = ".venv") -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return root / name / relative


def venv_python_relative(name: str = ".venv") -> str:
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return (Path(name) / relative).as_posix()
```

- [ ] **Step 2: Verify the recovered Codex RED→GREEN result stays green on Linux**

Run:

```bash
/home/pacmap/qualock-exp/.venv/bin/python3 -m pytest \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_codex_adapter.py -q
```

Expected: `22 passed` and no production-file diff.

The original native-Windows RED already captured before the spec was:

```text
test_agent_resolver.py: 6 failed, 9 passed (WinError 193)
test_codex_adapter.py: 3 failed, 4 passed (WinError 193)
```

Do not manufacture another failing test for already-preserved TDD work.

- [ ] **Step 3: Reproduce the still-unfixed Claude resolver failure on native Windows before editing it**

Create a clean C-drive checkout from the base and run only the Claude resolver tests:

```bash
rm -rf /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-claude-red
mkdir -p /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-claude-red
git archive 9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e | \
  tar -x -C /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-claude-red
powershell.exe -NoProfile -Command \
  "Set-Location '$env:LOCALAPPDATA\\Temp\\qualock-b33-claude-red'; uv sync --dev; uv run pytest tests/unit/test_claude_resolver.py -q"
```

Expected: FAIL from direct execution of fake POSIX `npm`/Claude scripts; the failure must be transport-related, not an assertion typo.

- [ ] **Step 4: Port Claude's fake npm transport without weakening contract validation**

Refactor `make_fake_npm()` so the npm program itself is created with `write_python_launcher()`. Keep the resolver's real install path and package-selection logic.

For the generated extensionless `.../claude` test artifact on Windows, do not attempt to execute a shebang script directly. Instead, keep `_validate_binary_contract()` real and interpose only its process transport in the test module:

```python
import qualock.agents.claude_resolver as claude_resolver_module


def install_contract_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reported_version: str = "2.1.260",
    missing_flag: str | None = None,
) -> None:
    real_run = claude_resolver_module.run_process
    flags = (
        "-p",
        "--safe-mode",
        "--restricted",
        "--no-session-persistence",
        "--output-format",
        "--verbose",
        "--permission-mode",
        "--permission-prompts",
        "--model",
        "--effort",
        "--tools",
        "--allowed-tools",
        "--strict-mcp-config",
        "--mcp-config",
        "--settings",
    )

    def portable_run(argv, *, timeout_seconds, env=None):
        executable = Path(argv[0])
        if executable.name == "claude" and argv[1:] == ["--version"]:
            return ProcessResult(0, f"{reported_version} (Claude Code)\n", "", 0.01, False)
        if executable.name == "claude" and argv[1:] == ["--help"]:
            visible = [flag for flag in flags if flag != missing_flag]
            help_text = "\n".join(f"  {flag} <value>  Test option" for flag in visible)
            return ProcessResult(0, help_text, "", 0.01, False)
        return real_run(argv, timeout_seconds=timeout_seconds, env=env)

    monkeypatch.setattr(claude_resolver_module, "run_process", portable_run)
```

Use the helper only in tests that currently rely on executing the generated fake Claude script. Tests that already monkeypatch `run_process` directly remain unchanged.

Preserve assertions for:
- exact x86_64/arm64 package selection;
- latest-version resolution;
- cache reuse after fake npm removal;
- mismatched reported version;
- required `-p`, `--verbose`, and `--restricted` flags;
- missing installed binary rejection.

- [ ] **Step 5: Run focused Linux GREEN**

```bash
/home/pacmap/qualock-exp/.venv/bin/python3 -m pytest \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_codex_adapter.py \
  tests/unit/test_claude_resolver.py -q
```

Expected: all pass.

- [ ] **Step 6: Run focused native-Windows GREEN from a fresh tracked-tree export**

```bash
rm -rf /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task1
mkdir -p /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task1
git archive HEAD | tar -x -C /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task1
cp tests/unit/_platform_helpers.py \
   tests/unit/test_agent_resolver.py \
   tests/unit/test_codex_adapter.py \
   tests/unit/test_claude_resolver.py \
   /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task1/tests/unit/
powershell.exe -NoProfile -Command \
  "Set-Location '$env:LOCALAPPDATA\\Temp\\qualock-b33-task1'; uv sync --dev; uv run pytest tests/unit/test_agent_resolver.py tests/unit/test_codex_adapter.py tests/unit/test_claude_resolver.py -q"
```

Expected: all focused agent tests pass on Windows.

- [ ] **Step 7: Static check and commit**

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  tests/unit/_platform_helpers.py \
  tests/unit/test_agent_resolver.py \
  tests/unit/test_codex_adapter.py \
  tests/unit/test_claude_resolver.py
git diff --check
git add tests/unit/_platform_helpers.py tests/unit/test_agent_resolver.py \
  tests/unit/test_codex_adapter.py tests/unit/test_claude_resolver.py
git commit -m "test: make agent fixtures Windows portable"
```

---

### Task 2: Platform-aware Python project fixtures and watch-control fail-closed behavior

**Files:**
- Modify: `tests/unit/test_project_setup_detection.py`
- Modify: `tests/unit/test_project_setup_flow.py`
- Modify: `tests/unit/test_project_setup_readiness.py`
- Modify: `tests/unit/test_project_protection_flow.py`
- Modify: `src/qualock/project_watch/control.py`
- Verify: `tests/unit/test_project_watch_control.py`
- Reuse: `tests/unit/_platform_helpers.py`

**Interfaces:**
- Consumes: `write_python_launcher`, `venv_python_path`, `venv_python_relative` from Task 1.
- Production behavior change: `assert_watch_control()` catches `PermissionError` at the same replaced/disappeared lock normalization boundary.

- [ ] **Step 1: Recover only Task 2's pre-spec TDD files**

```bash
git checkout 'stash@{0}' -- \
  src/qualock/project_watch/control.py \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_setup_readiness.py
git status --short
```

Expected: Task 1 is committed; only these Task 2 files are dirty.

- [ ] **Step 2: Confirm the recovered code matches the approved portability contract**

Every test meaning "valid venv" must use:

```python
python = venv_python_path(tmp_path)
python.parent.mkdir(parents=True, exist_ok=True)
python.write_text("", encoding="utf-8")
(tmp_path / ".venv/pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
```

Every assertion on the configured path must use:

```python
expected_python = venv_python_relative()
assert capabilities.python_executable == expected_python
assert compile_check.command[0] == expected_python
```

Tests that intentionally create the wrong platform layout must continue to construct the opposite path explicitly; do not make production detection accept both layouts.

The executable-disappears protection test must use a real cross-platform launcher:

```python
executable = write_python_launcher(
    tmp_path / "health-check",
    "raise SystemExit(0)\n",
)
write_config(tmp_path, [str(executable)], name="Executable health check")
```

- [ ] **Step 3: Keep the watch-control production fix minimal**

The implementation in `src/qualock/project_watch/control.py` must be exactly the existing exception normalization plus `PermissionError`:

```python
try:
    raw = _read_raw_lock(root)
except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError) as exc:
    raise WatchControlChangedError(
        "project protection lock disappeared during this watch session; "
        "restart qualock watch after intentionally re-protecting the project"
    ) from exc
```

Do not catch generic `OSError` or `Exception`.

Native-Windows RED provenance already captured before the spec:

```text
Path.read_bytes() on directory -> PermissionError
expected -> WatchControlChangedError
```

The existing `test_assert_treats_lock_replaced_by_directory_as_changed_control` is the regression test; do not duplicate it solely for platform naming.

- [ ] **Step 4: Run focused Linux GREEN**

```bash
/home/pacmap/qualock-exp/.venv/bin/python3 -m pytest \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_setup_readiness.py \
  tests/unit/test_project_watch_control.py -q
```

Expected: all pass.

- [ ] **Step 5: Run focused native-Windows GREEN**

Export the current tracked tree plus Task 2 dirty files to a C-drive temp directory and run the same five modules. Expected: all pass; specifically, the directory-replacement watch-control test must pass without a raw `PermissionError`.

Use:

```bash
rm -rf /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task2
mkdir -p /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task2
git archive HEAD | tar -x -C /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task2
for file in \
  src/qualock/project_watch/control.py \
  tests/unit/_platform_helpers.py \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_setup_readiness.py; do
  mkdir -p "/mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task2/$(dirname "$file")"
  cp "$file" "/mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-task2/$file"
done
powershell.exe -NoProfile -Command \
  "Set-Location '$env:LOCALAPPDATA\\Temp\\qualock-b33-task2'; uv sync --dev; uv run pytest tests/unit/test_project_protection_flow.py tests/unit/test_project_setup_detection.py tests/unit/test_project_setup_flow.py tests/unit/test_project_setup_readiness.py tests/unit/test_project_watch_control.py -q"
```

- [ ] **Step 6: Static/type check and commit**

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/project_watch/control.py \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_setup_readiness.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/project_watch/control.py
git diff --check
git add src/qualock/project_watch/control.py \
  tests/unit/test_project_protection_flow.py \
  tests/unit/test_project_setup_detection.py \
  tests/unit/test_project_setup_flow.py \
  tests/unit/test_project_setup_readiness.py
git commit -m "fix: make project portability tests cross-platform"
```

---

### Task 3: Scheduler generic portability with narrow native-backend skips

**Files:**
- Modify: `tests/unit/test_scheduler_models.py`
- Modify: `tests/unit/test_scheduler_state.py`
- Modify: `tests/unit/test_scheduler_cli.py`
- Modify: `tests/unit/test_scheduler_systemd.py`
- Modify: `tests/unit/test_scheduler_launchd.py`

**Interfaces:**
- Scheduler production code and schemas remain unchanged.
- Generic scheduler tests use absolute paths derived from `tmp_path`.
- Only the systemd POSIX-rendering assertion and launchd Unix-mode assertion are expected to need Windows skips based on current RED evidence.

- [ ] **Step 1: Reproduce scheduler RED on native Windows before editing**

From a clean C-drive export of current HEAD, run:

```text
pytest tests/unit/test_scheduler_models.py tests/unit/test_scheduler_state.py tests/unit/test_scheduler_cli.py tests/unit/test_scheduler_systemd.py tests/unit/test_scheduler_launchd.py -q
```

Expected current failures include:
- POSIX literals `/opt/qualock/python`, `/missing/qualock-python`, `/missing/runner-home` rejected as non-absolute on Windows;
- exact CLI output hard-codes `/opt/qualock/python`;
- systemd service exact rendering mismatches because Windows root paths contain backslashes;
- launchd `0o600` mode assertion is not meaningful on Windows.

If a new scheduler production defect appears beyond those categories, stop and escalate rather than broadening production scope silently.

- [ ] **Step 2: Make scheduler model/state fixtures host-native without changing schema behavior**

In `test_scheduler_models.py`, build fixture paths from `tmp_path`:

```python
python_executable = tmp_path / "runtime" / "qualock-python"
runner_working_directory = tmp_path / "runner-home"
```

These paths do not need to exist; they only need to be absolute on the current host.

For the stale-path acceptance test, use new nonexistent children of `tmp_path` and assert those exact `Path` values survive validation.

In `test_scheduler_state.py`, use the same pattern inside the `registration` fixture:

```python
python_executable=root / "missing" / "qualock-python",
runner_working_directory=root / "missing" / "runner-home",
```

Keep the existing POSIX-permission `skipif(os.name == "nt", reason="POSIX permissions")` unchanged.

- [ ] **Step 3: Make scheduler CLI expected output use the registration fixture's native path**

Change `sample_registration()` to:

```python
python_executable=canonical / "runtime" / "qualock-python",
runner_working_directory=canonical,
```

Change exact output assertions from:

```python
"Python: /opt/qualock/python\n"
```

to:

```python
f"Python: {registration.python_executable}\n"
```

Do not alter CLI production rendering or backend labels.

- [ ] **Step 4: Add only the two currently justified native-backend Windows skips**

In `tests/unit/test_scheduler_systemd.py`, import `os` and mark only the exact service-rendering test:

```python
@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering uses POSIX path semantics")
def test_systemd_rendering_is_shell_free_persistent_and_local(...):
    ...
```

Do not skip argv construction, state handling, atomic file behavior, or backend error tests merely because they live in the systemd module.

In `tests/unit/test_scheduler_launchd.py`, import `os` and mark only the Unix file-mode assertion:

```python
@pytest.mark.skipif(os.name == "nt", reason="launchd plist mode is a POSIX permission contract")
def test_install_writes_mode_0600_plist(...):
    ...
```

Keep plist serialization, owned-path behavior, launchctl argv, atomic writes, and error handling enabled on Windows.

- [ ] **Step 5: Run focused Linux scheduler GREEN**

```bash
/home/pacmap/qualock-exp/.venv/bin/python3 -m pytest \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py -q
```

Expected: all pass with no new Linux skips.

- [ ] **Step 6: Run focused native-Windows scheduler GREEN and record exact skips**

Run the same five modules in a fresh C-drive export. Expected: PASS with only the pre-existing state POSIX-permission skip plus the two narrowly-added backend-specific skips, unless the current suite already has other documented Windows-native scheduler skips.

Record exact node IDs of every Windows skip:

```powershell
uv run pytest <five scheduler modules> -q -rs
```

Reviewer requirement: no generic model/state/CLI test may be skipped.

- [ ] **Step 7: Static check and commit**

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py
git diff --check
git add tests/unit/test_scheduler_models.py tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_cli.py tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py
git commit -m "test: make scheduler suite Windows portable"
```

---

### Task 4: Windows contributor CI, documentation, and final dual-platform verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Verify: all changed files since `9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e`

**Interfaces:**
- Existing Ubuntu `test` job remains semantically unchanged: same Python matrix, install/test commands, Docker smoke condition, compile step.
- New Windows job is additive and uses Python 3.13 + uv.

- [ ] **Step 1: Add a separate Windows CI job**

Append a sibling job under `jobs:`; do not fold Windows into the Ubuntu matrix because Docker smoke and shell semantics are Linux-specific.

Use this exact shape, adjusting only indentation to fit the existing YAML:

```yaml
  windows-test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.13"
      - name: Install uv
        run: python -m pip install uv
      - name: Sync
        run: uv sync --dev
      - name: Test
        run: uv run pytest -q
      - name: Compile
        run: uv run python -m compileall -q src tests
```

Do not add Docker steps to this job.

- [ ] **Step 2: Add a narrow contributor note to README Development**

Keep the existing Linux development command and add wording equivalent to:

```text
The contributor unit suite is exercised on Linux and Windows. Native scheduler backend checks remain platform-scoped: systemd semantics are Linux-native and launchd permission semantics are macOS/POSIX-native. This does not claim full runtime parity across operating systems.
```

Do not claim a Windows scheduler backend or Windows execution of Linux-native Codex/Claude binaries.

- [ ] **Step 3: Commit CI/docs before final verification**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "ci: test contributors on Windows"
```

The next verification steps must run on this exact committed HEAD; do not rely on pre-commit evidence.

- [ ] **Step 4: Run fresh full Linux verification on exact current HEAD**

```bash
/home/pacmap/qualock-exp/.venv/bin/python3 -m pytest -q
changed_py=$(git diff --name-only 9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e...HEAD -- '*.py')
/tmp/qualock-static-22-final/bin/ruff check $changed_py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock
/home/pacmap/qualock-exp/.venv/bin/python3 -m compileall -q src tests
git diff --check 9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e...HEAD
```

Expected: full Linux suite green, Ruff clean, strict mypy clean, compileall clean, diff-check clean.

- [ ] **Step 5: Run a fresh Windows tracked-tree acceptance, not the working directory over UNC**

Export the exact current HEAD to the C drive:

```bash
rm -rf /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-final
mkdir -p /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-final
git archive HEAD | tar -x -C /mnt/c/Users/PACMAP/AppData/Local/Temp/qualock-b33-final
```

Then run the exact contributor path:

```powershell
Set-Location "$env:LOCALAPPDATA\Temp\qualock-b33-final"
uv sync --dev
uv run pytest -q -rs
uv run python -m compileall -q src tests
```

Expected:
- zero failures/errors;
- only explicit, reviewed platform-native skips;
- no skipped generic Codex/Claude resolver, project-setup/readiness/protection, scheduler-model/state/CLI, or watch-control tests.

Capture the exact final Windows pass/skip counts and node IDs.

- [ ] **Step 6: Verify protected production scope**

The only allowed production diff is `src/qualock/project_watch/control.py`. This command must produce no output:

```bash
git diff --exit-code 9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e...HEAD -- \
  src/qualock/agents \
  src/qualock/qualification \
  src/qualock/config \
  src/qualock/canary \
  src/qualock/baseline \
  src/qualock/run \
  src/qualock/scheduler \
  src/qualock/release_monitor \
  src/qualock/version_bisect \
  src/qualock/github_pr \
  pyproject.toml
```

Then explicitly inspect the one allowed production file:

```bash
git diff 9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e...HEAD -- \
  src/qualock/project_watch/control.py
```

Expected production delta: only `PermissionError` added to the existing exception tuple.

- [ ] **Step 7: Confirm stash provenance remains available until all task reviews finish**

```bash
git stash list | grep 'batch33-pre-spec-tdd-edits'
git status --short
```

Expected: clean worktree and the original stash still present. Do not drop it until final review has accepted the branch; it is provenance/recovery evidence, not a source of uncommitted work.

- [ ] **Step 8: Independent exact-head whole-branch review**

Review exact diff:

```text
BASE=9f0d9ff1ae8c6cb256ee6d3e4a66000e8ef8699e
HEAD=<current exact head>
```

Reviewer must verify:
- Windows full suite is genuinely full, not a reduced command;
- exact intentional Windows skip set is narrow and native-backend-specific;
- no production scope growth beyond watch-control `PermissionError` normalization;
- Ubuntu matrix and Docker smoke remain unchanged;
- generic resolver/project/scheduler tests remain enabled on Windows;
- Critical = 0 and Important = 0 before local completion.

After a clean final review, the controller may drop only the named pre-spec stash and run the finishing-branch workflow. No remote mutation is authorized by this plan.
