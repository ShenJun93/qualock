from __future__ import annotations

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
from qualock.scheduler.backends.systemd import (
    SystemdUserBackend,
    active_argv,
    disable_argv,
    enable_argv,
    enabled_argv,
    load_state_argv,
    manager_probe_argv,
    reload_argv,
    render_service,
    render_timer,
    service_name,
    systemd_escape_argument,
)
from qualock.scheduler.models import (
    NativeScheduleState,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    native_id_for,
    schedule_identity,
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
    backend = SchedulerBackendKind.SYSTEMD_USER
    return ScheduleRegistration(
        project_key=key,
        project_root=root,
        backend=backend,
        native_id=native_id_for(backend, key),
        hour=7,
        minute=5,
        python_executable=root / 'Python % 3\\bin' / 'py"thon',
        runner_working_directory=root / 'work % \\ "quoted"',
        path_env="unused",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def identity(registration: ScheduleRegistration) -> ScheduleIdentity:
    return ScheduleIdentity(
        registration.project_key, registration.backend, registration.native_id
    )


def successful_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
    if list(argv)[2:3] == ["is-enabled"]:
        return result(stdout="enabled\n")
    if list(argv)[2:3] == ["is-active"]:
        return result(stdout="active\n")
    return result()


def test_systemd_rendering_is_shell_free_persistent_and_local(
    registration: ScheduleRegistration,
) -> None:
    key = registration.project_key
    service = render_service(registration)
    timer = render_timer(registration)

    assert service == (
        "[Unit]\n"
        f"Description=QuaLock release monitor {key}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'WorkingDirectory="{registration.project_root}/work %% \\\\ \\"quoted\\""\n'
        f'ExecStart="{registration.project_root}/Python %% 3\\\\bin/py\\"thon" '
        f'"-m" "qualock.scheduler.runner" "--project-key" "{key}"\n'
    )
    assert timer == (
        "[Unit]\n"
        f"Description=QuaLock daily release monitor {key}\n\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 07:05:00\n"
        "Persistent=true\n"
        "AccuracySec=1min\n"
        f"Unit=qualock-release-monitor-{key}.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    assert "/bin/sh" not in service
    assert "bash" not in service


def test_argument_escaping_and_exact_manager_argv(
    registration: ScheduleRegistration,
) -> None:
    assert systemd_escape_argument('space % \\ " quote') == '"space %% \\\\ \\" quote"'
    assert service_name(registration.project_key) == registration.native_id.removesuffix(
        ".timer"
    ) + ".service"
    assert manager_probe_argv() == ["systemctl", "--user", "show-environment"]
    assert reload_argv() == ["systemctl", "--user", "daemon-reload"]
    assert enable_argv(registration.native_id) == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        registration.native_id,
    ]
    assert enabled_argv(registration.native_id) == [
        "systemctl",
        "--user",
        "is-enabled",
        registration.native_id,
    ]
    assert active_argv(registration.native_id) == [
        "systemctl",
        "--user",
        "is-active",
        registration.native_id,
    ]
    assert load_state_argv(registration.native_id) == [
        "systemctl",
        "--user",
        "show",
        "--property=LoadState",
        "--value",
        registration.native_id,
    ]
    assert disable_argv(registration.native_id) == [
        "systemctl",
        "--user",
        "disable",
        "--now",
        registration.native_id,
    ]


def test_install_uses_xdg_unit_root_atomic_files_and_exact_calls(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result()

    xdg = tmp_path / "xdg config"
    backend = SystemdUserBackend(
        process_runner=fake_run,
        which=lambda name: name,
        config_home=xdg,
        home=tmp_path / "ignored-home",
    )
    backend.install(registration)

    unit_root = xdg / "systemd" / "user"
    assert (unit_root / service_name(registration.project_key)).read_text() == render_service(
        registration
    )
    assert (unit_root / registration.native_id).read_text() == render_timer(registration)
    assert sorted(path.name for path in unit_root.iterdir()) == sorted(
        [service_name(registration.project_key), registration.native_id]
    )
    assert calls == [reload_argv(), enable_argv(registration.native_id)]
    assert all(
        forbidden not in " ".join(" ".join(call) for call in calls).lower()
        for forbidden in ("linger", "loginctl", "cron", "/bin/sh", "bash")
    )


def test_default_unit_root_is_home_config(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = SystemdUserBackend(
        process_runner=successful_run,
        which=lambda name: name,
        home=tmp_path / "home",
    )
    backend.install(registration)
    assert (
        tmp_path
        / "home"
        / ".config"
        / "systemd"
        / "user"
        / registration.native_id
    ).is_file()


def test_matching_timer_requires_enabled_and_active(
    registration: ScheduleRegistration,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        argv: Sequence[str], *, cwd: Path | None, env: object, timeout_seconds: float
    ) -> ProcessResult:
        calls.append(list(argv))
        key = tuple(argv)
        if key == ("systemctl", "--user", "is-enabled", registration.native_id):
            return result(stdout="enabled\n")
        if key == ("systemctl", "--user", "is-active", registration.native_id):
            return result(stdout="active\n")
        return result()

    backend = SystemdUserBackend(
        process_runner=fake_run,
        which=lambda name: name,
        config_home=tmp_path / "xdg",
        home=tmp_path / "home",
    )
    backend.install(registration)
    inspection = backend.inspect(
        schedule_identity(registration.project_root, registration.backend), registration
    )

    assert inspection.state is NativeScheduleState.MATCHING
    assert enabled_argv(registration.native_id) in calls
    assert active_argv(registration.native_id) in calls


@pytest.mark.parametrize(
    ("enabled", "active"),
    [(result(1, "disabled\n"), result(stdout="active\n")), (result(stdout="enabled\n"), result(3, "inactive\n"))],
)
def test_disabled_or_inactive_timer_is_drifted(
    registration: ScheduleRegistration,
    tmp_path: Path,
    enabled: ProcessResult,
    active: ProcessResult,
) -> None:
    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        if list(argv) == enabled_argv(registration.native_id):
            return enabled
        if list(argv) == active_argv(registration.native_id):
            return active
        return result()

    backend = SystemdUserBackend(process_runner=fake_run, config_home=tmp_path / "xdg")
    backend.install(registration)
    assert backend.inspect(identity(registration), registration).state is NativeScheduleState.DRIFTED


def test_byte_drift_is_drifted(registration: ScheduleRegistration, tmp_path: Path) -> None:
    backend = SystemdUserBackend(
        process_runner=successful_run, config_home=tmp_path / "xdg"
    )
    backend.install(registration)
    (tmp_path / "xdg" / "systemd" / "user" / registration.native_id).write_text(
        render_timer(registration) + "# drift\n"
    )
    assert backend.inspect(identity(registration), registration).state is NativeScheduleState.DRIFTED


def test_expected_none_presence_and_true_missing(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    unit_root = tmp_path / "xdg" / "systemd" / "user"
    timer_path = unit_root / registration.native_id

    def inspect(response: ProcessResult) -> NativeScheduleState:
        backend = SystemdUserBackend(
            process_runner=lambda *args, **kwargs: response,
            config_home=tmp_path / "xdg",
        )
        return backend.inspect(identity(registration), None).state

    assert inspect(result(1, "not-found\n")) is NativeScheduleState.MISSING
    assert inspect(result(stdout="loaded\n")) is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    unit_root.mkdir(parents=True)
    timer_path.write_text("stale")
    assert inspect(result(1, "not-found\n")) is NativeScheduleState.PRESENT_BUT_UNVERIFIED


def test_expected_none_without_owned_files_requires_known_loaded_timer(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        if list(argv) == load_state_argv(registration.native_id):
            return result(1, stderr="Failed to connect to bus: permission denied")
        return result(stdout="loaded\n")

    backend = SystemdUserBackend(
        process_runner=fake_run, config_home=tmp_path / "xdg"
    )

    with pytest.raises(SchedulerOperationalError):
        backend.inspect(identity(registration), None)

    assert calls == [load_state_argv(registration.native_id)]


def test_expected_none_rejects_unrelated_missing_diagnostic_naming_timer(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    backend = SystemdUserBackend(
        process_runner=lambda *args, **kwargs: result(
            1,
            stdout="not-found\n",
            stderr=(
                "Manager endpoint not found while processing "
                f"{registration.native_id}"
            ),
        ),
        config_home=tmp_path / "xdg",
    )

    with pytest.raises(SchedulerOperationalError):
        backend.inspect(identity(registration), None)


@pytest.mark.parametrize("failed_query", ["is-enabled", "is-active"])
def test_expected_inspection_raises_for_unknown_state_query_failure(
    registration: ScheduleRegistration, tmp_path: Path, failed_query: str
) -> None:
    backend = SystemdUserBackend(
        process_runner=successful_run, config_home=tmp_path / "xdg"
    )
    backend.install(registration)

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        if list(argv)[2:3] == [failed_query]:
            return result(1, stderr="Failed to connect to bus: permission denied")
        return successful_run(argv, **kwargs)

    backend = SystemdUserBackend(
        process_runner=fake_run, config_home=tmp_path / "xdg"
    )

    with pytest.raises(SchedulerOperationalError):
        backend.inspect(identity(registration), registration)


def test_remove_missing_is_idempotent_removes_stale_files_then_reloads(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        if list(argv) == disable_argv(registration.native_id):
            return result(
                1,
                stderr=f"Unit {registration.native_id} does not exist.\n",
            )
        return result()

    unit_root = tmp_path / "xdg" / "systemd" / "user"
    unit_root.mkdir(parents=True)
    service_path = unit_root / service_name(registration.project_key)
    timer_path = unit_root / registration.native_id
    service_path.write_text("stale")
    timer_path.write_text("stale")
    backend = SystemdUserBackend(process_runner=fake_run, config_home=tmp_path / "xdg")
    backend.remove(identity(registration))

    assert calls == [disable_argv(registration.native_id), reload_argv()]
    assert not service_path.exists()
    assert not timer_path.exists()


def test_probe_and_command_failures_are_typed(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    with pytest.raises(SchedulerUnsupportedError):
        SystemdUserBackend(
            process_runner=lambda *args, **kwargs: result(),
            which=lambda name: None,
            config_home=tmp_path / "missing-xdg",
            home=tmp_path / "missing-home",
        ).probe()
    calls: list[list[str]] = []

    def unavailable(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result(1, stderr="Failed to connect to bus")

    with pytest.raises(SchedulerUnsupportedError):
        SystemdUserBackend(
            process_runner=unavailable,
            which=lambda name: name,
            config_home=tmp_path / "unavailable-xdg",
            home=tmp_path / "unavailable-home",
        ).probe()
    assert calls == [manager_probe_argv()]

    backend = SystemdUserBackend(
        process_runner=lambda *args, **kwargs: result(1, stderr="permission denied"),
        config_home=tmp_path / "xdg",
    )
    with pytest.raises(SchedulerOperationalError):
        backend.install(registration)


def test_remove_unexpected_failure_keeps_files(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    unit_root = tmp_path / "xdg" / "systemd" / "user"
    unit_root.mkdir(parents=True)
    timer_path = unit_root / registration.native_id
    timer_path.write_text("owned")
    backend = SystemdUserBackend(
        process_runner=lambda *args, **kwargs: result(1, stderr="access denied"),
        config_home=tmp_path / "xdg",
    )
    with pytest.raises(SchedulerOperationalError):
        backend.remove(identity(registration))
    assert timer_path.is_file()


def test_remove_does_not_swallow_unrelated_missing_file_failure(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    unit_root = tmp_path / "xdg" / "systemd" / "user"
    unit_root.mkdir(parents=True)
    timer_path = unit_root / registration.native_id
    timer_path.write_text("owned")
    backend = SystemdUserBackend(
        process_runner=lambda *args, **kwargs: result(
            1, stderr="Failed to connect to bus: No such file or directory"
        ),
        config_home=tmp_path / "xdg",
    )

    with pytest.raises(SchedulerOperationalError):
        backend.remove(identity(registration))

    assert timer_path.is_file()


def test_remove_does_not_swallow_unrelated_missing_diagnostic_naming_timer(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    unit_root = tmp_path / "xdg" / "systemd" / "user"
    unit_root.mkdir(parents=True)
    service_path = unit_root / service_name(registration.project_key)
    timer_path = unit_root / registration.native_id
    service_path.write_text("owned")
    timer_path.write_text("owned")
    backend = SystemdUserBackend(
        process_runner=lambda *args, **kwargs: result(
            1,
            stderr=(
                "Manager endpoint not found while processing "
                f"{registration.native_id}"
            ),
        ),
        config_home=tmp_path / "xdg",
    )

    with pytest.raises(SchedulerOperationalError):
        backend.remove(identity(registration))

    assert service_path.is_file()
    assert timer_path.is_file()
