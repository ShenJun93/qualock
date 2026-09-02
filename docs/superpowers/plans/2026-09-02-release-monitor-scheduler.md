# QuaLock Batch #27 Release Monitor Scheduler Implementation Plan

> > **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one safe, per-user, native daily trigger for the existing one-shot `qualock monitor`, with strict local `HH:MM` input, deterministic project registration, fail-closed runner behavior, observable native drift, and idempotent enable/status/disable commands on Windows, Linux, and macOS.

**Architecture:** A new `qualock.scheduler` package owns validated registration state, a fixed runner, orchestration, and three injected native adapters. Native schedulers are clocks only: each invokes `python -m qualock.scheduler.runner --project-key <key>` without a shell, and the runner validates registration, restores only captured `PATH`, logs, and invokes exactly `python -m qualock.cli monitor` in the registered project. The existing release-monitor freshness chain is promoted to `monitor_preflight(root)` and remains the sole entry to monitor eligibility.

**Tech Stack:** Python 3.11+, Typer/Rich, Pydantic v2, platformdirs, standard-library `dataclasses`, `enum`, `hashlib`, `json`, `os`, `pathlib`, `plistlib`, `subprocess`, `sys`, `tempfile`, `xml.etree.ElementTree`; pytest, Ruff, mypy strict. Reuse `qualock.run.process.ProcessResult`/`run_process`; add no dependency.

**Spec:** `docs/superpowers/specs/2026-09-02-release-monitor-scheduler-design.md` (approved 2026-09-02), based on exact commit `964ee25e58c531b74d0d4e496a546e69693f113e`, implementation branch `feat/release-monitor-scheduler`.

## Global Constraints

- Diff and final-review scope anchor exactly to base `964ee25e58c531b74d0d4e496a546e69693f113e` on branch `feat/release-monitor-scheduler`.
- V1 schedules one daily local wall-clock trigger; default `09:00`; strict `HH:MM` only; no cron/weekday/interval/timezone/run-now surface.
- Use only the current user's native scheduler: Windows Task Scheduler, Linux `systemd --user`, or macOS LaunchAgent. No QuaLock daemon, cron fallback, cloud scheduler, root/admin elevation, LaunchDaemon, or system service.
- Every native action is shell-free. No `cmd.exe`, PowerShell, `/bin/sh`, Bash, or arbitrary command string may be stored or invoked.
- The native action is fixed to `<absolute sys.executable> -m qualock.scheduler.runner --project-key <64-hex-key>`; the runner child is exactly `[sys.executable, "-m", "qualock.cli", "monitor"]` in production.
- `qualock monitor` remains the sole safety authority. Scheduler code must not duplicate release discovery, freshness, dedupe, qualification, evidence, or verdict policy.
- Scheduler state lives outside the repo under `Path(user_state_dir("qualock")) / "release-scheduler" / "projects" / <project-key>`; it is operational state, never trust evidence.
- Project identity reuses `sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()` from `qualock.release_monitor.state.project_key`.
- Registration persists only `PATH` from the environment, with `os.defpath` only when `PATH` is absent. It must never persist credentials or arbitrary environment variables.
- `runner_working_directory` is the current-user home captured at enable time and is independent from the monitored project root.
- `enabled_at` is UTC audit metadata excluded from operational equality. Preserve it only when all desired operational registration fields already match; a time/Python/PATH/backend change receives a new UTC timestamp.
- `schedule enable` parses time, runs fresh `monitor_preflight(root)`, then probes/mutates the backend. `schedule status` and `schedule disable` never require or call monitor preflight.
- Corrupt/deleted registration fails closed. Only a healthy valid registration plus native `MATCHING` may report `ENABLED`; native presence without trusted registration is `PRESENT_BUT_UNVERIFIED` and yields `NEEDS REPAIR`.
- Enable failure after registration write performs best-effort native removal and independent best-effort registration deletion, preserves `runs.log`, never prints `ENABLED`, and explicitly warns `native schedule may still be enabled` if native rollback fails.
- Disable removes native state before registration metadata, is idempotent for an already-missing native schedule, retains registration if native removal fails, and always preserves `runs.log`.
- Windows is current-user interactive-token, least privilege, no stored password, `StartWhenAvailable=true`, `IgnoreNew`, and makes no logged-out execution promise.
- Linux owns only XDG user unit files, uses `systemctl --user`, `Persistent=true`, a oneshot service, and never changes user lingering.
- macOS owns only `~/Library/LaunchAgents/io.qualock.release-monitor.<key>.plist`, uses the current GUI domain, and never installs a LaunchDaemon.
- Native local-time/DST/catch-up semantics are authoritative; QuaLock adds no retry scheduler or second catch-up mechanism, and stable native identity prevents overlapping copies for one project.
- Native unit tests use injected process runners and injected filesystem roots; ordinary CI must never mutate the host scheduler.
- Add no third-party Python dependency and do not modify `src/qualock/qualification/`, `src/qualock/run/`, `src/qualock/evidence/`, `src/qualock/source/`, `src/qualock/project_protection/`, or `src/qualock/project_setup/`.
- No tag, release, or PyPI publish action belongs to Batch #27.

---


### Task 1: Models, identity, strict time parsing, and registration state

**Files:**

- Create: `src/qualock/scheduler/__init__.py`
- Create: `src/qualock/scheduler/models.py`
- Create: `src/qualock/scheduler/state.py`
- Create: `tests/unit/test_scheduler_models.py`
- Create: `tests/unit/test_scheduler_state.py`

**Interfaces:**

```python
class SchedulerBackendKind(str, Enum):
    WINDOWS_TASK_SCHEDULER = "windows_task_scheduler"
    SYSTEMD_USER = "systemd_user"
    LAUNCHD_AGENT = "launchd_agent"

def backend_label(kind: SchedulerBackendKind) -> str: ...

class NativeScheduleState(str, Enum):
    MISSING = "missing"
    MATCHING = "matching"
    PRESENT_BUT_UNVERIFIED = "present_but_unverified"
    DRIFTED = "drifted"

class ScheduleRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    project_key: str
    project_root: Path
    backend: SchedulerBackendKind
    native_id: str
    hour: int
    minute: int
    python_executable: Path
    runner_working_directory: Path
    path_env: str
    enabled_at: datetime

@dataclass(frozen=True)
class ScheduleIdentity:
    project_key: str
    backend: SchedulerBackendKind
    native_id: str

@dataclass(frozen=True)
class NativeScheduleInspection:
    state: NativeScheduleState
    detail: str | None = None

class ProcessRunner(Protocol):
    def __call__(self, argv: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, timeout_seconds: float) -> ProcessResult: ...

def parse_daily_time(value: str) -> tuple[int, int]: ...
def native_id_for(kind: SchedulerBackendKind, project_key: str) -> str: ...
def schedule_identity(root: Path, kind: SchedulerBackendKind) -> ScheduleIdentity: ...
def operationally_equal(left: ScheduleRegistration, right: ScheduleRegistration) -> bool: ...

class RegistrationLoadKind(str, Enum):
    MISSING = "missing"
    VALID = "valid"
    CORRUPT = "corrupt"

@dataclass(frozen=True)
class RegistrationLoad:
    kind: RegistrationLoadKind
    registration: ScheduleRegistration | None = None
    detail: str | None = None

class RegistrationStore(Protocol):
    def project_dir(self, project_key: str) -> Path: ...
    def registration_path(self, project_key: str) -> Path: ...
    def log_path(self, project_key: str) -> Path: ...
    def load(self, project_key: str) -> RegistrationLoad: ...
    def save(self, registration: ScheduleRegistration) -> None: ...
    def delete(self, project_key: str) -> None: ...

class FileRegistrationStore:
    def __init__(self, base_dir: Path | None = None) -> None: ...
```

- [ ] **Step 1: Write the RED model and state tests.** Include concrete assertions such as:

```python
@pytest.mark.parametrize("value", ["0:00", "09:0", "09:00 ", "24:00", "12:60", "-1:00"])
def test_parse_daily_time_rejects_non_strict_values(value: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        parse_daily_time(value)

def test_operational_equality_ignores_only_enabled_at(registration: ScheduleRegistration) -> None:
    later = registration.model_copy(update={"enabled_at": datetime(2030, 1, 1, tzinfo=UTC)})
    assert operationally_equal(registration, later)
    assert not operationally_equal(registration, later.model_copy(update={"path_env": "/new/bin"}))

def test_corrupt_registration_fails_closed(tmp_path: Path) -> None:
    store = FileRegistrationStore(tmp_path)
    path = store.registration_path("a" * 64)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    assert store.load("a" * 64).kind is RegistrationLoadKind.CORRUPT
```

Add concrete tests for canonical identity and stale-but-readable metadata:

```python
@pytest.fixture
def registration_payload(tmp_path: Path) -> dict[str, object]:
    root = tmp_path.resolve()
    key = project_key(root)
    return {
        "schema_version": 1,
        "project_key": key,
        "project_root": root,
        "backend": SchedulerBackendKind.SYSTEMD_USER,
        "native_id": native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        "hour": 9,
        "minute": 0,
        "python_executable": Path("/opt/qualock/python"),
        "runner_working_directory": Path("/home/tester"),
        "path_env": "/usr/bin",
        "enabled_at": datetime(2026, 9, 2, tzinfo=UTC),
    }


@pytest.fixture
def registration(registration_payload: dict[str, object]) -> ScheduleRegistration:
    return ScheduleRegistration.model_validate(registration_payload)


def test_registration_accepts_absolute_paths_that_disappear_after_enable(registration: ScheduleRegistration) -> None:
    payload = registration.model_dump()
    payload.update(
        {
            "python_executable": Path("/missing/qualock-python"),
            "runner_working_directory": Path("/missing/runner-home"),
        }
    )
    stale = ScheduleRegistration.model_validate(payload)
    assert stale.python_executable == Path("/missing/qualock-python")
    assert stale.runner_working_directory == Path("/missing/runner-home")


def test_enabled_at_must_be_utc(registration_payload: dict[str, object]) -> None:
    registration_payload["enabled_at"] = "2026-09-02T09:00:00+07:00"
    with pytest.raises(ValidationError, match="UTC timestamp"):
        ScheduleRegistration.model_validate(registration_payload)


def test_native_ids_are_exact() -> None:
    key = "a" * 64
    assert native_id_for(SchedulerBackendKind.WINDOWS_TASK_SCHEDULER, key) == f"QuaLock-ReleaseMonitor-{key}"
    assert native_id_for(SchedulerBackendKind.SYSTEMD_USER, key) == f"qualock-release-monitor-{key}.timer"
    assert native_id_for(SchedulerBackendKind.LAUNCHD_AGENT, key) == f"io.qualock.release-monitor.{key}"
```

Also test `00:00`, `23:59`, lowercase 64-hex keys, `project_key == release_monitor.state.project_key(project_root)`, non-canonical project roots, relative Python/home paths, hour/minute bounds, schema/extra-field rejection, native-ID/key/backend mismatch rejection, atomic replacement with same-directory UUID temp cleanup, POSIX `0700`/`0600` permissions, and missing versus unreadable/malformed registration. Missing Python/project/home paths remain valid registration metadata; enable/status/runner health checks own existence semantics.

- [ ] **Step 2: Run the focused tests and confirm RED.** Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py
```

Expected: failures are missing scheduler interfaces, not collection/environment errors.

- [ ] **Step 3: Implement the models and store with this central logic.** Import only `ProcessResult` and `run_process` from `qualock.run.process`; do not modify `qualock.run.process`. Reuse `qualock.release_monitor.state.project_key`. Compute the default base as `Path(user_state_dir("qualock")) / "release-scheduler" / "projects"`. Serialize UTF-8 JSON with a trailing newline, replace atomically from the per-project directory, apply POSIX `0o700` directories and `0o600` registration files on POSIX, and never delete `runs.log`.

```python
_DAILY_TIME = re.compile(r"^(\d{2}):(\d{2})$")
_BACKEND_LABELS = {
    SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: "Windows Task Scheduler",
    SchedulerBackendKind.SYSTEMD_USER: "systemd user timer",
    SchedulerBackendKind.LAUNCHD_AGENT: "macOS LaunchAgent",
}

def parse_daily_time(value: str) -> tuple[int, int]:
    match = _DAILY_TIME.fullmatch(value)
    if match is None:
        raise ValueError("daily time must use HH:MM")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("daily time must use HH:MM")
    return hour, minute

def backend_label(kind: SchedulerBackendKind) -> str:
    return _BACKEND_LABELS[kind]

def schedule_identity(root: Path, kind: SchedulerBackendKind) -> ScheduleIdentity:
    canonical_root = root.expanduser().resolve(strict=True)
    key = project_key(canonical_root)
    return ScheduleIdentity(key, kind, native_id_for(kind, key))

def native_id_for(kind: SchedulerBackendKind, key: str) -> str:
    prefix = {SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: "QuaLock-ReleaseMonitor-",
              SchedulerBackendKind.SYSTEMD_USER: "qualock-release-monitor-",
              SchedulerBackendKind.LAUNCHD_AGENT: "io.qualock.release-monitor."}[kind]
    suffix = ".timer" if kind is SchedulerBackendKind.SYSTEMD_USER else ""
    return f"{prefix}{key}{suffix}"

@field_validator("enabled_at")
@classmethod
def validate_enabled_at(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("enabled_at must be a UTC timestamp")
    return value

@model_validator(mode="after")
def validate_registration(self) -> "ScheduleRegistration":
    canonical_root = self.project_root.expanduser().resolve(strict=False)
    if not self.project_root.is_absolute() or canonical_root != self.project_root:
        raise ValueError("project_root must be an absolute canonical path")
    if re.fullmatch(r"[0-9a-f]{64}", self.project_key) is None:
        raise ValueError("project_key must be 64 lowercase hexadecimal characters")
    if self.project_key != project_key(canonical_root):
        raise ValueError("project_key does not match project_root")
    if self.native_id != native_id_for(self.backend, self.project_key):
        raise ValueError("native_id does not match backend and project_key")
    if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
        raise ValueError("hour or minute is outside the daily clock range")
    if not self.python_executable.is_absolute():
        raise ValueError("python_executable must be absolute")
    if not self.runner_working_directory.is_absolute():
        raise ValueError("runner_working_directory must be absolute")
    return self

def operationally_equal(left: ScheduleRegistration, right: ScheduleRegistration) -> bool:
    return (left.model_dump(exclude={"enabled_at"}) ==
            right.model_dump(exclude={"enabled_at"}))

class FileRegistrationStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (
            Path(user_state_dir("qualock")) / "release-scheduler" / "projects"
        )

    def project_dir(self, project_key: str) -> Path:
        return self.base_dir / project_key

    def registration_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "registration.json"

    def log_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "runs.log"

    def load(self, project_key: str) -> RegistrationLoad:
        if re.fullmatch(r"[0-9a-f]{64}", project_key) is None:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail="invalid project key")
        path = self.registration_path(project_key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return RegistrationLoad(RegistrationLoadKind.MISSING)
        except (OSError, UnicodeError) as exc:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail=str(exc))
        try:
            registration = ScheduleRegistration.model_validate_json(raw)
        except ValidationError as exc:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail=str(exc))
        if registration.project_key != project_key:
            return RegistrationLoad(
                RegistrationLoadKind.CORRUPT,
                detail="registration project key does not match state path",
            )
        return RegistrationLoad(RegistrationLoadKind.VALID, registration=registration)

    def save(self, registration: ScheduleRegistration) -> None:
        directory = self.project_dir(registration.project_key)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.registration_path(registration.project_key)
        temporary = directory / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(registration.model_dump_json() + "\n", encoding="utf-8")
            if os.name != "nt":
                directory.chmod(0o700)
                temporary.chmod(0o600)
            os.replace(temporary, destination)
            if os.name != "nt":
                destination.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, project_key: str) -> None:
        self.registration_path(project_key).unlink(missing_ok=True)
```

- [ ] **Step 4: Run GREEN/static checks.** Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/scheduler \
  src/qualock/release_monitor
```

- [ ] **Step 5: Commit.** Commit only Task 1 files with `git commit -m "feat: add scheduler registration model"`.

- [ ] **Reviewer gate:** Independently inspect the Task 1 diff and tests for exact identity/schema enforcement, strict time grammar, atomic state, POSIX user-only permissions, `enabled_at` exclusion only, no arbitrary command/environment fields, and corrupt/missing fail-closed distinction. Resolve all Critical/Important/P1/P2 findings before Task 2.

### Task 2: Public monitor preflight and fixed scheduler runner/logging

**Files:**
- Modify: `src/qualock/release_monitor/commands.py`
- Modify: `src/qualock/release_monitor/__init__.py`
- Modify: `tests/unit/test_release_monitor_flow.py`
- Create: `src/qualock/scheduler/runner.py`
- Create: `tests/unit/test_scheduler_runner.py`

**Interfaces:**
- Consumes Task 1 `RegistrationStore`, `FileRegistrationStore`, `RegistrationLoadKind`, `ProcessRunner`, and canonical project identity.
- Produces public `MonitorPreflight` and `monitor_preflight(root)` for both `execute_monitor()` and Task 6 `enable_schedule()`.
- Produces the fixed scheduler runner; no later task may add an arbitrary runner command.

```python
@dataclass(frozen=True)
class MonitorPreflight:
    baseline_version: str
    baseline_sha256: str


def monitor_preflight(root: Path) -> MonitorPreflight: ...


def run_registered_monitor(
    project_key: str,
    *,
    store: RegistrationStore | None = None,
    process_runner: ProcessRunner = run_process,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int: ...


def main(argv: Sequence[str] | None = None) -> int: ...
```

- [ ] **Step 1: Write RED tests for the public preflight and fixed runner**

Update release-monitor tests so they patch `monitor_preflight`, never the removed `_prepare_context`. Add a direct ordered preflight test using the current source interfaces:

```python
def test_monitor_preflight_reuses_exact_freshness_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    lock = baseline_lock()
    monkeypatch.setattr(
        monitor_commands,
        "load_project",
        lambda root: events.append("load_project") or (object(), []),
    )
    monkeypatch.setattr(
        monitor_commands,
        "read_baseline_lock",
        lambda path: events.append("read_baseline_lock") or lock,
    )
    monkeypatch.setattr(
        monitor_commands,
        "suite_fingerprint",
        lambda canaries: events.append("suite_fingerprint") or "suite-now",
    )
    monkeypatch.setattr(
        monitor_commands,
        "config_fingerprint",
        lambda config: events.append("config_fingerprint") or "config-now",
    )
    monkeypatch.setattr(
        monitor_commands,
        "assert_suite_fresh",
        lambda *args: events.append("assert_suite_fresh"),
    )

    context = monitor_commands.monitor_preflight(tmp_path)

    assert events == [
        "load_project",
        "read_baseline_lock",
        "suite_fingerprint",
        "config_fingerprint",
        "assert_suite_fresh",
    ]
    assert context.baseline_version == lock.agent.version
    assert context.baseline_sha256 == baseline_sha256(lock)
```

Keep the existing stale/non-Codex/missing/malformed baseline tests and prove `execute_monitor()` still runs this preflight before release discovery or state lookup. A non-Codex lock must raise exactly `CommandError("release monitor supports only a Codex baseline")`.

Add this local test helper, then runner tests with a real temporary file standing in for the current interpreter and monkeypatch `qualock.scheduler.runner.sys.executable` to that path:

```python
def saved_registration(
    base_dir: Path,
    *,
    project_root: Path,
    python_executable: Path,
    path_env: str,
) -> tuple[FileRegistrationStore, ScheduleRegistration]:
    key = project_key(project_root)
    registration = ScheduleRegistration(
        project_key=key,
        project_root=project_root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=9,
        minute=0,
        python_executable=python_executable,
        runner_working_directory=base_dir.resolve(),
        path_env=path_env,
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    store = FileRegistrationStore(base_dir)
    store.save(registration)
    return store, registration


def test_runner_invokes_only_monitor_and_propagates_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state",
        project_root=project.resolve(),
        python_executable=python.resolve(),
        path_env="/registered/bin",
    )
    calls: list[tuple[list[str], Path | None, dict[str, str]]] = []
    monkeypatch.setattr("qualock.scheduler.runner.sys.executable", str(python.resolve()))

    def fake_run(argv, *, cwd, env, timeout_seconds):
        calls.append((list(argv), cwd, dict(env)))
        return ProcessResult(2, "blocked\n", "detail\n", 0.1, False)

    result = run_registered_monitor(
        registration.project_key,
        store=store,
        process_runner=fake_run,
        environ={"PATH": "/scheduler/bin", "KEEP": "yes"},
    )

    assert result == 2
    assert calls == [
        ([str(python.resolve()), "-m", "qualock.cli", "monitor"],
         project.resolve(),
         {"PATH": "/registered/bin", "KEEP": "yes"})
    ]
    log = store.log_path(registration.project_key).read_text(encoding="utf-8")
    assert "START project=" in log
    assert "blocked\ndetail\n" in log
    assert "EXIT code=2" in log
```

Also write explicit tests that invalid key syntax, missing/corrupt registration, recomputed key/root mismatch, missing project root, and registered-Python/current-Python mismatch never call the process runner and return `1`; logging failure cannot change a real child exit; `ProcessResult(None, ..., timed_out=True)` and an absent exit code return `1` with no retry; runner accepts no arbitrary command argument.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_scheduler_runner.py
```

Expected: failures are the missing public preflight/runner interfaces, not collection errors.

- [ ] **Step 3: Promote the current preflight exactly and implement the runner**

In `release_monitor/commands.py`, replace only the private context name/call site; do not alter release decision logic:

```python
@dataclass(frozen=True)
class MonitorPreflight:
    baseline_version: str
    baseline_sha256: str


def monitor_preflight(root: Path) -> MonitorPreflight:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    if lock.agent.name != "codex":
        raise CommandError("release monitor supports only a Codex baseline")
    return MonitorPreflight(
        baseline_version=lock.agent.version,
        baseline_sha256=baseline_sha256(lock),
    )
```

Export `MonitorPreflight` and `monitor_preflight` from `qualock.release_monitor.__init__`, and make `execute_monitor()` call `monitor_preflight(root)`.

In `scheduler/runner.py`, define every helper used by the runner:

```python
MONITOR_TIMEOUT_SECONDS = 86400.0
_PROJECT_KEY = re.compile(r"^[0-9a-f]{64}$")


def _utc_now(now: Callable[[], datetime] | None) -> str:
    value = now() if now is not None else datetime.now(UTC)
    return value.astimezone(UTC).isoformat()


def _append_log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        if os.name != "nt":
            path.chmod(0o600)
    except OSError:
        return


def _append_runner_error(
    path: Path,
    message: str,
    now: Callable[[], datetime] | None,
) -> None:
    _append_log(path, f"[{_utc_now(now)}] ERROR {message}\n")


def run_registered_monitor(
    project_key: str,
    *,
    store: RegistrationStore | None = None,
    process_runner: ProcessRunner = run_process,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    if _PROJECT_KEY.fullmatch(project_key) is None:
        return 1
    active_store = store or FileRegistrationStore()
    loaded = active_store.load(project_key)
    log_path = active_store.log_path(project_key)
    if loaded.kind is not RegistrationLoadKind.VALID or loaded.registration is None:
        _append_runner_error(log_path, "registration unavailable", now)
        return 1

    registration = loaded.registration
    try:
        if registration.project_key != project_key:
            raise ValueError("registration project key does not match runner key")
        if project_key_for(registration.project_root) != project_key:
            raise ValueError("registration project root does not match runner key")
        if not registration.project_root.is_dir():
            raise ValueError("registered project root is unavailable")
        if not registration.python_executable.samefile(Path(sys.executable)):
            raise ValueError("registered Python does not match current Python")
    except (OSError, ValueError) as exc:
        _append_runner_error(log_path, str(exc), now)
        return 1

    child_env = dict(os.environ if environ is None else environ)
    child_env["PATH"] = registration.path_env
    argv = [sys.executable, "-m", "qualock.cli", "monitor"]
    _append_log(log_path, f"[{_utc_now(now)}] START project={registration.project_root}\n")
    try:
        result = process_runner(
            argv,
            cwd=registration.project_root,
            env=child_env,
            timeout_seconds=MONITOR_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        _append_runner_error(log_path, str(exc), now)
        return 1
    _append_log(log_path, result.stdout + result.stderr)
    exit_code = 1 if result.timed_out or result.exit_code is None else result.exit_code
    _append_log(log_path, f"[{_utc_now(now)}] EXIT code={exit_code}\n")
    return exit_code
```

Import `project_key` from `qualock.release_monitor.state` as `project_key_for` to avoid shadowing the runner argument. The production argv therefore uses `sys.executable` exactly; tests alter that module attribute instead of adding a production command seam.

Implement `main()` with `argparse` and only required `--project-key`; unknown extra options are rejected by argparse and the key itself is validated by `run_registered_monitor()`:

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qualock.scheduler.runner")
    parser.add_argument("--project-key", required=True)
    args = parser.parse_args(argv)
    return run_registered_monitor(args.project_key)


if __name__ == "__main__":
    raise SystemExit(main())
```

There is no loop, retry, state decision, discovery, or qualification policy in the runner.

- [ ] **Step 4: Run GREEN, preservation, and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_cli.py \
  tests/unit/test_scheduler_runner.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/release_monitor \
  src/qualock/scheduler/runner.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_scheduler_runner.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/release_monitor \
  src/qualock/scheduler/runner.py
```

Require existing exact `codex@<latest>` delegation, freshness-before-discovery/state, remembered BLOCK exit `2`, INCOMPLETE retry behavior, and existing `qualock monitor` output mapping to stay green.

- [ ] **Step 5: Commit Task 2**

```bash
git add \
  src/qualock/release_monitor/commands.py \
  src/qualock/release_monitor/__init__.py \
  src/qualock/scheduler/runner.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_scheduler_runner.py
git commit -m "feat: add fixed release monitor scheduler runner"
```

**Reviewer gate:** Verify the public preflight is exactly the existing freshness chain, no release/state/qualification work occurs inside preflight, the runner accepts no arbitrary command, the production child is exactly `sys.executable -m qualock.cli monitor`, only PATH is restored, registration failures are fail-closed, real monitor exits are preserved, and logging is best-effort/user-only.

### Task 3: Windows Task Scheduler backend

**Files:**
- Create: `src/qualock/scheduler/backends/__init__.py`
- Create: `src/qualock/scheduler/backends/base.py`
- Create: `src/qualock/scheduler/backends/windows.py`
- Create: `tests/unit/test_scheduler_windows.py`

**Interfaces:**
- Consumes Task 1 `SchedulerBackendKind`, `ScheduleIdentity`, `ScheduleRegistration`, `NativeScheduleInspection`, `NativeScheduleState`, and `ProcessRunner`.
- Produces the shared typed backend protocol/errors used by Tasks 4-7.
- Produces Windows Task Scheduler XML/render/install/query/delete behavior with no shell.

```python
class SchedulerError(RuntimeError):
    pass


class SchedulerUnsupportedError(SchedulerError):
    pass


class SchedulerOperationalError(SchedulerError):
    def __init__(self, message: str, *, rollback_uncertain: bool = False) -> None:
        super().__init__(message)
        self.rollback_uncertain = rollback_uncertain


class SchedulerBackend(Protocol):
    kind: SchedulerBackendKind

    def probe(self) -> None: ...
    def install(self, registration: ScheduleRegistration) -> None: ...
    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection: ...
    def remove(self, identity: ScheduleIdentity) -> None: ...


class WindowsTaskSchedulerBackend:
    kind = SchedulerBackendKind.WINDOWS_TASK_SCHEDULER

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        temp_dir: Path | None = None,
        user_id: str | None = None,
    ) -> None: ...
```

- [ ] **Step 1: Write RED Windows contract tests**

Use only fake `ProcessResult` responses and `tmp_path`; never invoke the real host Task Scheduler. Add a local Task 1 registration factory and verify current-user/no-password XML plus separate action fields:

```python
def test_task_xml_is_current_user_least_privilege_and_shell_free(registration: ScheduleRegistration) -> None:
    payload = task_xml(registration, user_id=r"DOMAIN\alice")
    root = ET.fromstring(payload)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert root.findtext(".//t:UserId", namespaces=ns) == r"DOMAIN\alice"
    assert root.findtext(".//t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext(".//t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext(".//t:StartWhenAvailable", namespaces=ns) == "true"
    assert root.findtext(".//t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert root.findtext(".//t:DaysInterval", namespaces=ns) == "1"
    assert root.findtext(".//t:Command", namespaces=ns) == str(registration.python_executable)
    assert root.findtext(".//t:Arguments", namespaces=ns) == subprocess.list2cmdline(
        ["-m", "qualock.scheduler.runner", "--project-key", registration.project_key]
    )
    assert root.findtext(".//t:WorkingDirectory", namespaces=ns) == str(
        registration.runner_working_directory
    )
    text = payload.decode("utf-8")
    assert "Password" not in text
    assert "cmd.exe" not in text
    assert "powershell" not in text.lower()
```

Use special-character Python/home paths and assert XML parsing round-trips them literally. The fixed `2000-01-01` part of `StartBoundary` is only a deterministic date anchor; tests and inspection compare the requested local `HH:MM` and daily interval, not that date as user state.

Add exact argv and unverified-presence tests:

```python
def test_present_task_without_registration_is_unverified(identity: ScheduleIdentity) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, env, timeout_seconds):
        calls.append(list(argv))
        return ProcessResult(
            0,
            '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"></Task>',
            "",
            0.01,
            False,
        )

    backend = WindowsTaskSchedulerBackend(
        process_runner=fake_run,
        which=lambda name: name,
        user_id="alice",
    )

    inspection = backend.inspect(identity, None)

    assert inspection.state is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    assert calls[-1] == ["schtasks.exe", "/Query", "/TN", identity.native_id, "/XML"]
```

Also test `install()` uses `['schtasks.exe', '/Create', '/TN', native_id, '/XML', <temp>, '/F']`, `remove()` uses `['schtasks.exe', '/Delete', '/TN', native_id, '/F']`, the temp XML is deleted in `finally` on both success and error, known missing task is idempotent/MISSING, malformed XML is `PRESENT_BUT_UNVERIFIED`, and time/command/arguments/working-directory/UserId/principal/settings drift is `DRIFTED`. `probe()` with missing `schtasks.exe` raises `SchedulerUnsupportedError`; timeout/access/other query failures raise `SchedulerOperationalError`.

- [ ] **Step 2: Run Windows tests to verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_windows.py
```

Expected: missing backend/base interfaces fail.

- [ ] **Step 3: Implement the shared backend base and Windows renderer/argv exactly**

In `backends/base.py`, implement the three errors and protocol exactly as declared above. In `windows.py`, use `getpass.getuser()` only as the default current-user identity and `xml.etree.ElementTree` for XML; never build `/TR` or a shell command.

```python
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
WINDOWS_TIMEOUT_SECONDS = 30.0


def _q(tag: str) -> str:
    return f"{{{TASK_NS}}}{tag}"


def runner_arguments(registration: ScheduleRegistration) -> str:
    return subprocess.list2cmdline(
        ["-m", "qualock.scheduler.runner", "--project-key", registration.project_key]
    )


def task_xml(registration: ScheduleRegistration, *, user_id: str) -> bytes:
    ET.register_namespace("", TASK_NS)
    task = ET.Element(_q("Task"), {"version": "1.4"})
    triggers = ET.SubElement(task, _q("Triggers"))
    calendar = ET.SubElement(triggers, _q("CalendarTrigger"))
    ET.SubElement(calendar, _q("StartBoundary")).text = (
        f"2000-01-01T{registration.hour:02d}:{registration.minute:02d}:00"
    )
    schedule = ET.SubElement(calendar, _q("ScheduleByDay"))
    ET.SubElement(schedule, _q("DaysInterval")).text = "1"

    principals = ET.SubElement(task, _q("Principals"))
    principal = ET.SubElement(principals, _q("Principal"), {"id": "CurrentUser"})
    ET.SubElement(principal, _q("UserId")).text = user_id
    ET.SubElement(principal, _q("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, _q("RunLevel")).text = "LeastPrivilege"

    settings = ET.SubElement(task, _q("Settings"))
    ET.SubElement(settings, _q("StartWhenAvailable")).text = "true"
    ET.SubElement(settings, _q("MultipleInstancesPolicy")).text = "IgnoreNew"

    actions = ET.SubElement(task, _q("Actions"), {"Context": "CurrentUser"})
    execute = ET.SubElement(actions, _q("Exec"))
    ET.SubElement(execute, _q("Command")).text = str(registration.python_executable)
    ET.SubElement(execute, _q("Arguments")).text = runner_arguments(registration)
    ET.SubElement(execute, _q("WorkingDirectory")).text = str(
        registration.runner_working_directory
    )
    return ET.tostring(task, encoding="utf-8", xml_declaration=True)


def create_argv(native_id: str, xml_path: Path) -> list[str]:
    return ["schtasks.exe", "/Create", "/TN", native_id, "/XML", str(xml_path), "/F"]


def query_argv(native_id: str) -> list[str]:
    return ["schtasks.exe", "/Query", "/TN", native_id, "/XML"]


def delete_argv(native_id: str) -> list[str]:
    return ["schtasks.exe", "/Delete", "/TN", native_id, "/F"]
```

`install()` writes `task_xml(...)` to a closed same-host temp file under the injected `temp_dir`, runs `create_argv`, converts timeout/absent/nonzero to `SchedulerOperationalError`, and unlinks the XML in `finally`. `inspect()` first queries; known missing task returns `MISSING`; an existing task with `expected is None` returns `PRESENT_BUT_UNVERIFIED`; malformed/unreadable XML returns `PRESENT_BUT_UNVERIFIED`; otherwise parse and compare local hour/minute, `DaysInterval=1`, UserId, InteractiveToken, LeastPrivilege, StartWhenAvailable, IgnoreNew, Command, Arguments, and WorkingDirectory to return `MATCHING` or `DRIFTED`. `remove()` treats only the tested missing-task response as already removed; all other failures are operational.

- [ ] **Step 4: Run GREEN and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_scheduler_windows.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler/backends/base.py \
  src/qualock/scheduler/backends/windows.py \
  tests/unit/test_scheduler_windows.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/scheduler
```

- [ ] **Step 5: Commit Task 3**

```bash
git add \
  src/qualock/scheduler/backends/__init__.py \
  src/qualock/scheduler/backends/base.py \
  src/qualock/scheduler/backends/windows.py \
  tests/unit/test_scheduler_windows.py
git commit -m "feat: add Windows scheduler backend"
```

**Reviewer gate:** Verify current-user `UserId` + InteractiveToken + LeastPrivilege, no password, local daily trigger, StartWhenAvailable, IgnoreNew, separate Command/Arguments/WorkingDirectory, no shell, exact schtasks argv, expected=None semantics, drift coverage, and temp cleanup. No Critical/Important/P1/P2 finding may remain.

### Task 4: Linux `systemd --user` backend

**Files:**
- Create: `src/qualock/scheduler/backends/systemd.py`
- Create: `tests/unit/test_scheduler_systemd.py`
- Modify: `src/qualock/scheduler/backends/__init__.py`

**Interfaces:**
- Consumes Task 3 shared backend errors/protocol and Task 1 registration/process interfaces.
- Produces one deterministic user `.service`/`.timer` pair and `SystemdUserBackend`.

```python
def systemd_escape_argument(value: str) -> str: ...

def render_service(registration: ScheduleRegistration) -> str: ...
def render_timer(registration: ScheduleRegistration) -> str: ...


class SystemdUserBackend:
    kind = SchedulerBackendKind.SYSTEMD_USER

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        config_home: Path | None = None,
        home: Path | None = None,
    ) -> None: ...
```

- [ ] **Step 1: Write RED systemd backend tests**

Use fake process results and an injected config/home tree. Assert exact service/timer rendering, including the project key in both descriptions:

```python
def test_systemd_rendering_is_shell_free_persistent_and_local(registration: ScheduleRegistration) -> None:
    key = registration.project_key
    service = render_service(registration)
    timer = render_timer(registration)

    assert f"Description=QuaLock release monitor {key}\n" in service
    assert "Type=oneshot\n" in service
    assert "qualock.scheduler.runner" in service
    assert "/bin/sh" not in service
    assert "bash" not in service
    assert f"Description=QuaLock daily release monitor {key}\n" in timer
    assert f"OnCalendar=*-*-* {registration.hour:02d}:{registration.minute:02d}:00\n" in timer
    assert "Persistent=true\n" in timer
    assert "AccuracySec=1min\n" in timer
    assert f"Unit=qualock-release-monitor-{key}.service\n" in timer
    assert "WantedBy=timers.target\n" in timer
```

Use a Python/home path containing spaces, quotes, backslashes, and `%`; assert the quoted unit text preserves it with backslashes/quotes escaped and `%` doubled so no systemd specifier expansion changes the literal value.

Assert exact manager argv and active-state inspection:

```python
def test_matching_timer_requires_enabled_and_active(
    registration: ScheduleRegistration,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, env, timeout_seconds):
        calls.append(list(argv))
        key = tuple(argv)
        if key == ("systemctl", "--user", "is-enabled", registration.native_id):
            return ProcessResult(0, "enabled\n", "", 0.01, False)
        if key == ("systemctl", "--user", "is-active", registration.native_id):
            return ProcessResult(0, "active\n", "", 0.01, False)
        return ProcessResult(0, "", "", 0.01, False)

    backend = SystemdUserBackend(
        process_runner=fake_run,
        which=lambda name: name,
        config_home=tmp_path / "xdg",
        home=tmp_path / "home",
    )
    backend.install(registration)

    inspection = backend.inspect(
        schedule_identity(registration.project_root, registration.backend),
        registration,
    )

    assert inspection.state is NativeScheduleState.MATCHING
    assert ["systemctl", "--user", "is-enabled", registration.native_id] in calls
    assert ["systemctl", "--user", "is-active", registration.native_id] in calls
```

The actual test helper may write `render_service()`/`render_timer()` bytes directly rather than exposing a production-only test method. Also test `inspect(identity, None)` returns `PRESENT_BUT_UNVERIFIED` when either owned unit files or a loaded timer exists; missing files/timer returns `MISSING`; byte drift, disabled state, or inactive state returns `DRIFTED` when an expected registration exists.

Test exact process arrays: `['systemctl', '--user', 'show-environment']`, `daemon-reload`, `enable --now <timer>`, `is-enabled <timer>`, `is-active <timer>`, and `disable --now <timer>`. Assert unit root is `${XDG_CONFIG_HOME}/systemd/user` when configured and `~/.config/systemd/user` otherwise. Removal of an already-missing timer is successful and still removes owned stale files; no command contains `linger`, `loginctl`, `cron`, or a shell.

- [ ] **Step 2: Run systemd tests to verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_systemd.py
```

- [ ] **Step 3: Implement canonical rendering, user-manager probe, install, inspect, and remove**

Use systemd command-line quoting, not a shell:

```python
SYSTEMD_TIMEOUT_SECONDS = 30.0


def systemd_escape_argument(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def service_name(key: str) -> str:
    return f"qualock-release-monitor-{key}.service"


def render_service(registration: ScheduleRegistration) -> str:
    argv = [
        str(registration.python_executable),
        "-m",
        "qualock.scheduler.runner",
        "--project-key",
        registration.project_key,
    ]
    command = " ".join(systemd_escape_argument(value) for value in argv)
    return (
        "[Unit]\n"
        f"Description=QuaLock release monitor {registration.project_key}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={systemd_escape_argument(str(registration.runner_working_directory))}\n"
        f"ExecStart={command}\n"
    )


def render_timer(registration: ScheduleRegistration) -> str:
    return (
        "[Unit]\n"
        f"Description=QuaLock daily release monitor {registration.project_key}\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {registration.hour:02d}:{registration.minute:02d}:00\n"
        "Persistent=true\n"
        "AccuracySec=1min\n"
        f"Unit={service_name(registration.project_key)}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def manager_probe_argv() -> list[str]:
    return ["systemctl", "--user", "show-environment"]


def reload_argv() -> list[str]:
    return ["systemctl", "--user", "daemon-reload"]


def enable_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "enable", "--now", timer]


def enabled_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "is-enabled", timer]


def active_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "is-active", timer]


def disable_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "disable", "--now", timer]
```

The backend resolves its unit directory as `(config_home or Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))) / "systemd" / "user"`, where `home` defaults to `Path.home()`. `probe()` requires `systemctl` and successful non-mutating `show-environment`; unavailable user manager raises `SchedulerUnsupportedError`, including WSL without usable user systemd. Install atomically replaces exactly the deterministic service/timer files with same-directory UUID temps, then `daemon-reload`, then `enable --now`.

Inspection treats native presence without `expected` as `PRESENT_BUT_UNVERIFIED`; with `expected`, require byte equality with both renderers, `is-enabled` success/`enabled`, and `is-active` success/`active` for `MATCHING`. Any owned-byte or enable/active mismatch is `DRIFTED`; a truly absent timer/files is `MISSING`. Removal runs `disable --now` first, tolerates only the tested missing-unit result, then unlinks the two owned files and `daemon-reload`. Do not invoke `loginctl`, enable lingering, create cron state, add retry loops, or wrap `ExecStart` in a shell. The stable timer activates a `Type=oneshot` service, so systemd does not start a second copy while that unit is active.

- [ ] **Step 4: Run GREEN and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_scheduler_systemd.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler/backends/systemd.py \
  tests/unit/test_scheduler_systemd.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/scheduler
```

- [ ] **Step 5: Commit Task 4**

```bash
git add \
  src/qualock/scheduler/backends/__init__.py \
  src/qualock/scheduler/backends/systemd.py \
  tests/unit/test_scheduler_systemd.py
git commit -m "feat: add systemd user scheduler backend"
```

**Reviewer gate:** Verify XDG user-only paths, exact key-bearing service/timer text, correct escaping including `%`, Persistent/local wall clock, enabled+active inspection, expected=None semantics, no shell/cron/linger, stable oneshot no-overlap behavior, exact argv, and idempotent removal. No Critical/Important/P1/P2 finding may remain.

### Task 5: macOS LaunchAgent backend

**Files:**
- Create: `src/qualock/scheduler/backends/launchd.py`
- Create: `tests/unit/test_scheduler_launchd.py`
- Modify: `src/qualock/scheduler/backends/__init__.py`

**Interfaces:**
- Consumes Task 3 shared backend errors/protocol and Task 1 registration/process interfaces.
- Produces one deterministic current-user LaunchAgent at `~/Library/LaunchAgents/io.qualock.release-monitor.<key>.plist`.

```python
def render_plist(registration: ScheduleRegistration) -> bytes: ...


class LaunchdAgentBackend:
    kind = SchedulerBackendKind.LAUNCHD_AGENT

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        home: Path | None = None,
        uid: int | None = None,
    ) -> None: ...
```

- [ ] **Step 1: Write RED LaunchAgent tests**

Parse generated bytes with `plistlib.loads` and assert the exact fixed action and local schedule:

```python
def test_launchagent_plist_is_fixed_user_action(registration: ScheduleRegistration) -> None:
    payload = plistlib.loads(render_plist(registration))

    assert payload == {
        "Label": f"io.qualock.release-monitor.{registration.project_key}",
        "ProgramArguments": [
            str(registration.python_executable),
            "-m",
            "qualock.scheduler.runner",
            "--project-key",
            registration.project_key,
        ],
        "WorkingDirectory": str(registration.runner_working_directory),
        "StartCalendarInterval": {
            "Hour": registration.hour,
            "Minute": registration.minute,
        },
        "RunAtLoad": False,
    }
```

Use a runner-home/Python path with spaces and XML-special characters and assert `plistlib` round-trips the literal values; `ProgramArguments` remains an array, never a command string. Assert the only owned path is `<home>/Library/LaunchAgents/io.qualock.release-monitor.<key>.plist`, never `/Library/LaunchDaemons`.

Add exact GUI-domain process tests:

```python
def test_present_launchagent_without_registration_is_unverified(
    identity: ScheduleIdentity,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, env, timeout_seconds):
        calls.append(list(argv))
        return ProcessResult(0, "service = {...}\n", "", 0.01, False)

    backend = LaunchdAgentBackend(
        process_runner=fake_run,
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    inspection = backend.inspect(identity, None)

    assert inspection.state is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    assert ["launchctl", "print", f"gui/501/{identity.native_id}"] in calls
```

Also assert install first best-effort boots out the same stable label, atomically replaces the plist, then runs `['launchctl', 'bootstrap', 'gui/<uid>', '<plist>']`; inspection compares canonical plist bytes and `launchctl print gui/<uid>/<label>` loaded state; removal bootouts then removes only the plist. Known absent service is idempotent, malformed/missing plist plus loaded service is unverified/drifted as appropriate, no argv contains a shell, and no extra catch-up or overlap helper is created.

- [ ] **Step 2: Run LaunchAgent tests to verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_launchd.py
```

- [ ] **Step 3: Implement canonical plist and GUI-domain operations**

Use only `plistlib`, user paths, and exact argv arrays:

```python
LAUNCHD_TIMEOUT_SECONDS = 30.0


def render_plist(registration: ScheduleRegistration) -> bytes:
    payload: dict[str, object] = {
        "Label": registration.native_id,
        "ProgramArguments": [
            str(registration.python_executable),
            "-m",
            "qualock.scheduler.runner",
            "--project-key",
            registration.project_key,
        ],
        "WorkingDirectory": str(registration.runner_working_directory),
        "StartCalendarInterval": {
            "Hour": registration.hour,
            "Minute": registration.minute,
        },
        "RunAtLoad": False,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def launch_domain(uid: int) -> str:
    return f"gui/{uid}"


def service_target(uid: int, label: str) -> str:
    return f"{launch_domain(uid)}/{label}"


def bootstrap_argv(uid: int, plist_path: Path) -> list[str]:
    return ["launchctl", "bootstrap", launch_domain(uid), str(plist_path)]


def bootout_argv(uid: int, label: str) -> list[str]:
    return ["launchctl", "bootout", service_target(uid, label)]


def print_argv(uid: int, label: str) -> list[str]:
    return ["launchctl", "print", service_target(uid, label)]
```

`home` defaults to `Path.home()` and `uid` to `os.getuid()`; `probe()` requires `launchctl` plus a nonnegative UID and otherwise raises `SchedulerUnsupportedError`. The plist path is exactly `home / "Library" / "LaunchAgents" / f"{native_id}.plist"`. Install treats only the tested absent-service bootout result as harmless, writes a same-directory UUID temp with POSIX `0600` where practical, atomically replaces the plist, then requires successful `bootstrap`.

Inspection checks both backend-owned plist and GUI service presence. If no trusted expected registration exists but either is present, return `PRESENT_BUT_UNVERIFIED`; if neither exists, `MISSING`. With expected registration, canonical plist byte mismatch or missing/unloaded GUI job is `DRIFTED`; exact bytes plus loaded label is `MATCHING`. Removal bootouts first; on a real bootout error leave the plist for retry, while a known absent service proceeds to unlink the plist. The stable single launchd label supplies one current-user job identity; do not add KeepAlive, a second label, a LaunchDaemon, a shell wrapper, or a second catch-up mechanism.

- [ ] **Step 4: Run GREEN and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_scheduler_launchd.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler/backends/launchd.py \
  tests/unit/test_scheduler_launchd.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/scheduler
```

- [ ] **Step 5: Commit Task 5**

```bash
git add \
  src/qualock/scheduler/backends/__init__.py \
  src/qualock/scheduler/backends/launchd.py \
  tests/unit/test_scheduler_launchd.py
git commit -m "feat: add launchd scheduler backend"
```

**Reviewer gate:** Verify exact `io.qualock...` label, ProgramArguments array, runner-home working directory, GUI LaunchAgent-only domain/path, local StartCalendarInterval, expected=None semantics, loaded/plist drift, idempotent absence handling, stable no-overlap identity, no shell/LaunchDaemon/extra catch-up. No Critical/Important/P1/P2 finding may remain.

### Task 6: Backend selection and enable/status/disable orchestration

**Files:**
- Create: `src/qualock/scheduler/commands.py`
- Create: `tests/unit/test_scheduler_commands.py`
- Modify: `src/qualock/scheduler/__init__.py`

**Interfaces:**
- Consumes Task 2 `monitor_preflight`, Task 3 `SchedulerBackend`/typed errors, and all three native backends.
- Produces the only scheduler lifecycle orchestration used by Task 7 CLI.

```python
class ScheduleStatus(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    NEEDS_REPAIR = "NEEDS REPAIR"


@dataclass(frozen=True)
class ScheduleOutcome:
    status: ScheduleStatus
    project_root: Path
    backend: SchedulerBackendKind
    backend_label: str
    log_path: Path
    registration: ScheduleRegistration | None = None
    detail: str | None = None


BackendFactory = Callable[[], SchedulerBackend]


def default_backend_factories() -> Mapping[SchedulerBackendKind, BackendFactory]: ...
def select_backend(
    *,
    platform: str = sys.platform,
    factories: Mapping[SchedulerBackendKind, BackendFactory] | None = None,
) -> SchedulerBackend: ...


def enable_schedule(
    root: Path,
    at: str = "09:00",
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
    executable: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ScheduleOutcome: ...


def schedule_status(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome: ...


def disable_schedule(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome: ...
```

- [ ] **Step 1: Write RED orchestration tests for ordering, convergence, status, and rollback**

Use fake backends/stores only. Define these local test helpers first so the task is self-contained:

```python
def existing_python(tmp_path: Path) -> Path:
    path = tmp_path / "qualock-python"
    path.write_text("", encoding="utf-8")
    return path.resolve()


@pytest.fixture
def healthy_registration(tmp_path: Path) -> ScheduleRegistration:
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    python = existing_python(tmp_path)
    key = project_key(root.resolve())
    return ScheduleRegistration(
        project_key=key,
        project_root=root.resolve(),
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=9,
        minute=0,
        python_executable=python,
        runner_working_directory=home.resolve(),
        path_env="/usr/bin",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


class FakeBackend:
    kind = SchedulerBackendKind.SYSTEMD_USER

    def __init__(
        self,
        events: list[str],
        *,
        final_state: NativeScheduleState = NativeScheduleState.MATCHING,
        install_error: SchedulerOperationalError | None = None,
    ) -> None:
        self.events = events
        self.final_state = final_state
        self.install_error = install_error
        self.remove_error: SchedulerOperationalError | None = None
        self.installed: list[ScheduleRegistration] = []
        self.remove_calls = 0

    def probe(self) -> None:
        self.events.append("probe")

    def install(self, registration: ScheduleRegistration) -> None:
        self.events.append("install")
        if self.install_error is not None:
            raise self.install_error
        self.installed.append(registration)

    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection:
        self.events.append("inspect")
        return NativeScheduleInspection(self.final_state)

    def remove(self, identity: ScheduleIdentity) -> None:
        self.events.append("remove")
        self.remove_calls += 1
        if self.remove_error is not None:
            raise self.remove_error


class MemoryRegistrationStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.loaded = RegistrationLoad(RegistrationLoadKind.MISSING)
        self.saved: list[ScheduleRegistration] = []
        self.delete_calls = 0
        self.delete_error: OSError | None = None

    def project_dir(self, project_key: str) -> Path:
        return Path("/state") / project_key

    def registration_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "registration.json"

    def log_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "runs.log"

    def load(self, project_key: str) -> RegistrationLoad:
        self.events.append("load")
        return self.loaded

    def save(self, registration: ScheduleRegistration) -> None:
        self.events.append("save")
        self.saved.append(registration)

    def delete(self, project_key: str) -> None:
        self.events.append("delete")
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
```

Prove `enable` does freshness before backend mutation and final MATCHING is mandatory:

```python
def test_enable_orders_preflight_before_native_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    backend = FakeBackend(events, final_state=NativeScheduleState.MATCHING)
    store = MemoryRegistrationStore(events)
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: events.append("preflight") or MonitorPreflight("0.151.0", "f" * 64),
    )

    outcome = enable_schedule(
        tmp_path,
        backend=backend,
        store=store,
        executable=existing_python(tmp_path),
        home=tmp_path,
        environ={},
        now=lambda: datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert events == ["preflight", "probe", "load", "save", "install", "inspect"]
    assert outcome.status is ScheduleStatus.ENABLED
    assert outcome.registration is not None
    assert outcome.registration.path_env == os.defpath
```

Prove `PATH` is the only environment field captured and `os.defpath` is used only when `PATH` is absent, not when it is an empty string.

Add idempotence and refresh tests:

```python
def test_matching_enable_preserves_enabled_at_and_skips_reinstall(
    healthy_registration: ScheduleRegistration,
) -> None:
    events: list[str] = []
    backend = FakeBackend(events, final_state=NativeScheduleState.MATCHING)
    store = MemoryRegistrationStore(events)
    store.loaded = RegistrationLoad(RegistrationLoadKind.VALID, healthy_registration)

    outcome = enable_schedule(
        healthy_registration.project_root,
        at=f"{healthy_registration.hour:02d}:{healthy_registration.minute:02d}",
        backend=backend,
        store=store,
        executable=healthy_registration.python_executable,
        home=healthy_registration.runner_working_directory,
        environ={"PATH": healthy_registration.path_env},
        now=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert outcome.registration is healthy_registration
    assert store.saved == []
    assert backend.installed == []
```

If the operational registration is the same but native inspection is `DRIFTED`, repair it while preserving the old `enabled_at`. If requested time, Python path, captured `PATH`, backend, or runner home changes, write a new registration with a new UTC `enabled_at` and reinstall.

Exercise rollback with two independent cleanup failures:

```python
def test_enable_rollback_warns_when_native_remove_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend([], install_error=SchedulerOperationalError("install failed"))
    backend.remove_error = SchedulerOperationalError("remove failed")
    store = MemoryRegistrationStore([])
    store.delete_error = OSError("state delete failed")
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: MonitorPreflight("0.151.0", "f" * 64),
    )

    with pytest.raises(SchedulerOperationalError) as caught:
        enable_schedule(
            tmp_path,
            backend=backend,
            store=store,
            executable=existing_python(tmp_path),
            home=tmp_path,
            environ={"PATH": "/bin"},
        )

    assert caught.value.rollback_uncertain is True
    assert "native schedule may still be enabled" in str(caught.value)
    assert "qualock schedule status" in str(caught.value)
    assert "qualock schedule disable" in str(caught.value)
    assert backend.remove_calls == 1
    assert store.delete_calls == 1
```

Prove state-delete failure is appended to the error but never hides the original install/verify failure. `runs.log` must remain untouched.

Encode the complete status table plus health checks:

```python
@pytest.mark.parametrize(
    ("load_kind", "native_state", "expected"),
    [
        (RegistrationLoadKind.MISSING, NativeScheduleState.MISSING, ScheduleStatus.DISABLED),
        (RegistrationLoadKind.VALID, NativeScheduleState.MATCHING, ScheduleStatus.ENABLED),
        (RegistrationLoadKind.VALID, NativeScheduleState.MISSING, ScheduleStatus.NEEDS_REPAIR),
        (RegistrationLoadKind.VALID, NativeScheduleState.DRIFTED, ScheduleStatus.NEEDS_REPAIR),
        (RegistrationLoadKind.CORRUPT, NativeScheduleState.MISSING, ScheduleStatus.NEEDS_REPAIR),
        (RegistrationLoadKind.CORRUPT, NativeScheduleState.PRESENT_BUT_UNVERIFIED, ScheduleStatus.NEEDS_REPAIR),
        (RegistrationLoadKind.MISSING, NativeScheduleState.PRESENT_BUT_UNVERIFIED, ScheduleStatus.NEEDS_REPAIR),
    ],
)
def test_status_table(load_kind, native_state, expected, healthy_registration):
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(
        load_kind,
        healthy_registration if load_kind is RegistrationLoadKind.VALID else None,
        "corrupt" if load_kind is RegistrationLoadKind.CORRUPT else None,
    )
    backend = FakeBackend([], final_state=native_state)
    assert schedule_status(healthy_registration.project_root, backend=backend, store=store).status is expected
```

For the VALID/MATCHING case separately make registered project root, Python executable, and runner home exist. Delete each one in turn and require `NEEDS_REPAIR`. Backend mismatch also requires repair. Patch `monitor_preflight` to raise if called and prove both `schedule_status()` and `disable_schedule()` still work. Disable ordering is exactly `probe -> remove -> delete`; failed remove never deletes registration; missing native removal succeeds; delete failure after successful native removal is operational; logs are preserved.

- [ ] **Step 2: Run orchestration tests to verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_commands.py
```

- [ ] **Step 3: Implement backend selection, registration construction, convergence, and rollback**

Define every helper used by orchestration:

```python
def default_backend_factories() -> Mapping[SchedulerBackendKind, BackendFactory]:
    return {
        SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: WindowsTaskSchedulerBackend,
        SchedulerBackendKind.SYSTEMD_USER: SystemdUserBackend,
        SchedulerBackendKind.LAUNCHD_AGENT: LaunchdAgentBackend,
    }


def select_backend(
    *,
    platform: str = sys.platform,
    factories: Mapping[SchedulerBackendKind, BackendFactory] | None = None,
) -> SchedulerBackend:
    active = factories or default_backend_factories()
    if platform == "win32":
        kind = SchedulerBackendKind.WINDOWS_TASK_SCHEDULER
    elif platform.startswith("linux"):
        kind = SchedulerBackendKind.SYSTEMD_USER
    elif platform == "darwin":
        kind = SchedulerBackendKind.LAUNCHD_AGENT
    else:
        raise SchedulerUnsupportedError(f"unsupported scheduler platform: {platform}")
    return active[kind]()


def _build_registration(
    root: Path,
    backend: SchedulerBackend,
    hour: int,
    minute: int,
    *,
    executable: Path | None,
    home: Path | None,
    environ: Mapping[str, str] | None,
    now: Callable[[], datetime] | None,
) -> ScheduleRegistration:
    canonical_root = root.expanduser().resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("project root must be a directory")
    python_path = Path(sys.executable) if executable is None else executable
    if not python_path.is_absolute() or not python_path.is_file():
        raise ValueError("scheduler Python executable is unavailable")
    runner_home = (Path.home() if home is None else home).expanduser().resolve(strict=True)
    if not runner_home.is_dir():
        raise ValueError("runner working directory is unavailable")
    source_env = os.environ if environ is None else environ
    captured_path = source_env["PATH"] if "PATH" in source_env else os.defpath
    enabled_at = (now() if now is not None else datetime.now(UTC)).astimezone(UTC)
    identity = schedule_identity(canonical_root, backend.kind)
    return ScheduleRegistration(
        project_key=identity.project_key,
        project_root=canonical_root,
        backend=backend.kind,
        native_id=identity.native_id,
        hour=hour,
        minute=minute,
        python_executable=python_path,
        runner_working_directory=runner_home,
        path_env=captured_path,
        enabled_at=enabled_at,
    )


def _outcome(
    status: ScheduleStatus,
    root: Path,
    backend: SchedulerBackend,
    store: RegistrationStore,
    registration: ScheduleRegistration | None,
    detail: str | None = None,
) -> ScheduleOutcome:
    identity = schedule_identity(root, backend.kind)
    return ScheduleOutcome(
        status=status,
        project_root=root,
        backend=backend.kind,
        backend_label=backend_label(backend.kind),
        log_path=store.log_path(identity.project_key),
        registration=registration,
        detail=detail,
    )
```

Implement enable with the exact ordering and enabled-at rule:

```python
def enable_schedule(
    root: Path,
    at: str = "09:00",
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
    executable: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ScheduleOutcome:
    hour, minute = parse_daily_time(at)
    canonical_root = root.expanduser().resolve(strict=True)
    monitor_preflight(canonical_root)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    candidate = _build_registration(
        canonical_root,
        active_backend,
        hour,
        minute,
        executable=executable,
        home=home,
        environ=environ,
        now=now,
    )
    loaded = active_store.load(candidate.project_key)
    same_registration = (
        loaded.kind is RegistrationLoadKind.VALID
        and loaded.registration is not None
        and operationally_equal(loaded.registration, candidate)
    )
    if same_registration:
        assert loaded.registration is not None
        candidate = candidate.model_copy(update={"enabled_at": loaded.registration.enabled_at})
        inspection = active_backend.inspect(
            schedule_identity(canonical_root, active_backend.kind),
            loaded.registration,
        )
        if inspection.state is NativeScheduleState.MATCHING:
            return _outcome(
                ScheduleStatus.ENABLED,
                canonical_root,
                active_backend,
                active_store,
                loaded.registration,
            )

    try:
        active_store.save(candidate)
    except OSError as exc:
        raise SchedulerOperationalError(f"scheduler registration could not be saved: {exc}") from exc

    identity = schedule_identity(canonical_root, active_backend.kind)
    try:
        active_backend.install(candidate)
        inspection = active_backend.inspect(identity, candidate)
        if inspection.state is not NativeScheduleState.MATCHING:
            raise SchedulerOperationalError(
                f"native schedule verification was {inspection.state.value}"
            )
    except (SchedulerOperationalError, OSError) as exc:
        remove_error: Exception | None = None
        delete_error: Exception | None = None
        try:
            active_backend.remove(identity)
        except (SchedulerOperationalError, OSError) as cleanup_exc:
            remove_error = cleanup_exc
        try:
            active_store.delete(identity.project_key)
        except OSError as cleanup_exc:
            delete_error = cleanup_exc

        parts = [f"schedule enable failed: {exc}"]
        if remove_error is not None:
            parts.append(
                "native schedule may still be enabled; run `qualock schedule status` "
                "and `qualock schedule disable`"
            )
        if delete_error is not None:
            parts.append(f"registration cleanup also failed: {delete_error}")
        raise SchedulerOperationalError(
            "; ".join(parts),
            rollback_uncertain=remove_error is not None,
        ) from exc

    return _outcome(
        ScheduleStatus.ENABLED,
        canonical_root,
        active_backend,
        active_store,
        candidate,
    )
```

No other arbitrary options are accepted.

Implement status without preflight:

```python
def schedule_status(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome:
    canonical_root = root.expanduser().resolve(strict=True)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    identity = schedule_identity(canonical_root, active_backend.kind)
    loaded = active_store.load(identity.project_key)
    registration = loaded.registration if loaded.kind is RegistrationLoadKind.VALID else None
    trusted_expected = (
        registration
        if registration is not None and registration.backend is active_backend.kind
        else None
    )
    inspection = active_backend.inspect(identity, trusted_expected)

    if loaded.kind is RegistrationLoadKind.MISSING and inspection.state is NativeScheduleState.MISSING:
        return _outcome(ScheduleStatus.DISABLED, canonical_root, active_backend, active_store, None)
    if registration is None:
        return _outcome(
            ScheduleStatus.NEEDS_REPAIR,
            canonical_root,
            active_backend,
            active_store,
            None,
            loaded.detail or inspection.detail,
        )
    healthy = (
        registration.backend is active_backend.kind
        and registration.project_root == canonical_root
        and registration.project_root.is_dir()
        and registration.python_executable.is_file()
        and registration.runner_working_directory.is_dir()
    )
    status = (
        ScheduleStatus.ENABLED
        if healthy and inspection.state is NativeScheduleState.MATCHING
        else ScheduleStatus.NEEDS_REPAIR
    )
    return _outcome(
        status,
        canonical_root,
        active_backend,
        active_store,
        registration,
        inspection.detail,
    )
```

Implement disable without preflight with native removal strictly before metadata deletion:

```python
def disable_schedule(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome:
    canonical_root = root.expanduser().resolve(strict=True)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    identity = schedule_identity(canonical_root, active_backend.kind)
    try:
        active_backend.remove(identity)
    except SchedulerOperationalError:
        raise
    except OSError as exc:
        raise SchedulerOperationalError(f"native schedule could not be removed: {exc}") from exc
    try:
        active_store.delete(identity.project_key)
    except OSError as exc:
        raise SchedulerOperationalError(
            f"native schedule was removed but registration cleanup failed: {exc}"
        ) from exc
    return _outcome(
        ScheduleStatus.DISABLED,
        canonical_root,
        active_backend,
        active_store,
        None,
    )
```

A failed native remove leaves registration untouched. `FileRegistrationStore.delete()` removes only `registration.json`, so `runs.log` survives successful disable and rollback.

- [ ] **Step 4: Run all scheduler orchestration/native tests and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_runner.py \
  tests/unit/test_scheduler_windows.py \
  tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_release_monitor_flow.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler \
  src/qualock/release_monitor \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_release_monitor_flow.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/scheduler \
  src/qualock/release_monitor
```

- [ ] **Step 5: Commit Task 6**

```bash
git add src/qualock/scheduler/commands.py src/qualock/scheduler/__init__.py tests/unit/test_scheduler_commands.py
git commit -m "feat: orchestrate native release schedules"
```

**Reviewer gate:** Verify strict preflight placement, automatic platform backend with no fallback, only-PATH registration, existence checks at enable/status rather than registration load, correct enabled-at preservation, MATCHING-only success, independent rollback cleanup, explicit uncertainty warning, complete status table/health checks, no preflight for status/disable, native-before-metadata disable, and log preservation. No Critical/Important/P1/P2 finding may remain.

### Task 7: Typer `schedule` sub-app, literal-safe rendering, and exit mapping

**Files:**
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_scheduler_cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes Task 6 `enable_schedule`, `schedule_status`, `disable_schedule`, `ScheduleOutcome`, `ScheduleStatus`, and Task 3 typed scheduler errors.
- Produces exactly `qualock schedule enable [--at HH:MM]`, `qualock schedule status`, and `qualock schedule disable`; no public backend override or `schedule run` command.

```python
schedule_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(schedule_app, name="schedule")


def _render_schedule_outcome(
    outcome: ScheduleOutcome,
    *,
    mode: Literal["enable", "status", "disable"],
) -> None: ...


@schedule_app.command("enable")
def schedule_enable_command(
    at: Annotated[str, typer.Option("--at")] = "09:00",
) -> None: ...


@schedule_app.command("status")
def schedule_status_command() -> None: ...


@schedule_app.command("disable")
def schedule_disable_command() -> None: ...
```

- [ ] **Step 1: Write RED CLI tests for exact copy, literal rendering, and exits**

Patch only scheduler orchestration functions; ordinary CLI tests must never mutate a native scheduler. Define these local factories first:

```python
def sample_registration(root: Path, *, hour: int = 9, minute: int = 0) -> ScheduleRegistration:
    canonical = root.resolve()
    key = project_key(canonical)
    return ScheduleRegistration(
        project_key=key,
        project_root=canonical,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=hour,
        minute=minute,
        python_executable=Path("/opt/qualock/python"),
        runner_working_directory=canonical,
        path_env="/usr/bin",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def sample_outcome(
    status: ScheduleStatus,
    *,
    project_root: Path,
    detail: str | None = None,
) -> ScheduleOutcome:
    registration = sample_registration(project_root)
    return ScheduleOutcome(
        status=status,
        project_root=project_root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        backend_label="systemd user timer",
        log_path=project_root / "scheduler-state" / "runs.log",
        registration=registration,
        detail=detail,
    )
```

Characterize the exact successful enable shape from the spec:

```python
def test_schedule_enable_output_is_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    registration = sample_registration(root, hour=9, minute=0)
    outcome = ScheduleOutcome(
        status=ScheduleStatus.ENABLED,
        project_root=root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        backend_label="systemd user timer",
        log_path=tmp_path / "state" / "runs.log",
        registration=registration,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "enable_schedule", lambda root, at="09:00": outcome)

    result = runner.invoke(cli.app, ["schedule", "enable"])

    assert result.exit_code == 0
    assert result.stdout == (
        "QuaLock Release Schedule\n\n"
        "ENABLED\n\n"
        f"Project: {root}\n"
        "Runs: every day at 09:00 local time\n"
        "Backend: systemd user timer\n"
        f"Logs: {outcome.log_path}\n\n"
        "The scheduled job only runs `qualock monitor`.\n"
        "It does not update Codex or change your baseline.\n"
    )
```

Status with a valid registration must render `Daily time: HH:MM local time`, `Python: <absolute executable>`, backend, and logs. `DISABLED` may omit unknown time/Python. `NEEDS REPAIR` must include the remediation to run `qualock schedule enable` or `qualock schedule disable` and exit `4`.

Prove all dynamic values are literal-safe:

```python
def test_schedule_status_renders_dynamic_text_literally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project[root]"
    project.mkdir()
    outcome = sample_outcome(
        ScheduleStatus.NEEDS_REPAIR,
        project_root=project.resolve(),
        detail="native [danger] drift",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "schedule_status", lambda root: outcome)

    result = runner.invoke(cli.app, ["schedule", "status"])

    assert result.exit_code == 4
    assert "project[root]" in result.stdout
    assert "native [danger] drift" in result.stdout
```

Add table-driven exit tests:

```python
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SchedulerOperationalError("install failed"), 1),
        (SchedulerUnsupportedError("no user scheduler"), 3),
        (ConfigError("bad config"), 3),
        (CanaryLoadError("bad canary"), 3),
        (CommandError("release monitor supports only a Codex baseline"), 3),
        (FileNotFoundError("baseline.lock"), 3),
        (ValidationError.from_exception_data("BaselineLock", []), 3),
        (ValueError("daily time must use HH:MM"), 3),
        (BaselineStaleError("suite changed"), 4),
    ],
)
def test_schedule_enable_exit_mapping(error: Exception, expected: int, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "enable_schedule", lambda root, at="09:00": (_ for _ in ()).throw(error))
    assert runner.invoke(cli.app, ["schedule", "enable"]).exit_code == expected
```

If constructing a Pydantic `ValidationError` directly is brittle, create a malformed `BaselineLock` through the real Pydantic model and use that caught exception; the contract is still exit `3`.

Status: ENABLED/DISABLED `0`, NEEDS REPAIR `4`, inspection operational error `1`, unsupported backend `3`. Disable: disabled/already-disabled `0`, native removal operational `1`, unsupported `3`. Verify `enable --at 08:30` forwards exact `08:30`; invalid options, cron/weekday/interval/timezone/backend override, and `schedule run` do not exist. Keep the existing byte-exact `qualock check` test and full `qualock monitor` output/exit tests unchanged.

- [ ] **Step 2: Run CLI tests to verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_cli.py \
  tests/unit/test_release_monitor_cli.py
```

- [ ] **Step 3: Implement the sub-app and exact renderer without changing existing commands**

Add `Literal`/`NoReturn`, Pydantic `ValidationError`, scheduler imports, and the sub-app. Render one complete string with markup disabled so brackets/backticks/paths remain literal:

```python
def _render_schedule_outcome(
    outcome: ScheduleOutcome,
    *,
    mode: Literal["enable", "status", "disable"],
) -> None:
    lines = [
        "QuaLock Release Schedule",
        "",
        outcome.status.value,
        "",
        f"Project: {outcome.project_root}",
    ]
    registration = outcome.registration
    if mode == "enable" and registration is not None:
        lines.append(
            f"Runs: every day at {registration.hour:02d}:{registration.minute:02d} local time"
        )
    elif mode == "status" and registration is not None:
        lines.append(
            f"Daily time: {registration.hour:02d}:{registration.minute:02d} local time"
        )
    lines.append(f"Backend: {outcome.backend_label}")
    if mode == "status" and registration is not None:
        lines.append(f"Python: {registration.python_executable}")
    lines.append(f"Logs: {outcome.log_path}")
    if outcome.detail:
        lines.extend(["", outcome.detail])
    if outcome.status is ScheduleStatus.NEEDS_REPAIR:
        lines.extend(
            [
                "",
                "Run `qualock schedule enable` to repair it or "
                "`qualock schedule disable` to remove it.",
            ]
        )
    if mode == "enable":
        lines.extend(
            [
                "",
                "The scheduled job only runs `qualock monitor`.",
                "It does not update Codex or change your baseline.",
            ]
        )
    console.print("\n".join(lines) + "\n", end="", markup=False)


def _schedule_fail(error: Exception, code: int) -> NoReturn:
    console.print(str(error), markup=False)
    raise typer.Exit(code)
```

Implement enable with the stale exception before generic `ValueError`/validation handling:

```python
@schedule_app.command("enable")
def schedule_enable_command(
    at: Annotated[str, typer.Option("--at")] = "09:00",
) -> None:
    try:
        outcome = enable_schedule(Path.cwd(), at)
    except BaselineStaleError as exc:
        _schedule_fail(exc, 4)
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except (
        ConfigError,
        CanaryLoadError,
        CommandError,
        FileNotFoundError,
        ValidationError,
        ValueError,
    ) as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="enable")
```

Implement `status` and `disable` with only scheduler unsupported `3`, scheduler operational/unexpected `1`, and their outcome exits. Neither command imports/calls monitor preflight:

```python
@schedule_app.command("status")
def schedule_status_command() -> None:
    try:
        outcome = schedule_status(Path.cwd())
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="status")
    if outcome.status is ScheduleStatus.NEEDS_REPAIR:
        raise typer.Exit(4)


@schedule_app.command("disable")
def schedule_disable_command() -> None:
    try:
        outcome = disable_schedule(Path.cwd())
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="disable")
```

`SchedulerOperationalError` from uncertain rollback already contains the exact `native schedule may still be enabled` warning and both remediation commands; CLI prints it once, literally. Do not duplicate it or add an always-on/logged-out guarantee.

- [ ] **Step 4: Run GREEN, static, and preservation checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_cli.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_release_monitor_flow.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/cli.py \
  src/qualock/scheduler \
  src/qualock/release_monitor \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_cli.py \
  tests/unit/test_release_monitor_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/scheduler \
  src/qualock/release_monitor \
  src/qualock/cli.py
```

Require existing `check` easy output byte-equivalence and all existing `monitor` exits/copy to remain unchanged.

- [ ] **Step 5: Commit Task 7**

```bash
git add src/qualock/cli.py tests/unit/test_scheduler_cli.py tests/unit/test_cli.py
git commit -m "feat: add release schedule CLI"
```

**Reviewer gate:** Verify exactly three public schedule subcommands, default/strict time semantics, exact low-tech copy, literal-safe dynamic output, complete exit matrix, uncertain rollback warning printed once, no logged-out/always-on promise, and zero changes to existing check/monitor behavior. No Critical/Important/P1/P2 finding may remain.

### Task 8: README/ROADMAP, full verification, independent review, and delivery gates

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Verify only: `pyproject.toml`
- Verify only: every path changed from exact base `964ee25e58c531b74d0d4e496a546e69693f113e`

**Interfaces:**
- No new Python API.
- Documents exactly `qualock schedule enable`, `qualock schedule enable --at 08:30`, `qualock schedule status`, and `qualock schedule disable`, with current-user/session/catch-up limits and `qualock monitor` as the only safety authority.

- [ ] **Step 1: Run RED documentation acceptance checks**

Before editing, require these to fail because Batch #27 is not yet documented:

```bash
grep -Fq 'qualock schedule enable --at 08:30' README.md
grep -Fq 'qualock schedule status' README.md
grep -Fq 'qualock schedule disable' README.md
grep -Fq 'Native per-user daily release monitoring' ROADMAP.md
```

Confirm a nonzero result is missing scheduler documentation, not a missing file/command.

- [ ] **Step 2: Write the low-tech scheduler documentation**

Replace the README sentence that scheduling is future work with this public workflow:

```text
qualock schedule enable
qualock schedule enable --at 08:30
qualock schedule status
qualock schedule disable
```

Document all of these facts explicitly:

```text
- default is 09:00 local wall-clock time;
- Windows uses current-user Task Scheduler, Linux uses systemd --user, macOS uses a LaunchAgent;
- no admin/root, QuaLock daemon, cron fallback, shell wrapper, LaunchDaemon, or arbitrary scheduled command;
- the OS trigger starts only the fixed QuaLock runner, and the runner only executes qualock monitor;
- it never updates Codex or changes/rebuilds the baseline;
- only PATH is captured for sparse scheduler environments; credentials/other environment are not persisted;
- logs live under the per-user release-scheduler state path and are retained after disable;
- Windows/macOS depend on an available user session; Linux depends on the per-user systemd manager and QuaLock does not enable lingering;
- native DST/catch-up behavior is authoritative; powered-off execution is not promised and QuaLock adds no second retry scheduler;
- status means only native trigger health, not that the last monitor run passed;
- users should disable before moving/copying a project because project identity is path-derived and V1 does not migrate orphan schedules.
```

Move the Roadmap scheduler item from future work to a completed v0.1 bullet named exactly `Native per-user daily release monitoring`. Do not claim a release/tag/PyPI publication.

- [ ] **Step 3: Run GREEN documentation checks and focused preservation tests**

Re-run the four grep commands and require zero, then run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_cli.py \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_runner.py \
  tests/unit/test_scheduler_windows.py \
  tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py
```

Require all existing release-monitor/check preservation assertions plus all scheduler tests to pass.

- [ ] **Step 4: Commit README/ROADMAP only**

```bash
git add README.md ROADMAP.md
git commit -m "docs: explain native release scheduling"
```

The approved spec and this implementation plan are already separate planning commits; do not fold them into this documentation commit.

- [ ] **Step 5: Run the full exact-head local gate**

Record:

```bash
reviewed_head=$(git rev-parse HEAD)
```

Then require all commands to pass on that exact SHA:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/scheduler \
  src/qualock/release_monitor \
  src/qualock/cli.py \
  tests/unit/test_scheduler_models.py \
  tests/unit/test_scheduler_state.py \
  tests/unit/test_scheduler_runner.py \
  tests/unit/test_scheduler_windows.py \
  tests/unit/test_scheduler_systemd.py \
  tests/unit/test_scheduler_launchd.py \
  tests/unit/test_scheduler_commands.py \
  tests/unit/test_scheduler_cli.py \
  tests/unit/test_release_monitor_flow.py \
  tests/unit/test_release_monitor_cli.py \
  tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/scheduler \
  src/qualock/release_monitor
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check 964ee25e58c531b74d0d4e496a546e69693f113e..HEAD
git diff --name-only 964ee25e58c531b74d0d4e496a546e69693f113e..HEAD
git diff --exit-code \
  964ee25e58c531b74d0d4e496a546e69693f113e..HEAD -- \
  src/qualock/qualification \
  src/qualock/run \
  src/qualock/evidence \
  src/qualock/source \
  src/qualock/project_protection \
  src/qualock/project_setup
git diff --exit-code 964ee25e58c531b74d0d4e496a546e69693f113e..HEAD -- pyproject.toml
git status --short
```

Require a clean working tree after the docs commit. The name-only audit may contain only the approved spec/plan, expected scheduler package/tests, the release-monitor preflight refactor/tests, CLI/tests, README, and ROADMAP. Any unrelated path or dependency change is blocking. Inspect pytest skip reasons rather than silently accepting a newly skipped scheduler test; ordinary native backend tests must remain fake/injected and cross-platform.

- [ ] **Step 6: Run the independent whole-branch review**

Review exact base `964ee25e58c531b74d0d4e496a546e69693f113e` through `reviewed_head` against the approved spec and this plan. The reviewer must explicitly audit:

```text
no daemon / cron fallback / admin or root / shell
no arbitrary command and no environment persistence beyond PATH
runner invokes only qualock monitor and propagates its real exit
monitor_preflight is the current freshness chain and only enable calls it
status/disable still work when baseline/config/canaries are stale or missing
canonical project/native identity and corrupt/deleted registration fail closed
MATCHING-only ENABLED and full health checks for project/Python/runner home
idempotent enabled_at semantics and refresh on time/Python/PATH change
independent rollback cleanup + explicit may-still-be-enabled warning
Windows UserId + InteractiveToken + LeastPrivilege + no password + StartWhenAvailable + IgnoreNew
systemd XDG user units + active/enabled timer + Persistent + no lingering
macOS io.qualock LaunchAgent + GUI domain + no LaunchDaemon
local wall clock / DST / native-only catch-up / no overlap helpers
Batch #26 exact-candidate, dedupe, BLOCK, INCOMPLETE, check and monitor output preserved
protected execution-engine directories and pyproject.toml unchanged
```

No Critical/Important/P1/P2 finding may remain. Any accepted fix invalidates `reviewed_head`: make a focused commit with a RED regression test first, rerun Step 5, regenerate the review package, and re-review the fix before continuing.

- [ ] **Step 7: Push/open-or-update PR only after explicit authorization**

This is an external side effect. Only after explicit user authorization, push `feat/release-monitor-scheduler` at the exact approved `reviewed_head`, then open or update the PR without assuming its number. Confirm the remote PR head SHA equals `reviewed_head`.

Require exact-head GitHub CI success for Python 3.11, 3.12, and 3.13 plus the repository's configured Docker tmpfs smoke. If the remote head changes or any job fails, do not merge; fix through the same TDD/local-review cycle and establish a new reviewed head. Do not tag, publish a GitHub release, or publish PyPI.

- [ ] **Step 8: Squash merge and post-merge exact-SHA CI only after explicit merge authorization**

After a separate explicit merge authorization and all exact-head gates, squash merge only if the remote PR head still equals the reviewed SHA. Capture the returned main merge SHA and require post-merge main CI on that exact SHA for Python 3.11/3.12/3.13 plus the configured Docker tmpfs smoke. Report the exact SHA/checks. Do not tag, release, or publish PyPI.

**Reviewer gate:** Final evidence must include full pytest/static/compile/diff/prohibited-directory results at one exact reviewed head, an independent review with zero unresolved Critical/Important/P1/P2 findings, exact-head PR CI and post-merge CI when authorized, and no release artifact.
