from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from qualock.project_protection.models import ProjectVerifyResult, ProtectionStatus
from qualock.project_watch.engine import run_stable_cycle, run_watch
from qualock.project_watch.models import (
    FileStamp,
    ProjectSnapshot,
    WatchControlIdentity,
    WatchEvent,
    WatchEventKind,
    WatchTiming,
)


def _snapshot(label: str) -> ProjectSnapshot:
    return ProjectSnapshot(
        files=(FileStamp(path="app.py", present=True, mode=33188, size=len(label), mtime_ns=hash(label)),)
    )


def _result(status: ProtectionStatus, operation_id: str) -> ProjectVerifyResult:
    return ProjectVerifyResult(
        operation_id=operation_id,
        created_at="2026-09-02T00:00:00+00:00",
        status=status,
        baseline_git_head="a" * 40,
        baseline_git_dirty=False,
        current_git_head="a" * 40,
        current_git_dirty=True,
        runs=[],
    )


def _queued(values):
    items = list(values)

    def pop(*args, **kwargs):
        if not items:
            raise AssertionError("queue exhausted")
        value = items.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    return pop


class FakeClock:
    def __init__(self, *, interrupt_after_sleeps: int | None = None) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.interrupt_after_sleeps = interrupt_after_sleeps

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds
        if self.interrupt_after_sleeps is not None and self.sleeps >= self.interrupt_after_sleeps:
            raise KeyboardInterrupt


def _freeze(*args, **kwargs) -> WatchControlIdentity:
    return WatchControlIdentity(lock_sha256="f" * 64)


def _assert_control(*args, **kwargs) -> None:
    return None


def test_stable_cycle_returns_authoritative_result_when_snapshot_unchanged(tmp_path: Path) -> None:
    snap = _snapshot("same")
    result = _result(ProtectionStatus.PASS, "verify-1")

    cycle = run_stable_cycle(
        tmp_path,
        frozen=_freeze(),
        snapshot_fn=_queued([snap, snap]),
        verify_fn=lambda *args, **kwargs: result,
        assert_control_fn=_assert_control,
    )

    assert cycle.authoritative_result is result
    assert cycle.post_snapshot == snap
    assert cycle.stable is True


def test_stable_cycle_suppresses_result_when_snapshot_changes(tmp_path: Path) -> None:
    before = _snapshot("before")
    after = _snapshot("after")

    cycle = run_stable_cycle(
        tmp_path,
        frozen=_freeze(),
        snapshot_fn=_queued([before, after]),
        verify_fn=lambda *args, **kwargs: _result(ProtectionStatus.PASS, "stale"),
        assert_control_fn=_assert_control,
    )

    assert cycle.authoritative_result is None
    assert cycle.post_snapshot == after
    assert cycle.stable is False


def test_stable_cycle_checks_control_before_and_after_verify(tmp_path: Path) -> None:
    order: list[str] = []
    snap = _snapshot("same")

    def assert_control(*args, **kwargs) -> None:
        order.append("control")

    def snapshot(*args, **kwargs) -> ProjectSnapshot:
        order.append("snapshot")
        return snap

    def verify(*args, **kwargs) -> ProjectVerifyResult:
        order.append("verify")
        return _result(ProtectionStatus.PASS, "verify-1")

    run_stable_cycle(
        tmp_path,
        frozen=_freeze(),
        snapshot_fn=snapshot,
        verify_fn=verify,
        assert_control_fn=assert_control,
    )

    assert order == ["control", "snapshot", "verify", "control", "snapshot"]


def test_control_failure_after_verify_propagates_and_suppresses_result(tmp_path: Path) -> None:
    snap = _snapshot("same")
    checks = 0

    def assert_control(*args, **kwargs) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("control changed")

    with pytest.raises(RuntimeError, match="control changed"):
        run_stable_cycle(
            tmp_path,
            frozen=_freeze(),
            snapshot_fn=_queued([snap]),
            verify_fn=lambda *args, **kwargs: _result(ProtectionStatus.PASS, "stale"),
            assert_control_fn=assert_control,
        )


def test_run_watch_initial_pass_becomes_authoritative_before_polling(tmp_path: Path) -> None:
    snap = _snapshot("same")
    clock = FakeClock(interrupt_after_sleeps=1)
    events: list[WatchEvent] = []

    outcome = run_watch(
        tmp_path,
        snapshot_fn=_queued([snap, snap]),
        verify_fn=lambda *args, **kwargs: _result(ProtectionStatus.PASS, "initial"),
        freeze_control_fn=_freeze,
        assert_control_fn=_assert_control,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
        on_event=events.append,
    )

    assert outcome.interrupted is True
    assert outcome.last_result is not None
    assert outcome.last_result.operation_id == "initial"
    assert outcome.exit_status is ProtectionStatus.PASS
    assert [event.kind for event in events].count(WatchEventKind.RESULT) == 1


def test_run_watch_debounces_change_burst_into_one_followup_verify(tmp_path: Path) -> None:
    a, b, c = _snapshot("a"), _snapshot("bb"), _snapshot("ccc")
    snapshots = _queued([a, a, b, c, c, c, c, c])
    results = _queued([
        _result(ProtectionStatus.PASS, "initial"),
        _result(ProtectionStatus.PASS, "after-burst"),
    ])
    clock = FakeClock(interrupt_after_sleeps=5)
    events: list[WatchEvent] = []

    outcome = run_watch(
        tmp_path,
        timing=WatchTiming(poll_seconds=0.5, settle_seconds=1.0, max_unstable_cycles=2),
        snapshot_fn=snapshots,
        verify_fn=results,
        freeze_control_fn=_freeze,
        assert_control_fn=_assert_control,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
        on_event=events.append,
    )

    assert outcome.last_result is not None
    assert outcome.last_result.operation_id == "after-burst"
    assert [event.kind for event in events].count(WatchEventKind.RESULT) == 2
    assert [event.kind for event in events].count(WatchEventKind.CHANGED) == 1


def test_unstable_result_is_not_emitted_and_stable_retry_wins(tmp_path: Path) -> None:
    a, b, c = _snapshot("a"), _snapshot("bb"), _snapshot("ccc")
    snapshots = _queued([a, a, b, b, b, b, c, c, c, c, c])
    results = _queued([
        _result(ProtectionStatus.PASS, "initial"),
        _result(ProtectionStatus.PASS, "stale-pass"),
        _result(ProtectionStatus.FAIL, "stable-fail"),
    ])
    clock = FakeClock(interrupt_after_sleeps=6)
    events: list[WatchEvent] = []

    outcome = run_watch(
        tmp_path,
        snapshot_fn=snapshots,
        verify_fn=results,
        freeze_control_fn=_freeze,
        assert_control_fn=_assert_control,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
        on_event=events.append,
    )

    emitted_ids = [
        event.result.operation_id
        for event in events
        if event.kind is WatchEventKind.RESULT and event.result is not None
    ]
    assert "stale-pass" not in emitted_ids
    assert emitted_ids[-1] == "stable-fail"
    assert WatchEventKind.STALE in [event.kind for event in events]
    assert outcome.exit_status is ProtectionStatus.FAIL


def test_two_consecutive_unstable_cycles_yield_incomplete_without_third_retry(tmp_path: Path) -> None:
    a, b, c, d = (_snapshot("a"), _snapshot("bb"), _snapshot("ccc"), _snapshot("dddd"))
    snapshots = _queued([a, a, b, b, b, b, c, c, c, c, d, d])
    results = _queued([
        _result(ProtectionStatus.PASS, "initial"),
        _result(ProtectionStatus.PASS, "unstable-1"),
        _result(ProtectionStatus.PASS, "unstable-2"),
    ])
    clock = FakeClock(interrupt_after_sleeps=7)
    events: list[WatchEvent] = []

    outcome = run_watch(
        tmp_path,
        snapshot_fn=snapshots,
        verify_fn=results,
        freeze_control_fn=_freeze,
        assert_control_fn=_assert_control,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
        on_event=events.append,
    )

    assert WatchEventKind.INSTABILITY_INCOMPLETE in [event.kind for event in events]
    assert outcome.exit_status is ProtectionStatus.INCOMPLETE


def test_keyboard_interrupt_during_followup_verify_keeps_previous_authoritative_result(
    tmp_path: Path,
) -> None:
    a, b = _snapshot("a"), _snapshot("bb")
    snapshots = _queued([a, a, b, b, b, b])
    verify_calls = 0

    def verify(*args, **kwargs) -> ProjectVerifyResult:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise KeyboardInterrupt
        return _result(ProtectionStatus.PASS, "initial")

    clock = FakeClock()

    outcome = run_watch(
        tmp_path,
        snapshot_fn=snapshots,
        verify_fn=verify,
        freeze_control_fn=_freeze,
        assert_control_fn=_assert_control,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert outcome.interrupted is True
    assert outcome.last_result is not None
    assert outcome.last_result.operation_id == "initial"
    assert outcome.exit_status is ProtectionStatus.PASS
