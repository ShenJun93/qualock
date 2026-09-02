# Native Release Monitor Scheduler — Design

**Status:** Approved design for Batch #27  
**Date:** 2026-09-02  
**Base:** `964ee25` (`feat: add one-shot Codex release monitoring (#27)`)

## 1. Goal

Batch #27 makes the Batch #26 one-shot release monitor run automatically on a simple daily schedule without introducing a QuaLock daemon, cloud service, new qualification engine, or second source of safety truth.

The scheduler is deliberately thin:

```text
native per-user OS scheduler
  -> python -m qualock.scheduler.runner --project-key <key>
  -> runner loads QuaLock registration
  -> runner executes: python -m qualock.cli monitor
  -> existing Batch #26 freshness / discovery / dedupe / qualification logic
```

The native scheduler is only a clock. `qualock monitor` remains the complete authority for whether a Codex release needs qualification and for the resulting PASS/WARN/BLOCK/INCOMPLETE behavior.

## 2. User experience

V1 exposes exactly these public commands:

```bash
qualock schedule enable
qualock schedule enable --at 08:30
qualock schedule status
qualock schedule disable
```

`enable` defaults to `09:00` local wall-clock time.

`--at` accepts only strict 24-hour `HH:MM` values from `00:00` through `23:59`. V1 has no cron syntax, weekday selection, interval syntax, timezone flag, run-now command, or multiple schedules for one project.

A typical successful enable should read like:

```text
QuaLock Release Schedule

ENABLED

Project: /absolute/project/path
Runs: every day at 09:00 local time
Backend: systemd user timer
Logs: <user-state>/release-scheduler/projects/<project-key>/runs.log

The scheduled job only runs `qualock monitor`.
It does not update Codex or change your baseline.
```

`status` reports one of:

```text
ENABLED
DISABLED
NEEDS REPAIR
```

`disable` is idempotent. Disabling an already-disabled project succeeds.

All paths, warnings, and exception-derived output are rendered literally with Rich markup disabled.

## 3. Non-goals

Batch #27 does not add:

- a QuaLock daemon or continuously running background process;
- cron fallback;
- cloud scheduling or GitHub Actions scheduling;
- email, desktop, Slack, or other notifications;
- automatic Codex installation or upgrade;
- automatic baseline creation, repair, or re-baselining;
- automatic project migration when a repository is moved;
- schedule listing across all projects;
- more than one schedule per project;
- version bisect;
- new qualification, verdict, evidence, canary, grader, or Docker semantics;
- credential copying or arbitrary environment capture;
- release/tag/PyPI work.

A moved/copied project has a different project key and therefore a different schedule identity. Users should disable the schedule before moving a project. V1 does not discover or migrate orphaned schedules.

## 4. Design principles

### 4.1 Native scheduler, no QuaLock daemon

QuaLock uses the current user's native scheduler:

| Platform | Backend |
| --- | --- |
| Windows | Task Scheduler |
| Linux | `systemd --user` service + timer |
| macOS | per-user `launchd` LaunchAgent |

There is no fallback to cron or a home-grown daemon. If the native backend is unavailable, QuaLock fails clearly and leaves scheduling disabled.

### 4.2 Per-user, least privilege

No backend requires administrator/root privileges.

- Windows uses a current-user task with least privilege and does not store a password.
- Linux writes only user unit files and calls `systemctl --user`.
- macOS writes only to the user's LaunchAgents directory and uses the GUI user launchd domain.

### 4.3 Shell-free execution

No scheduled action invokes `cmd.exe`, PowerShell, `/bin/sh`, Bash, or another shell.

All QuaLock subprocess calls use argument arrays with `shell=False`.

The native scheduled command always targets the Python interpreter that ran `qualock schedule enable` and the fixed runner module:

```text
<absolute sys.executable> -m qualock.scheduler.runner --project-key <64-hex-key>
```

The registration never stores an arbitrary command to execute.

### 4.4 Existing monitor stays authoritative

The runner never reimplements freshness, release discovery, dedupe, version ordering, qualification, evidence generation, or verdict policy.

The runner executes exactly:

```text
<current runner sys.executable> -m qualock.cli monitor
```

with the registered project as working directory.

The child process exit code is returned unchanged by the runner.

## 5. Project identity and scheduler state

Batch #27 reuses the Batch #26 project identity algorithm:

```python
sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()
```

This is the same semantic identity used by `qualock.release_monitor.state.project_key`.

The default scheduler state root is:

```text
Path(user_state_dir("qualock"))
  / "release-scheduler"
  / "projects"
  / <project-key>
```

Per-project files:

```text
registration.json
runs.log
```

Temporary files used for atomic writes live in the same directory and are removed after success/failure.

Scheduler state is operational state, not trust evidence. Trust remains in the project config, canaries, baseline lock, qualification execution, and Batch #26 release-monitor logic.

## 6. Registration schema

V1 stores one immutable logical registration model:

```text
schema_version: 1
project_key: <64 lowercase hex>
project_root: <absolute resolved path>
backend: windows_task_scheduler | systemd_user | launchd_agent
native_id: <deterministic native identifier>
hour: 0..23
minute: 0..59
python_executable: <absolute path captured from sys.executable>
runner_working_directory: <absolute current-user home captured at enable time>
path_env: <PATH captured at enable time, or os.defpath if PATH is absent>
enabled_at: <UTC ISO timestamp>
```

Rules:

- unknown schema versions are invalid;
- `project_key` must equal the canonical key recomputed from `project_root`;
- `python_executable` and `runner_working_directory` must be absolute;
- `runner_working_directory` must exist as a directory when enabling;
- `native_id` must equal the deterministic ID derived from `project_key` for the recorded backend;
- `path_env` captures only `PATH`; no other environment variables or secrets are persisted;
- `enabled_at` is audit metadata and is excluded from desired-registration equality; an idempotent enable preserves the existing value when all operational fields already match;
- registration JSON is atomically replaced;
- malformed/invalid registration is never trusted as an enabled schedule.

Native IDs are stable across time changes:

```text
Windows: QuaLock-ReleaseMonitor-<project-key>
Linux:   qualock-release-monitor-<project-key>.timer
macOS:   io.qualock.release-monitor.<project-key>
```

The Linux service name uses the same stem with `.service`.

## 7. Why the runner exists

The native scheduler does not directly call `qualock monitor`.

A small fixed runner solves three cross-platform problems without creating a second safety engine:

1. it gives every backend one stable executable shape;
2. it restores only the captured `PATH` so npm/Docker discovery is not dependent on a sparse scheduler environment;
3. it writes consistent per-project run logs even on Windows, where Task Scheduler does not provide shell-free stdout redirection.

The runner accepts only:

```text
--project-key <64-hex-key>
```

It then:

1. locates `registration.json` under the fixed user-state root;
2. validates the V1 registration model;
3. verifies the argument key equals `registration.project_key`;
4. recomputes the key from `registration.project_root` and requires equality;
5. requires `Path(registration.python_executable)` to identify the same executable as the running `sys.executable`;
6. verifies the project root still exists and is a directory;
7. creates an environment by copying the runner's environment and replacing only `PATH` with `registration.path_env`;
8. appends a UTC start marker to `runs.log`;
9. executes `[sys.executable, "-m", "qualock.cli", "monitor"]` with `cwd=project_root`, `shell=False`, and the prepared environment;
10. appends the combined child output and a UTC completion/exit marker to `runs.log`;
11. returns the monitor child exit code unchanged.

If registration is missing/corrupt, identity validation fails, the Python executable mismatches, or the project root no longer exists, the runner must not invoke `qualock monitor`. It writes a best-effort error marker to `runs.log` and exits `1`.

The runner has no retry loop. The next attempt is controlled only by the native daily scheduler.

## 8. Monitor preflight reuse

`schedule enable` must verify that the project can safely participate in release monitoring without performing release discovery or qualification.

Batch #27 promotes the existing Batch #26 `_prepare_context(root)` logic into a public read-only monitor preflight function rather than duplicating it.

The public preflight must still perform, in this order:

```text
load_project(root)
read_baseline_lock(.qualock/baseline.lock)
suite_fingerprint(canaries)
config_fingerprint(config)
assert_suite_fresh(...)
require baseline agent == codex
```

It performs no npm query, no candidate resolution, no Docker qualification, no state dedupe, and no project modification.

`execute_monitor()` uses the same public preflight after the refactor, preserving Batch #26 behavior.

Only `schedule enable` requires this fresh monitor preflight.

`schedule status` and `schedule disable` must remain usable even if config/canaries/baseline are missing or stale.

## 9. Native backend interface

Production scheduling is behind an injectable protocol so unit tests never mutate the developer's real OS scheduler.

Conceptually:

```python
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
```

`probe()` is non-mutating.

`remove()` is idempotent: a missing native schedule is not an error.

`inspect()` distinguishes at minimum:

```text
MISSING
MATCHING
PRESENT_BUT_UNVERIFIED
DRIFTED
```

A valid registration plus a native schedule that differs in time, command, working identity, enablement/load state, or backend-owned files is `DRIFTED`.

## 10. Backend selection

Backend selection is automatic from `sys.platform`; V1 has no user override.

### Windows

Requirements:

- `sys.platform == "win32"`;
- `schtasks.exe` is available.

If unavailable, raise `SchedulerUnsupportedError`.

### Linux

Requirements:

- `sys.platform.startswith("linux")`;
- `systemctl` is available;
- a non-mutating `systemctl --user` probe confirms a reachable per-user systemd manager.

If the user manager is unavailable (including WSL installations without usable user systemd), raise `SchedulerUnsupportedError`.

There is no cron fallback.

### macOS

Requirements:

- `sys.platform == "darwin"`;
- `launchctl` is available;
- the current process has a user ID suitable for the `gui/<uid>` launchd domain.

Other platforms are unsupported.

## 11. Windows Task Scheduler contract

QuaLock creates/replaces one flat current-user task:

```text
QuaLock-ReleaseMonitor-<project-key>
```

Registration uses Task Scheduler XML, not `/TR`, so Command, Arguments, and WorkingDirectory remain separate fields.

The XML contract requires:

- a daily calendar trigger at the requested local `HH:MM`;
- current-user interactive-token execution;
- least privilege;
- no stored password;
- `StartWhenAvailable = true`;
- multiple-instance policy equivalent to "ignore new";
- an Exec action with:
  - Command = captured absolute Python executable;
  - Arguments = `-m qualock.scheduler.runner --project-key <key>`;
  - WorkingDirectory = registered runner working directory.

XML is generated with a real XML library. The Arguments field uses Windows command-line quoting rules; it is not a shell command.

Install uses `schtasks.exe /Create ... /XML ... /F` with an ephemeral XML file.

Inspection uses `schtasks.exe /Query /TN <id> /XML`, parses XML, and compares the relevant trigger/principal/action/settings fields against the expected registration.

Removal uses `schtasks.exe /Delete /TN <id> /F`.

The temporary XML file must be deleted in `finally`.
Because V1 uses an interactive-token current-user task, QuaLock does not store a Windows password and does not promise execution while the user is logged out. StartWhenAvailable requests catch-up when Task Scheduler can run the user task; powered-off time is never guaranteed.

## 12. Linux systemd user contract

QuaLock owns exactly two files under the user's systemd unit directory:
The default user-unit directory follows XDG_CONFIG_HOME/systemd/user and falls back to ~/.config/systemd/user.

```text
qualock-release-monitor-<key>.service
qualock-release-monitor-<key>.timer
```

Service contract:

```text
[Unit]
Description=QuaLock release monitor <key>

[Service]
Type=oneshot
WorkingDirectory=<escaped runner working directory>
ExecStart=<escaped absolute python> -m qualock.scheduler.runner --project-key <key>
```

Timer contract:

```text
[Unit]
Description=QuaLock daily release monitor <key>

[Timer]
OnCalendar=*-*-* HH:MM:00
Persistent=true
AccuracySec=1min
Unit=qualock-release-monitor-<key>.service

[Install]
WantedBy=timers.target
```

`ExecStart` is systemd command syntax, not a shell command. Rendering must correctly escape whitespace, quotes, backslashes, and `%` specifier characters.

Install flow writes both unit files atomically, runs:

```text
systemctl --user daemon-reload
systemctl --user enable --now <timer>
```

Inspection:

- byte-compares the owned unit files to the canonical rendering for a valid expected registration;
- verifies the timer is enabled;
- verifies the timer is loaded/active enough to receive future triggers.

Removal:

```text
systemctl --user disable --now <timer>
```

then removes the owned service/timer files and runs `daemon-reload`.

Missing units/timers are treated as already removed.

`Persistent=true` provides catch-up behavior for missed calendar events according to systemd semantics.
QuaLock does not enable or modify systemd user lingering. If the user manager is unavailable while logged out, native user-manager and Persistent behavior governs the next run when that manager becomes available. The timer/service pair must not create overlapping monitor processes for the same project.

## 13. macOS launchd contract

QuaLock owns one plist:

```text
~/Library/LaunchAgents/io.qualock.release-monitor.<key>.plist
```

The plist is generated with `plistlib` and contains:

```text
Label
ProgramArguments = [
  <absolute python>,
  "-m",
  "qualock.scheduler.runner",
  "--project-key",
  <key>
]
WorkingDirectory = <runner working directory>
StartCalendarInterval = {Hour: <hour>, Minute: <minute>}
RunAtLoad = false
```

No shell command string is stored.

Install converges by unloading the same label if already loaded, atomically replacing the plist, then calling the current-user `launchctl bootstrap gui/<uid> <plist>` path.

Inspection compares the canonical plist content and verifies the label is loaded in the current user's GUI domain.

Removal boots out the label if present and removes the owned plist.
A LaunchAgent exists only in the logged-in user GUI domain; QuaLock does not install a system LaunchDaemon. The same label must not launch overlapping monitor copies.

With `StartCalendarInterval`, macOS may run a missed event when waking from sleep; a job missed while the machine is powered off waits until a later scheduled occurrence. QuaLock does not add a second catch-up mechanism.

## 14. Daily time semantics

The requested `HH:MM` is always local wall-clock time.

QuaLock does not convert the schedule to fixed UTC.

Native scheduler local-time/DST behavior is authoritative.

The schedule should continue to target the same local clock reading across daylight-saving changes.

V1 guarantees one requested daily calendar trigger, not "exactly every 24 hours."

No backend promises execution while the machine is powered off. Windows current-user and macOS LaunchAgent execution also depends on an available user session; Linux depends on the per-user systemd manager. QuaLock relies on native catch-up semantics and adds no second scheduler.

## 15. Enable semantics

`qualock schedule enable [--at HH:MM]` is a convergence operation.

Order:

```text
1. resolve current project root
2. parse/validate local daily time
3. run fresh monitor preflight
4. select and probe native backend
5. compute project identity/native identity
6. build registration from:
     current root
     selected backend
     requested time
     sys.executable
     current user home as runner_working_directory
     current PATH or os.defpath
7. atomically save registration.json
8. install/replace native schedule
9. inspect native schedule against saved registration
10. require MATCHING
11. render ENABLED
```

If the already-installed native schedule and saved registration exactly match the requested registration, `enable` succeeds idempotently without unnecessary replacement.

If current Python path or captured `PATH` changed, the new registration differs and `enable` refreshes the native schedule.

### Enable failure / rollback

Scheduling must fail closed.

If native install or post-install verification fails after the registration was written:

1. attempt `backend.remove(identity)`;
2. remove `registration.json` best-effort;
3. preserve `runs.log`;
4. exit `1`.

If native rollback also fails, the error must explicitly say the native schedule **may still be enabled** and instruct the user to run:

```text
qualock schedule status
qualock schedule disable
```

QuaLock must never print ENABLED unless a final native inspection is `MATCHING`.

## 16. Status semantics

`qualock schedule status` does not require a valid/fresh QuaLock baseline.

It computes identity from the current resolved project root, selects/probes the current platform backend, loads registration conservatively, and inspects the deterministic native ID.

Outcome table:

| Registration | Native schedule | Result |
| --- | --- | --- |
| missing | missing | DISABLED |
| valid | exact match | ENABLED |
| valid | missing | NEEDS REPAIR |
| valid | drifted | NEEDS REPAIR |
| missing/corrupt | present | NEEDS REPAIR |
| corrupt | missing | NEEDS REPAIR |

Additional `NEEDS REPAIR` conditions include:

- registered Python executable no longer exists;
- registered runner working directory no longer exists;
- registered project root no longer exists;
- registration project key/root mismatch;
- registration backend does not match the current backend;
- native scheduler action/time/enablement differs from registration.

`NEEDS REPAIR` tells the user to run `qualock schedule enable` to converge or `qualock schedule disable` to remove it.

Status output includes:

```text
Status
Project
Daily time, when known
Backend
Python executable, when known
Logs path
```

It does not claim that the last monitor run succeeded.

## 17. Disable semantics

`qualock schedule disable` does not require a valid/fresh QuaLock baseline.

Order:

```text
1. resolve current project root
2. select/probe native backend
3. compute deterministic identity
4. remove native schedule idempotently
5. only after successful native removal, remove registration.json
6. preserve runs.log
7. render DISABLED
```

If native removal fails, keep `registration.json` so the user can retry and exit `1`.

If the native schedule is already missing, stale registration metadata is removed and the command succeeds.

## 18. Registration corruption and deletion

Registration state can reduce operational reliability but cannot create trust.

Rules:

- corrupt registration never yields ENABLED;
- a native schedule plus corrupt/missing registration yields NEEDS REPAIR;
- runner refuses to execute monitor without a valid matching registration;
- deleted registration therefore causes the scheduled runner to fail closed rather than qualifying an arbitrary project;
- `schedule disable` can still remove the native task because native identity is deterministic from the current project root;
- `schedule enable` overwrites corrupt registration with a fresh validated registration only after monitor preflight succeeds.

No schedule decision may suppress Batch #26 freshness checks.

## 19. Logging

Default log path:

```text
<user-state>/release-scheduler/projects/<key>/runs.log
```

Each runner invocation appends:

```text
[<UTC>] START project=<root>
<combined qualock monitor stdout/stderr>
[<UTC>] EXIT code=<integer>
```

Logging is best-effort but runner/monitor exit status remains authoritative.

On POSIX, QuaLock should create scheduler state directories/files with user-only permissions where practical (`0700` directory, `0600` files). Windows relies on the user's profile/state-directory ACLs.

V1 does not rotate logs and does not parse them into safety state.

## 20. Exit-code contract

Public schedule commands use the existing QuaLock CLI vocabulary where practical.

### `schedule enable`

| Situation | Exit |
| --- | ---: |
| enabled / already matching | 0 |
| native operational/install/verify/rollback failure | 1 |
| invalid `--at`, unsupported platform/backend, missing/invalid project/config/canary/baseline, non-Codex baseline | 3 |
| stale suite/config fingerprint | 4 |

### `schedule status`

| Situation | Exit |
| --- | ---: |
| ENABLED | 0 |
| DISABLED | 0 |
| backend inspection operational failure | 1 |
| unsupported platform/backend | 3 |
| NEEDS REPAIR | 4 |

### `schedule disable`

| Situation | Exit |
| --- | ---: |
| disabled / already disabled | 0 |
| native removal operational failure | 1 |
| unsupported platform/backend | 3 |

### Scheduled runner

The runner returns `qualock monitor`'s `0/1/2/3/4` unchanged after a real monitor invocation.

Runner registration/identity/bootstrap failure returns `1`.

## 21. CLI integration

`src/qualock/cli.py` adds a Typer sub-app named `schedule` with:

```text
enable
status
disable
```

The existing top-level commands remain unchanged.

There is no `schedule run` command because `qualock monitor` is already the explicit one-shot command.

Low-tech copy must emphasize:

```text
enable = ask the operating system to run QuaLock once a day
status = check whether that daily trigger is healthy
disable = remove the daily trigger
```

## 22. Expected code boundaries

New package:

```text
src/qualock/scheduler/
  __init__.py
  models.py
  state.py
  commands.py
  runner.py
  backends/
    __init__.py
    base.py
    windows.py
    systemd.py
    launchd.py
```

Expected tests:

```text
tests/unit/test_scheduler_models.py
tests/unit/test_scheduler_state.py
tests/unit/test_scheduler_runner.py
tests/unit/test_scheduler_commands.py
tests/unit/test_scheduler_windows.py
tests/unit/test_scheduler_systemd.py
tests/unit/test_scheduler_launchd.py
tests/unit/test_scheduler_cli.py
```

Expected existing-file modifications:

```text
src/qualock/release_monitor/commands.py
src/qualock/release_monitor/__init__.py
src/qualock/cli.py
tests/unit/test_release_monitor_flow.py
tests/unit/test_cli.py
README.md
ROADMAP.md
```

No intended modification to:

```text
src/qualock/qualification/
src/qualock/run/
src/qualock/evidence/
src/qualock/source/
src/qualock/project_protection/
src/qualock/project_setup/
```

No new third-party Python dependency is required. XML, plist, hashing, subprocess handling, and path manipulation use the standard library plus existing dependencies.

## 23. Testing strategy

### 23.1 Pure unit tests

All native backends use injected process runners and injected filesystem roots so tests do not mutate the real host scheduler.

Tests cover:

- strict `HH:MM` parsing boundaries;
- deterministic project/native identity;
- registration schema and canonical validation;
- corrupt/missing registration behavior;
- runner key/root/python validation;
- runner PATH restoration;
- runner exact child argv/cwd and exit-code propagation;
- runner refusal on missing/corrupt registration;
- append log format;
- enable idempotence;
- enable refresh when time/Python/PATH changes;
- enable rollback on install/verification failure;
- rollback-failure warning;
- status outcome table;
- disable idempotence and metadata retention on native removal failure.

### 23.2 Backend contract tests

Windows tests assert:

- XML keeps Command/Arguments/WorkingDirectory separate;
- no shell executable appears;
- current-user least-privilege settings;
- daily trigger and StartWhenAvailable;
- ignore-new instance policy;
- `schtasks` argument arrays are exact;
- XML inspection detects time/action/principal drift;
- paths containing spaces and XML-special characters remain literal.

systemd tests assert:

- exact service/timer rendering;
- no shell wrapper;
- correct escaping of spaces, quotes, backslashes, and `%`;
- `Persistent=true`, local calendar time, `AccuracySec=1min`;
- exact `systemctl --user` argv;
- missing timer is idempotent removal;
- unit-file or enablement drift is detected.

launchd tests assert:

- `plistlib` structure;
- ProgramArguments is an array, never a command string;
- local StartCalendarInterval;
- correct runner WorkingDirectory, distinct from the monitored project root;
- exact GUI-domain `launchctl` argv;
- loaded/plist drift detection;
- idempotent bootout/remove behavior.

### 23.3 Preservation tests

Batch #26 must remain unchanged semantically:

- `execute_monitor()` still performs freshness before discovery/state;
- exact candidate delegation remains `codex@<latest>`;
- remembered BLOCK remains exit `2`;
- INCOMPLETE remains nonterminal/retryable;
- existing `qualock check` easy output remains byte-equivalent;
- `qualock monitor` output/exit mapping remains unchanged.

## 24. Review gates

Task reviewers and final reviewer must explicitly audit:

```text
no daemon / no cron fallback
no admin/root requirement
no shell invocation
no arbitrary command in registration
no credential/environment copying beyond PATH
runner delegates only to qualock monitor
fresh monitor preflight reused by schedule enable
status/disable work on stale/broken projects
registration corruption fails closed
native drift never reports ENABLED
enable rollback cannot silently leave uncertain background work
disable is idempotent
daily local-time/DST semantics
Windows current-user/no-password behavior
systemd user-only files + Persistent timer
launchd user-agent behavior
Batch #26 monitor behavior preserved
protected execution-engine directories unchanged
```

No merge while Critical/Important/P1/P2 findings remain.

## 25. Platform behavior references

The design relies on native documented scheduler features rather than emulating them:

- Microsoft Task Scheduler Exec actions separate Command, Arguments, and WorkingDirectory and support per-task execution settings.
- systemd user timers provide `OnCalendar` and `Persistent` calendar-trigger behavior with a separate oneshot service.
- macOS launchd supports `ProgramArguments`, `WorkingDirectory`, and `StartCalendarInterval`; Apple documents wake-from-sleep behavior for calendar jobs while powered-off missed jobs wait for a later scheduled occurrence.

These references define scheduler behavior only. QuaLock safety behavior continues to come exclusively from the Batch #26 monitor core.

## 26. Definition of done

Batch #27 is complete only when:

- a user can enable a daily local-time release monitor with one command;
- the default time is `09:00`;
- Windows, Linux systemd-user, and macOS launchd adapters satisfy their contracts;
- unsupported native scheduling fails clearly with no fallback daemon/cron;
- no administrator/root privilege or stored Windows password is required;
- the native action is shell-free;
- the runner executes only the fixed `qualock monitor` path;
- scheduled runs use the registered project root and captured PATH;
- the monitor child exit code is preserved;
- registration corruption/deletion fails closed;
- enable reports success only after native MATCHING verification;
- failed enable attempts roll back best-effort and never silently claim success;
- status distinguishes ENABLED, DISABLED, and NEEDS REPAIR;
- disable works even when project trust inputs are stale/missing and is idempotent;
- logs are retained after disable;
- Batch #26 one-shot monitor behavior remains preserved;
- qualification/run/evidence/source/protection/setup engines have zero intended diff;
- full tests/static checks pass;
- independent final review has no unresolved high-priority finding;
- exact-head PR CI and post-merge main CI pass;
- no tag/release/PyPI action occurs.
