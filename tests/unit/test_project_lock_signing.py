import os
import stat
from pathlib import Path

import pytest

from qualock.config.models import ProjectProtectionConfig
from qualock.project_protection.models import ProjectLock, ProtectionRun, ProtectionStatus


def sample_lock() -> ProjectLock:
    definition = ProjectProtectionConfig(
        id="tests",
        name="Tests still pass",
        command=["python", "-m", "pytest", "-q"],
        timeout_seconds=120,
    )
    run = ProtectionRun(
        id=definition.id,
        name=definition.name,
        command=definition.command,
        timeout_seconds=definition.timeout_seconds,
        status=ProtectionStatus.PASS,
        exit_code=0,
        duration_ms=10,
    )
    return ProjectLock(
        created_at="2026-09-02T00:00:00Z",
        git_head="a" * 40,
        git_dirty=False,
        protections=[definition],
        baseline=[run],
    )


def test_ensure_signing_key_creates_32_byte_key_with_private_permissions(tmp_path: Path) -> None:
    from qualock.project_protection.signing import ensure_signing_key

    path = tmp_path / "project-protection.key"
    key = ensure_signing_key(path)

    assert len(key) == 32
    assert path.read_bytes() == key
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_signing_is_deterministic_for_same_lock_and_key() -> None:
    from qualock.project_protection.signing import sign_project_lock

    key = b"k" * 32
    first = sign_project_lock(sample_lock(), key)
    second = sign_project_lock(sample_lock(), key)

    assert first.hmac_sha256 == second.hmac_sha256
    assert len(first.hmac_sha256) == 64


def test_verify_rejects_wrong_key() -> None:
    from qualock.project_protection.signing import (
        ProjectLockIntegrityError,
        sign_project_lock,
        verify_project_lock,
    )

    envelope = sign_project_lock(sample_lock(), b"a" * 32)

    with pytest.raises(ProjectLockIntegrityError, match="signature"):
        verify_project_lock(envelope, b"b" * 32)


def test_verify_rejects_tampered_payload() -> None:
    from qualock.project_protection.signing import (
        ProjectLockIntegrityError,
        sign_project_lock,
        verify_project_lock,
    )

    envelope = sign_project_lock(sample_lock(), b"a" * 32)
    envelope.lock.protections[0].name = "Always passes now"

    with pytest.raises(ProjectLockIntegrityError, match="signature"):
        verify_project_lock(envelope, b"a" * 32)


def test_load_signing_key_rejects_missing_or_malformed_key(tmp_path: Path) -> None:
    from qualock.project_protection.signing import ProjectLockIntegrityError, load_signing_key

    path = tmp_path / "project-protection.key"
    with pytest.raises(ProjectLockIntegrityError, match="missing"):
        load_signing_key(path)

    path.write_bytes(b"too-short")
    with pytest.raises(ProjectLockIntegrityError, match="invalid"):
        load_signing_key(path)
