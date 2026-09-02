from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from qualock.project_protection.commands import execute_verify
from qualock.project_protection.models import ProjectVerifyResult, ProtectionStatus

from .control import assert_watch_control, freeze_watch_control
from .models import (
    ProjectSnapshot,
    StableCycle,
    WatchControlIdentity,
    WatchEvent,
    WatchEventKind,
    WatchOutcome,
    WatchTiming,
)
from .snapshot import capture_project_snapshot

SnapshotFn = Callable[[Path], ProjectSnapshot]
VerifyFn = Callable[..., ProjectVerifyResult]
FreezeControlFn = Callable[..., WatchControlIdentity]
AssertControlFn = Callable[..., None]
EventFn = Callable[[WatchEvent], None]
DEFAULT_WATCH_TIMING = WatchTiming()


def _emit(on_event: EventFn | None, kind: WatchEventKind, result: ProjectVerifyResult | None = None) -> None:
    if on_event is not None:
        on_event(WatchEvent(kind=kind, result=result))


def run_stable_cycle(
    root: Path,
    *,
    frozen: WatchControlIdentity,
    key_path: Path | None = None,
    snapshot_fn: SnapshotFn = capture_project_snapshot,
    verify_fn: VerifyFn = execute_verify,
    assert_control_fn: AssertControlFn = assert_watch_control,
) -> StableCycle:
    assert_control_fn(root, frozen, key_path=key_path)
    before = snapshot_fn(root)
    result = verify_fn(root, key_path=key_path)
    assert_control_fn(root, frozen, key_path=key_path)
    after = snapshot_fn(root)
    if before == after:
        return StableCycle(
            stable=True,
            authoritative_result=result,
            post_snapshot=after,
        )
    return StableCycle(
        stable=False,
        authoritative_result=None,
        post_snapshot=after,
    )


def run_watch(
    root: Path,
    *,
    timing: WatchTiming = DEFAULT_WATCH_TIMING,
    key_path: Path | None = None,
    snapshot_fn: SnapshotFn = capture_project_snapshot,
    verify_fn: VerifyFn = execute_verify,
    freeze_control_fn: FreezeControlFn = freeze_watch_control,
    assert_control_fn: AssertControlFn = assert_watch_control,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_event: EventFn | None = None,
) -> WatchOutcome:
    frozen = freeze_control_fn(root, key_path=key_path)
    _emit(on_event, WatchEventKind.CONTROL_VERIFIED)

    last_result: ProjectVerifyResult | None = None
    exit_status: ProtectionStatus | None = None
    observed: ProjectSnapshot | None = None
    pending = False
    last_change_at: float | None = None
    unstable_cycles = 0

    try:
        _emit(on_event, WatchEventKind.CHECKING)
        cycle = run_stable_cycle(
            root,
            frozen=frozen,
            key_path=key_path,
            snapshot_fn=snapshot_fn,
            verify_fn=verify_fn,
            assert_control_fn=assert_control_fn,
        )
        observed = cycle.post_snapshot
        if cycle.stable:
            assert cycle.authoritative_result is not None
            last_result = cycle.authoritative_result
            exit_status = last_result.status
            _emit(on_event, WatchEventKind.RESULT, last_result)
            _emit(on_event, WatchEventKind.WATCHING)
        else:
            unstable_cycles = 1
            _emit(on_event, WatchEventKind.STALE)
            if unstable_cycles >= timing.max_unstable_cycles:
                exit_status = ProtectionStatus.INCOMPLETE
                unstable_cycles = 0
                _emit(on_event, WatchEventKind.INSTABILITY_INCOMPLETE)
                _emit(on_event, WatchEventKind.WATCHING)
            else:
                pending = True
                last_change_at = monotonic_fn()
                _emit(on_event, WatchEventKind.SETTLING)

        while True:
            sleep_fn(timing.poll_seconds)
            assert_control_fn(root, frozen, key_path=key_path)
            current = snapshot_fn(root)
            now = monotonic_fn()

            if current != observed:
                first_change_in_burst = not pending
                observed = current
                pending = True
                last_change_at = now
                if first_change_in_burst:
                    _emit(on_event, WatchEventKind.CHANGED)
                    _emit(on_event, WatchEventKind.SETTLING)
                continue

            if not pending or last_change_at is None:
                continue
            if now - last_change_at < timing.settle_seconds:
                continue

            _emit(on_event, WatchEventKind.CHECKING)
            cycle = run_stable_cycle(
                root,
                frozen=frozen,
                key_path=key_path,
                snapshot_fn=snapshot_fn,
                verify_fn=verify_fn,
                assert_control_fn=assert_control_fn,
            )
            observed = cycle.post_snapshot

            if cycle.stable:
                assert cycle.authoritative_result is not None
                last_result = cycle.authoritative_result
                exit_status = last_result.status
                pending = False
                last_change_at = None
                unstable_cycles = 0
                _emit(on_event, WatchEventKind.RESULT, last_result)
                _emit(on_event, WatchEventKind.WATCHING)
                continue

            unstable_cycles += 1
            _emit(on_event, WatchEventKind.STALE)
            if unstable_cycles >= timing.max_unstable_cycles:
                exit_status = ProtectionStatus.INCOMPLETE
                pending = False
                last_change_at = None
                unstable_cycles = 0
                _emit(on_event, WatchEventKind.INSTABILITY_INCOMPLETE)
                _emit(on_event, WatchEventKind.WATCHING)
                continue

            pending = True
            last_change_at = monotonic_fn()
            _emit(on_event, WatchEventKind.SETTLING)
    except KeyboardInterrupt:
        return WatchOutcome(
            last_result=last_result,
            exit_status=exit_status,
            interrupted=True,
        )
