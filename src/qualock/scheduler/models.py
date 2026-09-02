from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from qualock.release_monitor.state import project_key
from qualock.run.process import ProcessResult, run_process


class SchedulerBackendKind(str, Enum):
    WINDOWS_TASK_SCHEDULER = "windows_task_scheduler"
    SYSTEMD_USER = "systemd_user"
    LAUNCHD_AGENT = "launchd_agent"


_BACKEND_LABELS = {
    SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: "Windows Task Scheduler",
    SchedulerBackendKind.SYSTEMD_USER: "systemd user timer",
    SchedulerBackendKind.LAUNCHD_AGENT: "macOS LaunchAgent",
}


def backend_label(kind: SchedulerBackendKind) -> str:
    return _BACKEND_LABELS[kind]


class NativeScheduleState(str, Enum):
    MISSING = "missing"
    MATCHING = "matching"
    PRESENT_BUT_UNVERIFIED = "present_but_unverified"
    DRIFTED = "drifted"


class ScheduleRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    project_key: str
    project_root: Path
    backend: SchedulerBackendKind
    native_id: str
    hour: int
    minute: int
    python_executable: Path
    runner_working_directory: Path
    path_env: str
    enabled_at: datetime

    @field_validator("enabled_at")
    @classmethod
    def validate_enabled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("enabled_at must be a UTC timestamp")
        return value

    @model_validator(mode="after")
    def validate_registration(self) -> ScheduleRegistration:
        canonical_root = self.project_root.expanduser().resolve(strict=False)
        if not self.project_root.is_absolute() or canonical_root != self.project_root:
            raise ValueError("project_root must be an absolute canonical path")
        if re.fullmatch(r"[0-9a-f]{64}", self.project_key) is None:
            raise ValueError("project_key must be 64 lowercase hexadecimal characters")
        if self.project_key != project_key(canonical_root):
            raise ValueError("project_key does not match project_root")
        if self.native_id != native_id_for(self.backend, self.project_key):
            raise ValueError("native_id does not match backend and project_key")
        if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ValueError("hour or minute is outside the daily clock range")
        if not self.python_executable.is_absolute():
            raise ValueError("python_executable must be absolute")
        if not self.runner_working_directory.is_absolute():
            raise ValueError("runner_working_directory must be absolute")
        return self


@dataclass(frozen=True)
class ScheduleIdentity:
    project_key: str
    backend: SchedulerBackendKind
    native_id: str


@dataclass(frozen=True)
class NativeScheduleInspection:
    state: NativeScheduleState
    detail: str | None = None


class ProcessRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float,
    ) -> ProcessResult: ...


_DAILY_TIME = re.compile(r"^(\d{2}):(\d{2})$")


def parse_daily_time(value: str) -> tuple[int, int]:
    match = _DAILY_TIME.fullmatch(value)
    if match is None:
        raise ValueError("daily time must use HH:MM")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("daily time must use HH:MM")
    return hour, minute


def native_id_for(kind: SchedulerBackendKind, key: str) -> str:
    prefix = {
        SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: "QuaLock-ReleaseMonitor-",
        SchedulerBackendKind.SYSTEMD_USER: "qualock-release-monitor-",
        SchedulerBackendKind.LAUNCHD_AGENT: "io.qualock.release-monitor.",
    }[kind]
    suffix = ".timer" if kind is SchedulerBackendKind.SYSTEMD_USER else ""
    return f"{prefix}{key}{suffix}"


def schedule_identity(root: Path, kind: SchedulerBackendKind) -> ScheduleIdentity:
    canonical_root = root.expanduser().resolve(strict=True)
    key = project_key(canonical_root)
    return ScheduleIdentity(key, kind, native_id_for(kind, key))


def operationally_equal(left: ScheduleRegistration, right: ScheduleRegistration) -> bool:
    return left.model_dump(exclude={"enabled_at"}) == right.model_dump(exclude={"enabled_at"})


__all__ = [
    "NativeScheduleInspection",
    "NativeScheduleState",
    "ProcessRunner",
    "ScheduleIdentity",
    "ScheduleRegistration",
    "SchedulerBackendKind",
    "backend_label",
    "native_id_for",
    "operationally_equal",
    "parse_daily_time",
    "run_process",
    "schedule_identity",
]
