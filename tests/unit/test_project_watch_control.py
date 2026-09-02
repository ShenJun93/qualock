from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qualock.project_protection.io import write_project_lock
from qualock.project_protection.models import ProjectLock, ProtectionRun, ProtectionStatus
from qualock.project_protection.signing import ProjectLockIntegrityError
from qualock.project_watch.control import (
    WatchControlChangedError,
    assert_watch_control,
    freeze_watch_control,
)

KEY = b"k" * 32


def _lock(*, created_at: str = "2026-09-02T00:00:00+00:00") -> ProjectLock:
    run = ProtectionRun(
        id="tests",
        name="Tests still pass",
        command=["python", "-m", "pytest", "-q"],
        timeout_seconds=30,
        status=ProtectionStatus.PASS,
        exit_code=0,
        duration_ms=10,
    )
    from qualock.config.models import ProjectProtectionConfig

    protection = ProjectProtectionConfig(
        id="tests",
        name="Tests still pass",
        command=["python", "-m", "pytest", "-q"],
        timeout_seconds=30,
    )
    return ProjectLock(
        created_at=created_at,
        git_head="a" * 40,
        git_dirty=False,
        protections=[protection],
        baseline=[run],
    )


def _write(root: Path, lock: ProjectLock, *, key: bytes = KEY) -> Path:
    qdir = root / ".qualock"
    qdir.mkdir()
    path = qdir / "project.lock"
    write_project_lock(path, lock, key)
    return path


def _key_path(root: Path, key: bytes = KEY) -> Path:
    path = root / "watch.key"
    path.write_bytes(key)
    return path


def test_freeze_authenticates_lock_and_hashes_raw_bytes(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)

    identity = freeze_watch_control(tmp_path, key_path=key_path)

    assert identity.lock_sha256 == hashlib.sha256(lock_path.read_bytes()).hexdigest()
    assert_watch_control(tmp_path, identity, key_path=key_path)


def test_freeze_authenticates_the_same_raw_bytes_it_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    valid_raw = lock_path.read_bytes()
    lock_path.write_bytes(b"{not-json")
    original_read_bytes = Path.read_bytes
    swapped = False

    def racing_read_bytes(path: Path) -> bytes:
        nonlocal swapped
        raw = original_read_bytes(path)
        if path == lock_path and not swapped:
            swapped = True
            lock_path.write_bytes(valid_raw)
        return raw

    monkeypatch.setattr(Path, "read_bytes", racing_read_bytes)

    with pytest.raises(ProjectLockIntegrityError, match="malformed"):
        freeze_watch_control(tmp_path, key_path=key_path)


def test_freeze_rejects_well_formed_tampered_lock(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    raw["lock"]["git_head"] = "b" * 40
    lock_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProjectLockIntegrityError, match="signature does not match"):
        freeze_watch_control(tmp_path, key_path=key_path)


def test_freeze_rejects_missing_key(tmp_path: Path) -> None:
    _write(tmp_path, _lock())

    with pytest.raises(ProjectLockIntegrityError, match="signing key is missing"):
        freeze_watch_control(tmp_path, key_path=tmp_path / "missing.key")


def test_freeze_preserves_missing_lock_as_file_not_found(tmp_path: Path) -> None:
    key_path = _key_path(tmp_path)

    with pytest.raises(FileNotFoundError):
        freeze_watch_control(tmp_path, key_path=key_path)


def test_assert_treats_lock_removed_during_session_as_changed_control(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    lock_path.unlink()

    with pytest.raises(WatchControlChangedError, match="restart qualock watch"):
        assert_watch_control(tmp_path, identity, key_path=key_path)



def test_assert_treats_lock_parent_replaced_by_file_as_changed_control(tmp_path: Path) -> None:
    _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    qdir = tmp_path / ".qualock"
    for child in qdir.iterdir():
        child.unlink()
    qdir.rmdir()
    qdir.write_text("not a directory", encoding="utf-8")

    with pytest.raises(WatchControlChangedError, match="restart qualock watch"):
        assert_watch_control(tmp_path, identity, key_path=key_path)

def test_assert_rejects_tampered_lock_via_existing_integrity_error(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    raw["lock"]["git_head"] = "b" * 40
    lock_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProjectLockIntegrityError, match="signature does not match"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_rejects_new_valid_signed_lock_as_changed_control(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    write_project_lock(lock_path, _lock(created_at="2026-09-02T00:01:00+00:00"), KEY)

    with pytest.raises(WatchControlChangedError, match="restart qualock watch"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_rejects_logically_equivalent_reserialization(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_path.write_text(json.dumps(raw, sort_keys=False, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(WatchControlChangedError, match="restart qualock watch"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_treats_lock_replaced_by_directory_as_changed_control(tmp_path: Path) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    lock_path.unlink()
    lock_path.mkdir()

    with pytest.raises(WatchControlChangedError, match="restart qualock watch"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_fails_closed_if_signing_key_disappears_during_session(tmp_path: Path) -> None:
    _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    key_path.unlink()

    with pytest.raises(ProjectLockIntegrityError, match="signing key is missing"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_fails_closed_if_signing_key_rotates_during_session(tmp_path: Path) -> None:
    _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    key_path.write_bytes(b"r" * 32)

    with pytest.raises(ProjectLockIntegrityError, match="signature does not match"):
        assert_watch_control(tmp_path, identity, key_path=key_path)


def test_assert_authenticates_and_hashes_one_raw_lock_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = _write(tmp_path, _lock())
    key_path = _key_path(tmp_path)
    identity = freeze_watch_control(tmp_path, key_path=key_path)
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    raw["lock"]["git_head"] = "b" * 40
    tampered = json.dumps(raw).encode("utf-8")
    original_read_bytes = Path.read_bytes
    lock_reads = 0

    def racing_read_bytes(path: Path) -> bytes:
        nonlocal lock_reads
        result = original_read_bytes(path)
        if path == lock_path:
            lock_reads += 1
            if lock_reads == 1:
                lock_path.write_bytes(tampered)
        return result

    monkeypatch.setattr(Path, "read_bytes", racing_read_bytes)

    assert_watch_control(tmp_path, identity, key_path=key_path)
    assert lock_reads == 1

    with pytest.raises(ProjectLockIntegrityError, match="signature does not match"):
        assert_watch_control(tmp_path, identity, key_path=key_path)
