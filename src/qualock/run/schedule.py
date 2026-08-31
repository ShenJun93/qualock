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
    for repetition in range(1, repetitions + 1):
        digest = hashlib.sha256(
            f"{qualification_id}:{canary_id}:{repetition}".encode("utf-8")
        ).digest()
        first = Side.BASELINE if digest[0] % 2 == 0 else Side.CANDIDATE
        second = Side.CANDIDATE if first is Side.BASELINE else Side.BASELINE
        slots.extend([RunSlot(first, repetition), RunSlot(second, repetition)])
    return tuple(slots)
