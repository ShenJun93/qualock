import hashlib
from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"


@dataclass(frozen=True)
class RunSlot:
    side: Side
    repetition: int


def paired_schedule(canary_id: str, repetitions: int, qualification_id: str) -> tuple[RunSlot, ...]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    slots: list[RunSlot] = []
    digest = hashlib.sha256(f"{qualification_id}:{canary_id}".encode()).digest()
    first_orientation = Side.BASELINE if digest[0] % 2 == 0 else Side.CANDIDATE
    for repetition in range(1, repetitions + 1):
        first = (
            first_orientation
            if repetition % 2 == 1
            else Side.CANDIDATE if first_orientation is Side.BASELINE else Side.BASELINE
        )
        second = Side.CANDIDATE if first is Side.BASELINE else Side.BASELINE
        slots.extend([RunSlot(first, repetition), RunSlot(second, repetition)])
    return tuple(slots)
