from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qualock.release_monitor.state import project_key
from qualock.scheduler.models import SchedulerBackendKind, ScheduleRegistration, native_id_for
from qualock.scheduler.state import FileRegistrationStore, RegistrationLoadKind


@pytest.fixture
def registration(tmp_path: Path) -> ScheduleRegistration:
    root = tmp_path.resolve()
    key = project_key(root)
    return ScheduleRegistration(
        project_key=key,
        project_root=root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=9,
        minute=0,
        python_executable=root / "missing" / "qualock-python",
        runner_working_directory=root / "missing" / "runner-home",
        path_env="/usr/bin",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def test_store_paths_are_per_project(tmp_path: Path) -> None:
    store = FileRegistrationStore(tmp_path)
    key = "a" * 64
    assert store.project_dir(key) == tmp_path / key
    assert store.registration_path(key) == tmp_path / key / "registration.json"
    assert store.log_path(key) == tmp_path / key / "runs.log"


def test_default_store_uses_platform_state_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("qualock.scheduler.state.user_state_dir", lambda app: str(tmp_path))
    assert FileRegistrationStore().base_dir == tmp_path / "release-scheduler" / "projects"


def test_missing_registration_is_distinct(tmp_path: Path) -> None:
    load = FileRegistrationStore(tmp_path).load("a" * 64)
    assert load.kind is RegistrationLoadKind.MISSING
    assert load.registration is None
    assert load.detail is None


def test_corrupt_registration_fails_closed(tmp_path: Path) -> None:
    store = FileRegistrationStore(tmp_path)
    path = store.registration_path("a" * 64)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    assert store.load("a" * 64).kind is RegistrationLoadKind.CORRUPT


def test_invalid_key_fails_closed_without_reading_state(tmp_path: Path) -> None:
    load = FileRegistrationStore(tmp_path).load("A" * 64)
    assert load.kind is RegistrationLoadKind.CORRUPT
    assert load.detail == "invalid project key"


def test_unreadable_registration_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FileRegistrationStore(tmp_path)

    def deny_read(*args: object, **kwargs: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", deny_read)
    load = store.load("a" * 64)
    assert load.kind is RegistrationLoadKind.CORRUPT
    assert "denied" in (load.detail or "")


def test_save_and_load_round_trip(registration: ScheduleRegistration, tmp_path: Path) -> None:
    store = FileRegistrationStore(tmp_path / "state")
    store.save(registration)
    load = store.load(registration.project_key)
    assert load.kind is RegistrationLoadKind.VALID
    assert load.registration == registration
    assert store.registration_path(registration.project_key).read_bytes().endswith(b"\n")


def test_state_path_key_mismatch_fails_closed(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    store = FileRegistrationStore(tmp_path / "state")
    other_key = "a" * 64
    path = store.registration_path(other_key)
    path.parent.mkdir(parents=True)
    path.write_text(registration.model_dump_json(), encoding="utf-8")
    load = store.load(other_key)
    assert load.kind is RegistrationLoadKind.CORRUPT
    assert load.detail == "registration project key does not match state path"


def test_save_atomically_replaces_and_cleans_same_directory_temp_files(
    registration: ScheduleRegistration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileRegistrationStore(tmp_path / "state")
    destination = store.registration_path(registration.project_key)
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observe_replace(source: Path, target: Path) -> None:
        replaced.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr("qualock.scheduler.state.os.replace", observe_replace)
    store.save(registration)
    source, target = replaced[0]
    assert source.parent == destination.parent
    assert source.name.startswith(".registration.json.")
    assert source.name.endswith(".tmp")
    assert target == destination
    assert list(destination.parent.glob(".registration.json.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_save_applies_user_only_permissions(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    store = FileRegistrationStore(tmp_path / "state")
    store.save(registration)
    assert stat.S_IMODE(store.project_dir(registration.project_key).stat().st_mode) == 0o700
    assert stat.S_IMODE(store.registration_path(registration.project_key).stat().st_mode) == 0o600


def test_delete_removes_only_registration_and_preserves_log(
    registration: ScheduleRegistration, tmp_path: Path
) -> None:
    store = FileRegistrationStore(tmp_path / "state")
    store.save(registration)
    log = store.log_path(registration.project_key)
    log.write_text("run\n", encoding="utf-8")
    store.delete(registration.project_key)
    assert not store.registration_path(registration.project_key).exists()
    assert log.read_text(encoding="utf-8") == "run\n"


def test_delete_missing_registration_is_idempotent(tmp_path: Path) -> None:
    FileRegistrationStore(tmp_path).delete("a" * 64)
