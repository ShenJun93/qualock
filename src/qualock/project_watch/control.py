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


def _read_raw_lock(root: Path) -> bytes:
    return (project_dir(root) / "project.lock").read_bytes()


def _authenticate_raw_lock(raw: bytes, key_path: Path | None) -> None:
    key = load_signing_key(key_path)
    parse_project_lock_bytes(raw, key)


def freeze_watch_control(
    root: Path,
    *,
    key_path: Path | None = None,
) -> WatchControlIdentity:
    raw = _read_raw_lock(root)
    _authenticate_raw_lock(raw, key_path)
    return WatchControlIdentity(lock_sha256=hashlib.sha256(raw).hexdigest())


def assert_watch_control(
    root: Path,
    frozen: WatchControlIdentity,
    *,
    key_path: Path | None = None,
) -> None:
    try:
        raw = _read_raw_lock(root)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        raise WatchControlChangedError(
            "project protection lock disappeared during this watch session; "
            "restart qualock watch after intentionally re-protecting the project"
        ) from exc
    _authenticate_raw_lock(raw, key_path)
    current = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(current, frozen.lock_sha256):
        raise WatchControlChangedError(
            "project protection lock changed during this watch session; "
            "restart qualock watch after intentionally re-protecting the project"
        )
