from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from platformdirs import user_config_dir

from .models import ProjectLock, SignedProjectLock

KEY_BYTES = 32


class ProjectLockIntegrityError(ValueError):
    pass


def default_signing_key_path() -> Path:
    return Path(user_config_dir("qualock")) / "project-protection.key"


def load_signing_key(path: Path | None = None) -> bytes:
    key_path = path or default_signing_key_path()
    try:
        key = key_path.read_bytes()
    except FileNotFoundError as exc:
        raise ProjectLockIntegrityError(f"project protection signing key is missing: {key_path}") from exc
    except OSError as exc:
        raise ProjectLockIntegrityError(f"unable to read project protection signing key: {key_path}: {exc}") from exc
    if len(key) != KEY_BYTES:
        raise ProjectLockIntegrityError(f"project protection signing key is invalid: expected {KEY_BYTES} bytes")
    return key


def ensure_signing_key(path: Path | None = None) -> bytes:
    key_path = path or default_signing_key_path()
    if key_path.exists():
        return load_signing_key(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_signing_key(key_path)
    except OSError as exc:
        raise ProjectLockIntegrityError(f"unable to create project protection signing key: {key_path}: {exc}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _canonical_lock_bytes(lock: ProjectLock) -> bytes:
    payload = lock.model_dump(mode="json")
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sign_project_lock(lock: ProjectLock, key: bytes) -> SignedProjectLock:
    digest = hmac.new(key, _canonical_lock_bytes(lock), hashlib.sha256).hexdigest()
    return SignedProjectLock(lock=lock, hmac_sha256=digest)


def verify_project_lock(envelope: SignedProjectLock, key: bytes) -> ProjectLock:
    expected = hmac.new(key, _canonical_lock_bytes(envelope.lock), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(envelope.hmac_sha256, expected):
        raise ProjectLockIntegrityError(
            "project protection lock signature does not match; the lock may have been changed"
        )
    return envelope.lock
