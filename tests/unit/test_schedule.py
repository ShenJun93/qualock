from collections import Counter

from qualock.run.schedule import Side, paired_schedule


def test_schedule_has_one_slot_per_side_per_repetition() -> None:
    schedule = paired_schedule("canary", repetitions=3, qualification_id="q1")
    counts = Counter((slot.side, slot.repetition) for slot in schedule)
    for repetition in range(1, 4):
        assert counts[(Side.BASELINE, repetition)] == 1
        assert counts[(Side.CANDIDATE, repetition)] == 1


def test_schedule_is_deterministic_for_same_qualification() -> None:
    assert paired_schedule("canary", 5, "q1") == paired_schedule("canary", 5, "q1")


def test_schedule_is_interleaved_not_grouped_by_side() -> None:
    schedule = paired_schedule("canary", 4, "q1")
    sides = [slot.side for slot in schedule]
    assert sides != [Side.BASELINE] * 4 + [Side.CANDIDATE] * 4
    for index in range(0, len(schedule), 2):
        assert {schedule[index].side, schedule[index + 1].side} == {
            Side.BASELINE,
            Side.CANDIDATE,
        }
        assert schedule[index].repetition == schedule[index + 1].repetition


def test_qualification_id_can_change_pair_order() -> None:
    schedules = {
        tuple(slot.side for slot in paired_schedule("canary", 6, qualification_id))
        for qualification_id in ["q1", "q2", "q3", "q4", "q5", "q6"]
    }
    assert len(schedules) > 1
