from __future__ import annotations

import plistlib
import stat
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qualock.release_monitor.state import project_key
from qualock.run.process import ProcessResult
from qualock.scheduler.backends.base import (
    SchedulerOperationalError,
    SchedulerUnsupportedError,
)
from qualock.scheduler.backends.launchd import (
    LAUNCHD_TIMEOUT_SECONDS,
    LaunchdAgentBackend,
    bootout_argv,
    bootstrap_argv,
    print_argv,
    render_plist,
)
from qualock.scheduler.models import (
    NativeScheduleState,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    native_id_for,
)


def result(
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    *,
    timed_out: bool = False,
) -> ProcessResult:
    return ProcessResult(exit_code, stdout, stderr, 0.01, timed_out)


@pytest.fixture
def registration(tmp_path: Path) -> ScheduleRegistration:
    root = tmp_path.resolve()
    key = project_key(root)
    backend = SchedulerBackendKind.LAUNCHD_AGENT
    return ScheduleRegistration(
        project_key=key,
        project_root=root,
        backend=backend,
        native_id=native_id_for(backend, key),
        hour=7,
        minute=5,
        python_executable=root / "Python & Tools" / "py<thon>",
        runner_working_directory=root / "runner & home <safe>",
        path_env="unused",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def identity(registration: ScheduleRegistration) -> ScheduleIdentity:
    return ScheduleIdentity(
        registration.project_key, registration.backend, registration.native_id
    )


def test_launchagent_plist_is_fixed_user_action(
    registration: ScheduleRegistration,
) -> None:
    rendered = render_plist(registration)
    payload = plistlib.loads(rendered)

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
    assert rendered == plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
    assert isinstance(payload["ProgramArguments"], list)
    assert payload["ProgramArguments"][0] == str(registration.python_executable)
    assert payload["WorkingDirectory"] == str(registration.runner_working_directory)


def test_install_uses_only_owned_launchagent_path_and_exact_gui_calls(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        if list(argv) == bootout_argv(501, registration.native_id):
            return result(
                3,
                stderr=(
                    f'Could not find service "{registration.native_id}" '
                    "in domain for user gui: 501\n"
                ),
            )
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )
    backend.install(registration)

    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    assert plist_path.read_bytes() == render_plist(registration)
    assert list(plist_path.parent.iterdir()) == [plist_path]
    assert calls == [
        bootout_argv(501, registration.native_id),
        bootstrap_argv(501, plist_path),
    ]
    assert all("/Library/LaunchDaemons" not in value for call in calls for value in call)
    assert all(value not in {"sh", "bash", "/bin/sh"} for call in calls for value in call)


def test_process_calls_use_exact_cwd_env_and_timeout(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], Path | None, object, float]] = []

    def fake_run(
        argv: Sequence[str],
        *,
        cwd: Path | None,
        env: object,
        timeout_seconds: float,
    ) -> ProcessResult:
        calls.append((list(argv), cwd, env, timeout_seconds))
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )
    backend.install(registration)
    backend.inspect(identity(registration), registration)
    backend.remove(identity(registration))

    assert calls
    assert all(
        cwd is None and env is None and timeout == LAUNCHD_TIMEOUT_SECONDS
        for _, cwd, env, timeout in calls
    )


def test_install_writes_mode_0600_plist(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    backend.install(registration)

    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


def test_install_bootout_failure_stops_before_write_and_bootstrap(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result(1, stderr="Operation not permitted")

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )

    with pytest.raises(SchedulerOperationalError, match="bootout failed"):
        backend.install(registration)

    assert calls == [bootout_argv(501, registration.native_id)]
    assert not (tmp_path / "Library" / "LaunchAgents").exists()


def test_install_bootstrap_failure_occurs_after_atomic_write(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        if list(argv)[1] == "bootstrap":
            return result(1, stderr="Bootstrap failed")
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )

    with pytest.raises(SchedulerOperationalError, match="bootstrap failed"):
        backend.install(registration)

    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    assert plist_path.read_bytes() == render_plist(registration)
    assert calls == [
        bootout_argv(501, registration.native_id),
        bootstrap_argv(501, plist_path),
    ]


def test_atomic_write_failure_preserves_old_plist_cleans_temp_and_skips_bootstrap(
    registration: ScheduleRegistration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"old")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("qualock.scheduler.backends.launchd.os.replace", fail_replace)

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )

    with pytest.raises(SchedulerOperationalError, match="write plist failed"):
        backend.install(registration)

    assert plist_path.read_bytes() == b"old"
    assert list(plist_path.parent.iterdir()) == [plist_path]
    assert calls == [bootout_argv(501, registration.native_id)]


@pytest.mark.parametrize("operation", ["bootout", "bootstrap"])
def test_install_timeout_is_operational(
    registration: ScheduleRegistration, tmp_path: Path, operation: str
) -> None:
    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        if list(argv)[1] == operation:
            return result(None, timed_out=True)
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )

    with pytest.raises(SchedulerOperationalError, match=rf"{operation} timed out"):
        backend.install(registration)


def test_matching_requires_canonical_bytes_and_loaded_gui_label(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result(stdout="service = {...}\n")

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(render_plist(registration))

    inspection = backend.inspect(identity(registration), registration)

    assert inspection.state is NativeScheduleState.MATCHING
    assert calls == [print_argv(501, registration.native_id)]


@pytest.mark.parametrize("contents", [b"malformed", b"<?xml version='1.0'?><plist/>"])
def test_loaded_service_with_bad_plist_is_drifted(
    registration: ScheduleRegistration, tmp_path: Path, contents: bytes
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(stdout="service = {...}\n"),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(contents)

    assert (
        backend.inspect(identity(registration), registration).state
        is NativeScheduleState.DRIFTED
    )


def test_missing_or_unloaded_service_with_expected_is_drifted(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(
            3,
            stderr=(
                f'Could not find service "{registration.native_id}" '
                "in domain for user gui: 501\n"
            ),
        ),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    assert (
        backend.inspect(identity(registration), registration).state
        is NativeScheduleState.DRIFTED
    )


def test_present_launchagent_without_registration_is_unverified(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result(0, "service = {...}\n")

    backend = LaunchdAgentBackend(
        process_runner=fake_run,
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    inspection = backend.inspect(identity(registration), None)

    assert inspection.state is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    assert ["launchctl", "print", f"gui/501/{registration.native_id}"] in calls


def test_owned_plist_without_loaded_service_is_unverified(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"malformed")
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(
            3,
            stderr=(
                f'Could not find service "{registration.native_id}" '
                "in domain for user gui: 501\n"
            ),
        ),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    assert (
        backend.inspect(identity(registration), None).state
        is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    )


def test_no_plist_and_known_absent_service_is_missing(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(
            3,
            stderr=(
                f'Could not find service "{registration.native_id}" '
                "in domain for user gui: 501\n"
            ),
        ),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )
    assert (
        backend.inspect(identity(registration), None).state
        is NativeScheduleState.MISSING
    )


@pytest.mark.parametrize("native_id", ["other.service", "../../../escaped"])
@pytest.mark.parametrize("operation", ["inspect", "remove"])
def test_malformed_identity_fails_closed_before_path_or_service_use(
    registration: ScheduleRegistration,
    tmp_path: Path,
    native_id: str,
    operation: str,
) -> None:
    calls: list[list[str]] = []
    malformed = ScheduleIdentity(
        registration.project_key, SchedulerBackendKind.LAUNCHD_AGENT, native_id
    )
    escaped = tmp_path / "escaped.plist"
    escaped.write_bytes(b"not owned")

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )

    with pytest.raises(SchedulerOperationalError, match="identity"):
        if operation == "inspect":
            backend.inspect(malformed, None)
        else:
            backend.remove(malformed)

    assert calls == []
    assert escaped.read_bytes() == b"not owned"


def test_inspect_timeout_is_operational(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(None, timed_out=True),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    with pytest.raises(SchedulerOperationalError, match="print timed out"):
        backend.inspect(identity(registration), None)


def test_remove_boots_out_before_removing_only_owned_plist(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"owned")

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        assert plist_path.exists()
        return result()

    backend = LaunchdAgentBackend(
        process_runner=fake_run, which=lambda name: name, home=tmp_path, uid=501
    )
    backend.remove(identity(registration))

    assert calls == [bootout_argv(501, registration.native_id)]
    assert not plist_path.exists()


def test_remove_known_absent_is_idempotent(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"owned")
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(
            3,
            stderr=(
                f'Could not find service "{registration.native_id}" '
                "in domain for user gui: 501\n"
            ),
        ),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )
    backend.remove(identity(registration))
    assert not plist_path.exists()


def test_remove_real_failure_preserves_plist(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"owned")
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(1, stderr="Operation not permitted"),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )
    with pytest.raises(SchedulerOperationalError):
        backend.remove(identity(registration))
    assert plist_path.read_bytes() == b"owned"


def test_remove_timeout_preserves_plist(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{registration.native_id}.plist"
    )
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"owned")
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(None, timed_out=True),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )

    with pytest.raises(SchedulerOperationalError, match="bootout timed out"):
        backend.remove(identity(registration))

    assert plist_path.read_bytes() == b"owned"


def test_unknown_print_failure_is_operational(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = LaunchdAgentBackend(
        process_runner=lambda *args, **kwargs: result(1, stderr="Operation not permitted"),
        which=lambda name: name,
        home=tmp_path,
        uid=501,
    )
    with pytest.raises(SchedulerOperationalError):
        backend.inspect(identity(registration), None)


def test_probe_requires_launchctl_and_nonnegative_uid(tmp_path: Path) -> None:
    with pytest.raises(SchedulerUnsupportedError):
        LaunchdAgentBackend(
            process_runner=lambda *args, **kwargs: result(),
            which=lambda name: None,
            home=tmp_path,
            uid=501,
        ).probe()
    with pytest.raises(SchedulerUnsupportedError):
        LaunchdAgentBackend(
            process_runner=lambda *args, **kwargs: result(),
            which=lambda name: name,
            home=tmp_path,
            uid=-1,
        ).probe()
