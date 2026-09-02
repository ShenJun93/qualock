from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from qualock.project import project_dir
from qualock.project_protection.io import parse_project_lock_bytes
from qualock.project_protection.signing import load_signing_key

from .models import WatchControlIdentity


class WatchControlChangedError(RuntimeError):
    pass


def _authenticated_raw_lock(root: Path, key_path: Path | None) -> bytes:
    lock_path = project_dir(root) / "project.lock"
    raw = lock_path.read_bytes()
    key = load_signing_key(key_path)
    parse_project_lock_bytes(raw, key)
    return raw


def freeze_watch_control(
    root: Path,
    *,
    key_path: Path | None = None,
) -> WatchControlIdentity:
    raw = _authenticated_raw_lock(root, key_path)
    return WatchControlIdentity(lock_sha256=hashlib.sha256(raw).hexdigest())


def assert_watch_control(
    root: Path,
    frozen: WatchControlIdentity,
    *,
    key_path: Path | None = None,
) -> None:
    raw = _authenticated_raw_lock(root, key_path)
    current = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(current, frozen.lock_sha256):
        raise WatchControlChangedError(
            "project protection lock changed during this watch session; "
            "restart qualock watch after intentionally re-protecting the project"
        )
