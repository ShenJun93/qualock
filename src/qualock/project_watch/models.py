from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from qualock.project_protection.models import ProjectVerifyResult, ProtectionStatus


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


@dataclass(frozen=True)
class WatchTiming:
    poll_seconds: float = 0.5
    settle_seconds: float = 1.0
    max_unstable_cycles: int = 2


class WatchEventKind(str, Enum):
    CONTROL_VERIFIED = "control_verified"
    CHECKING = "checking"
    RESULT = "result"
    WATCHING = "watching"
    CHANGED = "changed"
    SETTLING = "settling"
    STALE = "stale"
    INSTABILITY_INCOMPLETE = "instability_incomplete"


@dataclass(frozen=True)
class WatchEvent:
    kind: WatchEventKind
    result: ProjectVerifyResult | None = None


@dataclass(frozen=True)
class StableCycle:
    stable: bool
    authoritative_result: ProjectVerifyResult | None
    post_snapshot: ProjectSnapshot


@dataclass(frozen=True)
class WatchOutcome:
    last_result: ProjectVerifyResult | None
    exit_status: ProtectionStatus | None
    interrupted: bool
