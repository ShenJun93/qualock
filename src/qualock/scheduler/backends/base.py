from __future__ import annotations

from typing import Protocol

from ..models import (
    NativeScheduleInspection,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
)


class SchedulerError(RuntimeError):
    pass


class SchedulerUnsupportedError(SchedulerError):
    pass


class SchedulerOperationalError(SchedulerError):
    def __init__(self, message: str, *, rollback_uncertain: bool = False) -> None:
        super().__init__(message)
        self.rollback_uncertain = rollback_uncertain


class SchedulerBackend(Protocol):
    kind: SchedulerBackendKind

    def probe(self) -> None: ...

    def install(self, registration: ScheduleRegistration) -> None: ...

    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection: ...

    def remove(self, identity: ScheduleIdentity) -> None: ...


__all__ = [
    "SchedulerBackend",
    "SchedulerError",
    "SchedulerOperationalError",
    "SchedulerUnsupportedError",
]
