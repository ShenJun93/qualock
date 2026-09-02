from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class FileStamp:
    path: str
    present: bool
    mode: int | None
    size: int | None
    mtime_ns: int | None


@dataclass(frozen=True)
class ProjectSnapshot:
    files: tuple[FileStamp, ...]


@dataclass(frozen=True)
class WatchControlIdentity:
    lock_sha256: str
