from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qualock.release_monitor.commands import MonitorPreflight
from qualock.release_monitor.state import project_key
from qualock.scheduler.backends import (
    SchedulerOperationalError,
    SchedulerUnsupportedError,
)
from qualock.scheduler.commands import (
    ScheduleStatus,
    disable_schedule,
    enable_schedule,
    schedule_status,
    select_backend,
)
from qualock.scheduler.models import (
    NativeScheduleInspection,
    NativeScheduleState,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    native_id_for,
)
from qualock.scheduler.state import RegistrationLoad, RegistrationLoadKind


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
        inspection_states: list[NativeScheduleState] | None = None,
        install_error: SchedulerOperationalError | None = None,
    ) -> None:
        self.events = events
        self.final_state = final_state
        self.inspection_states = inspection_states or []
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
        state = self.inspection_states.pop(0) if self.inspection_states else self.final_state
        return NativeScheduleInspection(state)

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


@pytest.fixture(autouse=True)
def successful_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: MonitorPreflight("0.151.0", "f" * 64),
    )


def test_select_backend_uses_only_platform_backend() -> None:
    made: list[SchedulerBackendKind] = []
    factories = {
        kind: lambda kind=kind: made.append(kind) or FakeBackend([])
        for kind in SchedulerBackendKind
    }
    select_backend(platform="win32", factories=factories)
    assert made == [SchedulerBackendKind.WINDOWS_TASK_SCHEDULER]
    made.clear()
    select_backend(platform="linux2", factories=factories)
    assert made == [SchedulerBackendKind.SYSTEMD_USER]
    made.clear()
    select_backend(platform="darwin", factories=factories)
    assert made == [SchedulerBackendKind.LAUNCHD_AGENT]
    with pytest.raises(SchedulerUnsupportedError, match="unsupported scheduler platform: plan9"):
        select_backend(platform="plan9", factories=factories)


def test_enable_orders_preflight_before_native_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_enable_captures_empty_path_and_no_other_environment(tmp_path: Path) -> None:
    backend = FakeBackend([])
    store = MemoryRegistrationStore([])
    outcome = enable_schedule(
        tmp_path,
        backend=backend,
        store=store,
        executable=existing_python(tmp_path),
        home=tmp_path,
        environ={"PATH": "", "SECRET": "nope"},
    )
    assert outcome.registration is not None
    assert outcome.registration.path_env == ""
    assert "SECRET" not in outcome.registration.model_dump()


@pytest.mark.parametrize("at", ["9:00", "09:0", "24:00", "09:60"])
def test_enable_rejects_bad_time_before_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: str
) -> None:
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: pytest.fail("preflight called"),
    )
    with pytest.raises(ValueError, match="daily time must use HH:MM"):
        enable_schedule(tmp_path, at=at, backend=FakeBackend([]), store=MemoryRegistrationStore([]))


def test_enable_requires_python_and_home(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Python executable is unavailable"):
        enable_schedule(
            tmp_path,
            backend=FakeBackend([]),
            store=MemoryRegistrationStore([]),
            executable=tmp_path / "missing",
            home=tmp_path,
        )
    python = existing_python(tmp_path)
    unavailable_home = tmp_path / "not-a-directory"
    unavailable_home.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="working directory is unavailable"):
        enable_schedule(
            tmp_path,
            backend=FakeBackend([]),
            store=MemoryRegistrationStore([]),
            executable=python,
            home=unavailable_home,
        )


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


def test_drifted_equal_registration_repairs_and_preserves_enabled_at(
    healthy_registration: ScheduleRegistration,
) -> None:
    backend = FakeBackend(
        [],
        inspection_states=[NativeScheduleState.DRIFTED, NativeScheduleState.MATCHING],
    )
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(RegistrationLoadKind.VALID, healthy_registration)
    outcome = enable_schedule(
        healthy_registration.project_root,
        backend=backend,
        store=store,
        executable=healthy_registration.python_executable,
        home=healthy_registration.runner_working_directory,
        environ={"PATH": healthy_registration.path_env},
        now=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert outcome.registration is not None
    assert outcome.registration.enabled_at == healthy_registration.enabled_at
    assert backend.installed == [outcome.registration]


@pytest.mark.parametrize("field", ["time", "python", "path", "home", "backend"])
def test_changed_operational_field_refreshes_registration(
    tmp_path: Path, healthy_registration: ScheduleRegistration, field: str
) -> None:
    backend = FakeBackend([])
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(RegistrationLoadKind.VALID, healthy_registration)
    new_python = healthy_registration.python_executable
    new_home = healthy_registration.runner_working_directory
    at = "09:00"
    path = healthy_registration.path_env
    if field == "time":
        at = "10:00"
    elif field == "python":
        other = tmp_path / "other-python"
        other.write_text("", encoding="utf-8")
        new_python = other.resolve()
    elif field == "path":
        path = "/different"
    elif field == "home":
        other_home = tmp_path / "other-home"
        other_home.mkdir()
        new_home = other_home.resolve()
    else:
        backend.kind = SchedulerBackendKind.LAUNCHD_AGENT
    refreshed = datetime(2030, 1, 1, tzinfo=UTC)
    outcome = enable_schedule(
        healthy_registration.project_root,
        at=at,
        backend=backend,
        store=store,
        executable=new_python,
        home=new_home,
        environ={"PATH": path},
        now=lambda: refreshed,
    )
    assert outcome.registration is not None
    assert outcome.registration.enabled_at == refreshed
    assert backend.installed == [outcome.registration]


def test_enable_requires_final_matching_and_rolls_back(tmp_path: Path) -> None:
    events: list[str] = []
    backend = FakeBackend(events, final_state=NativeScheduleState.DRIFTED)
    store = MemoryRegistrationStore(events)
    with pytest.raises(SchedulerOperationalError, match="verification was drifted"):
        enable_schedule(
            tmp_path,
            backend=backend,
            store=store,
            executable=existing_python(tmp_path),
            home=tmp_path,
        )
    assert events[-2:] == ["remove", "delete"]


def test_enable_rollback_warns_when_native_remove_fails(tmp_path: Path) -> None:
    backend = FakeBackend([], install_error=SchedulerOperationalError("install failed"))
    backend.remove_error = SchedulerOperationalError("remove failed")
    store = MemoryRegistrationStore([])
    store.delete_error = OSError("state delete failed")
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
    assert "install failed" in str(caught.value)
    assert "native schedule may still be enabled" in str(caught.value)
    assert "qualock schedule status" in str(caught.value)
    assert "qualock schedule disable" in str(caught.value)
    assert "state delete failed" in str(caught.value)
    assert backend.remove_calls == 1
    assert store.delete_calls == 1


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
def test_status_table(
    load_kind: RegistrationLoadKind,
    native_state: NativeScheduleState,
    expected: ScheduleStatus,
    healthy_registration: ScheduleRegistration,
) -> None:
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(
        load_kind,
        healthy_registration if load_kind is RegistrationLoadKind.VALID else None,
        "corrupt" if load_kind is RegistrationLoadKind.CORRUPT else None,
    )
    backend = FakeBackend([], final_state=native_state)
    assert schedule_status(healthy_registration.project_root, backend=backend, store=store).status is expected


@pytest.mark.parametrize("missing", ["root", "python", "home"])
def test_status_matching_requires_healthy_paths(
    healthy_registration: ScheduleRegistration,
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = healthy_registration
    if missing == "root":
        original_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path,
            "is_dir",
            lambda path: False if path == registration.project_root else original_is_dir(path),
        )
    elif missing == "python":
        registration.python_executable.unlink()
    else:
        registration.runner_working_directory.rmdir()
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(RegistrationLoadKind.VALID, registration)
    assert schedule_status(registration.project_root, backend=FakeBackend([]), store=store).status is ScheduleStatus.NEEDS_REPAIR


def test_status_backend_mismatch_needs_repair(healthy_registration: ScheduleRegistration) -> None:
    backend = FakeBackend([])
    backend.kind = SchedulerBackendKind.LAUNCHD_AGENT
    store = MemoryRegistrationStore([])
    store.loaded = RegistrationLoad(RegistrationLoadKind.VALID, healthy_registration)
    assert schedule_status(healthy_registration.project_root, backend=backend, store=store).status is ScheduleStatus.NEEDS_REPAIR


def test_status_and_disable_never_preflight(
    healthy_registration: ScheduleRegistration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qualock.scheduler.commands.monitor_preflight",
        lambda root: pytest.fail("preflight called"),
    )
    schedule_status(healthy_registration.project_root, backend=FakeBackend([]), store=MemoryRegistrationStore([]))
    disable_schedule(healthy_registration.project_root, backend=FakeBackend([]), store=MemoryRegistrationStore([]))


def test_disable_order_is_probe_remove_delete(tmp_path: Path) -> None:
    events: list[str] = []
    outcome = disable_schedule(tmp_path, backend=FakeBackend(events), store=MemoryRegistrationStore(events))
    assert events == ["probe", "remove", "delete"]
    assert outcome.status is ScheduleStatus.DISABLED


def test_disable_remove_failure_preserves_registration(tmp_path: Path) -> None:
    backend = FakeBackend([])
    backend.remove_error = SchedulerOperationalError("remove failed")
    store = MemoryRegistrationStore([])
    with pytest.raises(SchedulerOperationalError, match="remove failed"):
        disable_schedule(tmp_path, backend=backend, store=store)
    assert store.delete_calls == 0


def test_disable_delete_failure_is_operational(tmp_path: Path) -> None:
    store = MemoryRegistrationStore([])
    store.delete_error = OSError("state failed")
    with pytest.raises(
        SchedulerOperationalError,
        match="native schedule was removed but registration cleanup failed: state failed",
    ):
        disable_schedule(tmp_path, backend=FakeBackend([]), store=store)
