from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
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
from qualock.scheduler.backends.windows import (
    WindowsTaskSchedulerBackend,
    create_argv,
    delete_argv,
    query_argv,
    task_xml,
)
from qualock.scheduler.models import (
    NativeScheduleState,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    native_id_for,
)


@pytest.fixture
def registration(tmp_path: Path) -> ScheduleRegistration:
    root = tmp_path.resolve()
    key = project_key(root)
    backend = SchedulerBackendKind.WINDOWS_TASK_SCHEDULER
    return ScheduleRegistration(
        project_key=key,
        project_root=root,
        backend=backend,
        native_id=native_id_for(backend, key),
        hour=7,
        minute=5,
        python_executable=root / "Program Files" / "Py & <tools>" / "python.exe",
        runner_working_directory=root / "Alice & Bob" / "QuaLock <work>",
        path_env="unused",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


@pytest.fixture
def identity(registration: ScheduleRegistration) -> ScheduleIdentity:
    return ScheduleIdentity(
        registration.project_key, registration.backend, registration.native_id
    )


def result(
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    *,
    timed_out: bool = False,
) -> ProcessResult:
    return ProcessResult(exit_code, stdout, stderr, 0.01, timed_out)


def test_task_xml_is_current_user_least_privilege_and_shell_free(
    registration: ScheduleRegistration,
) -> None:
    payload = task_xml(registration, user_id=r"DOMAIN\alice")
    root = ET.fromstring(payload)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert root.findtext(".//t:UserId", namespaces=ns) == r"DOMAIN\alice"
    assert root.findtext(".//t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext(".//t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext(".//t:StartWhenAvailable", namespaces=ns) == "true"
    assert root.findtext(".//t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert root.findtext(".//t:DaysInterval", namespaces=ns) == "1"
    assert root.findtext(".//t:StartBoundary", namespaces=ns) == "2000-01-01T07:05:00"
    assert root.findtext(".//t:Command", namespaces=ns) == str(
        registration.python_executable
    )
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


def test_argv_is_structured(registration: ScheduleRegistration, tmp_path: Path) -> None:
    xml_path = tmp_path / "task.xml"
    assert create_argv(registration.native_id, xml_path) == [
        "schtasks.exe",
        "/Create",
        "/TN",
        registration.native_id,
        "/XML",
        str(xml_path),
        "/F",
    ]
    assert query_argv(registration.native_id) == [
        "schtasks.exe",
        "/Query",
        "/TN",
        registration.native_id,
        "/XML",
    ]
    assert delete_argv(registration.native_id) == [
        "schtasks.exe",
        "/Delete",
        "/TN",
        registration.native_id,
        "/F",
    ]


def test_install_uses_xml_file_and_always_removes_it(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        argv: Sequence[str], **kwargs: object
    ) -> ProcessResult:
        calls.append(list(argv))
        xml_path = Path(argv[5])
        assert xml_path.parent == tmp_path
        assert ET.fromstring(xml_path.read_bytes()).tag.endswith("Task")
        return result()

    backend = WindowsTaskSchedulerBackend(
        process_runner=fake_run, which=lambda name: name, temp_dir=tmp_path, user_id="alice"
    )
    backend.install(registration)
    assert calls[0][:5] == [
        "schtasks.exe",
        "/Create",
        "/TN",
        registration.native_id,
        "/XML",
    ]
    assert calls[0][-1] == "/F"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("response", [result(5, stderr="access denied"), result(None, timed_out=True)])
def test_install_failure_is_operational_and_cleans_temp(
    registration: ScheduleRegistration, tmp_path: Path, response: ProcessResult
) -> None:
    backend = WindowsTaskSchedulerBackend(
        process_runner=lambda *args, **kwargs: response,
        which=lambda name: name,
        temp_dir=tmp_path,
        user_id="alice",
    )
    with pytest.raises(SchedulerOperationalError):
        backend.install(registration)
    assert list(tmp_path.iterdir()) == []


def test_present_task_without_registration_is_unverified(
    identity: ScheduleIdentity,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        argv: Sequence[str], *, cwd: Path | None, env: object, timeout_seconds: float
    ) -> ProcessResult:
        calls.append(list(argv))
        return result(
            stdout='<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"></Task>'
        )

    backend = WindowsTaskSchedulerBackend(
        process_runner=fake_run, which=lambda name: name, user_id="alice"
    )
    inspection = backend.inspect(identity, None)
    assert inspection.state is NativeScheduleState.PRESENT_BUT_UNVERIFIED
    assert calls[-1] == ["schtasks.exe", "/Query", "/TN", identity.native_id, "/XML"]


def test_inspect_matching_and_every_required_drift(
    registration: ScheduleRegistration, identity: ScheduleIdentity
) -> None:
    good = task_xml(registration, user_id="alice")

    def inspect(payload: bytes) -> NativeScheduleState:
        backend = WindowsTaskSchedulerBackend(
            process_runner=lambda *args, **kwargs: result(stdout=payload.decode()),
            which=lambda name: name,
            user_id="alice",
        )
        return backend.inspect(identity, registration).state

    assert inspect(good) is NativeScheduleState.MATCHING
    drifts = [
        ("StartBoundary", "2000-01-01T08:05:00"),
        ("DaysInterval", "2"),
        ("UserId", "bob"),
        ("LogonType", "Password"),
        ("RunLevel", "HighestAvailable"),
        ("StartWhenAvailable", "false"),
        ("MultipleInstancesPolicy", "Parallel"),
        ("Command", r"C:\other.exe"),
        ("Arguments", "wrong args"),
        ("WorkingDirectory", r"C:\other"),
    ]
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    for tag, changed_value in drifts:
        changed = ET.fromstring(good)
        node = changed.find(f".//{{{namespace}}}{tag}")
        assert node is not None
        node.text = changed_value
        assert inspect(ET.tostring(changed)) is NativeScheduleState.DRIFTED


@pytest.mark.parametrize("payload", ["not xml", '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"/>'])
def test_malformed_or_unreadable_xml_is_unverified(
    registration: ScheduleRegistration, identity: ScheduleIdentity, payload: str
) -> None:
    backend = WindowsTaskSchedulerBackend(
        process_runner=lambda *args, **kwargs: result(stdout=payload),
        which=lambda name: name,
        user_id="alice",
    )
    assert backend.inspect(identity, registration).state is NativeScheduleState.PRESENT_BUT_UNVERIFIED


MISSING = "ERROR: The system cannot find the file specified."


def test_known_missing_is_missing_and_remove_is_idempotent(
    identity: ScheduleIdentity,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(list(argv))
        return result(1, stderr=MISSING)

    backend = WindowsTaskSchedulerBackend(process_runner=fake_run, which=lambda name: name)
    assert backend.inspect(identity, None).state is NativeScheduleState.MISSING
    backend.remove(identity)
    assert calls[-1] == ["schtasks.exe", "/Delete", "/TN", identity.native_id, "/F"]


def test_probe_missing_executable_is_unsupported() -> None:
    backend = WindowsTaskSchedulerBackend(which=lambda name: None)
    with pytest.raises(SchedulerUnsupportedError):
        backend.probe()


@pytest.mark.parametrize(
    "response",
    [result(None, timed_out=True), result(5, stderr="Access is denied."), result(1, stderr="other")],
)
def test_query_failures_are_operational(
    identity: ScheduleIdentity, response: ProcessResult
) -> None:
    backend = WindowsTaskSchedulerBackend(
        process_runner=lambda *args, **kwargs: response, which=lambda name: name
    )
    with pytest.raises(SchedulerOperationalError):
        backend.inspect(identity, None)


def test_remove_other_failure_is_operational(identity: ScheduleIdentity) -> None:
    backend = WindowsTaskSchedulerBackend(
        process_runner=lambda *args, **kwargs: result(1, stderr="other"),
        which=lambda name: name,
    )
    with pytest.raises(SchedulerOperationalError):
        backend.remove(identity)
