from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.release_monitor.state import project_key
from qualock.scheduler.models import (
    NativeScheduleInspection,
    NativeScheduleState,
    SchedulerBackendKind,
    ScheduleRegistration,
    backend_label,
    native_id_for,
    operationally_equal,
    parse_daily_time,
    schedule_identity,
)


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


@pytest.mark.parametrize("value, expected", [("00:00", (0, 0)), ("23:59", (23, 59))])
def test_parse_daily_time_accepts_clock_boundaries(
    value: str, expected: tuple[int, int]
) -> None:
    assert parse_daily_time(value) == expected


@pytest.mark.parametrize("value", ["0:00", "09:0", "09:00 ", "24:00", "12:60", "-1:00"])
def test_parse_daily_time_rejects_non_strict_values(value: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        parse_daily_time(value)


def test_backend_labels_are_human_readable() -> None:
    assert backend_label(SchedulerBackendKind.WINDOWS_TASK_SCHEDULER) == "Windows Task Scheduler"
    assert backend_label(SchedulerBackendKind.SYSTEMD_USER) == "systemd user timer"
    assert backend_label(SchedulerBackendKind.LAUNCHD_AGENT) == "macOS LaunchAgent"


def test_native_ids_are_exact() -> None:
    key = "a" * 64
    assert native_id_for(SchedulerBackendKind.WINDOWS_TASK_SCHEDULER, key) == (
        f"QuaLock-ReleaseMonitor-{key}"
    )
    assert native_id_for(SchedulerBackendKind.SYSTEMD_USER, key) == (
        f"qualock-release-monitor-{key}.timer"
    )
    assert native_id_for(SchedulerBackendKind.LAUNCHD_AGENT, key) == (
        f"io.qualock.release-monitor.{key}"
    )


def test_schedule_identity_uses_canonical_release_monitor_key(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    identity = schedule_identity(root, SchedulerBackendKind.SYSTEMD_USER)
    assert identity.project_key == project_key(root)
    assert identity.backend is SchedulerBackendKind.SYSTEMD_USER
    assert identity.native_id == native_id_for(identity.backend, identity.project_key)


def test_schedule_identity_requires_existing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        schedule_identity(tmp_path / "missing", SchedulerBackendKind.SYSTEMD_USER)


def test_registration_accepts_absolute_paths_that_disappear_after_enable(
    registration: ScheduleRegistration,
) -> None:
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


@pytest.mark.parametrize(
    "enabled_at",
    ["2026-09-02T09:00:00", datetime.fromisoformat("2026-09-02T09:00:00")],
)
def test_enabled_at_must_be_timezone_aware(
    registration_payload: dict[str, object], enabled_at: object
) -> None:
    registration_payload["enabled_at"] = enabled_at
    with pytest.raises(ValidationError, match="UTC timestamp"):
        ScheduleRegistration.model_validate(registration_payload)


def test_operational_equality_ignores_only_enabled_at(
    registration: ScheduleRegistration,
) -> None:
    later = registration.model_copy(update={"enabled_at": datetime(2030, 1, 1, tzinfo=UTC)})
    assert operationally_equal(registration, later)
    assert not operationally_equal(registration, later.model_copy(update={"path_env": "/new/bin"}))


@pytest.mark.parametrize("field", ["python_executable", "runner_working_directory"])
def test_registration_rejects_relative_runtime_paths(
    registration_payload: dict[str, object], field: str
) -> None:
    registration_payload[field] = Path("relative/path")
    with pytest.raises(ValidationError, match=f"{field} must be absolute"):
        ScheduleRegistration.model_validate(registration_payload)


@pytest.mark.parametrize(
    "field,value", [("hour", -1), ("hour", 24), ("minute", -1), ("minute", 60)]
)
def test_registration_rejects_clock_values_outside_bounds(
    registration_payload: dict[str, object], field: str, value: int
) -> None:
    registration_payload[field] = value
    with pytest.raises(ValidationError, match="daily clock range"):
        ScheduleRegistration.model_validate(registration_payload)


def test_registration_rejects_noncanonical_project_root(
    registration_payload: dict[str, object], tmp_path: Path
) -> None:
    registration_payload["project_root"] = tmp_path / "child" / ".."
    with pytest.raises(ValidationError, match="absolute canonical path"):
        ScheduleRegistration.model_validate(registration_payload)


@pytest.mark.parametrize("key", ["A" * 64, "a" * 63, "g" * 64])
def test_registration_rejects_non_lowercase_64_hex_project_key(
    registration_payload: dict[str, object], key: str
) -> None:
    registration_payload["project_key"] = key
    with pytest.raises(ValidationError, match="64 lowercase hexadecimal"):
        ScheduleRegistration.model_validate(registration_payload)


def test_registration_rejects_project_key_root_mismatch(
    registration_payload: dict[str, object]
) -> None:
    registration_payload["project_key"] = "a" * 64
    with pytest.raises(ValidationError, match="does not match project_root"):
        ScheduleRegistration.model_validate(registration_payload)


@pytest.mark.parametrize(
    "update",
    [
        {"native_id": "wrong"},
        {"backend": SchedulerBackendKind.LAUNCHD_AGENT},
    ],
)
def test_registration_rejects_native_identity_mismatch(
    registration_payload: dict[str, object], update: dict[str, object]
) -> None:
    registration_payload.update(update)
    with pytest.raises(ValidationError, match="native_id does not match"):
        ScheduleRegistration.model_validate(registration_payload)


@pytest.mark.parametrize("update", [{"schema_version": 2}, {"surprise": True}])
def test_registration_rejects_schema_or_extra_fields(
    registration_payload: dict[str, object], update: dict[str, object]
) -> None:
    registration_payload.update(update)
    with pytest.raises(ValidationError):
        ScheduleRegistration.model_validate(registration_payload)


def test_registration_is_frozen(registration: ScheduleRegistration) -> None:
    with pytest.raises(ValidationError):
        registration.path_env = "/changed"  # type: ignore[misc]


def test_native_inspection_defaults_to_no_detail() -> None:
    inspection = NativeScheduleInspection(NativeScheduleState.MISSING)
    assert inspection.detail is None
