from dataclasses import dataclass
from enum import Enum

from qualock.qualification.models import Verdict


class BisectStop(str, Enum):
    NO_BAD_FOUND = "no_bad_found"
    FIRST_BAD_FOUND = "first_bad_found"
    WARN_UNRESOLVED = "warn_unresolved"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class BisectStep:
    version: str
    qualification_id: str
    verdict: Verdict


@dataclass(frozen=True)
class BisectOutcome:
    bisect_id: str
    baseline_version: str
    upper_version: str
    steps: tuple[BisectStep, ...]
    last_known_good: str
    first_bad: str | None
    stop_reason: BisectStop
