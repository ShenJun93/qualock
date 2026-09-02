from __future__ import annotations

import os
import plistlib
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from qualock.run.process import ProcessResult

from ..models import (
    NativeScheduleInspection,
    NativeScheduleState,
    ProcessRunner,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    run_process,
)
from .base import SchedulerOperationalError, SchedulerUnsupportedError

LAUNCHD_TIMEOUT_SECONDS = 30.0


def render_plist(registration: ScheduleRegistration) -> bytes:
    payload: dict[str, object] = {
        "Label": registration.native_id,
        "ProgramArguments": [
            str(registration.python_executable),
            "-m",
            "qualock.scheduler.runner",
            "--project-key",
            registration.project_key,
        ],
        "WorkingDirectory": str(registration.runner_working_directory),
        "StartCalendarInterval": {
            "Hour": registration.hour,
            "Minute": registration.minute,
        },
        "RunAtLoad": False,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def launch_domain(uid: int) -> str:
    return f"gui/{uid}"


def service_target(uid: int, label: str) -> str:
    return f"{launch_domain(uid)}/{label}"


def bootstrap_argv(uid: int, plist_path: Path) -> list[str]:
    return ["launchctl", "bootstrap", launch_domain(uid), str(plist_path)]


def bootout_argv(uid: int, label: str) -> list[str]:
    return ["launchctl", "bootout", service_target(uid, label)]


def print_argv(uid: int, label: str) -> list[str]:
    return ["launchctl", "print", service_target(uid, label)]


def _is_absent_service(result: ProcessResult, uid: int, label: str) -> bool:
    if result.timed_out or result.exit_code == 0 or result.stdout.strip():
        return False
    expected = (
        rf'Could not find service "{re.escape(label)}" '
        rf"in domain for user gui: {uid}"
    )
    lines = [line for line in result.stderr.splitlines() if line]
    return bool(lines) and all(re.fullmatch(expected, line) for line in lines)


class LaunchdAgentBackend:
    kind = SchedulerBackendKind.LAUNCHD_AGENT

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        home: Path | None = None,
        uid: int | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._which = which
        self._home = Path.home() if home is None else home
        self._uid = os.getuid() if uid is None else uid
        self._agent_root = self._home / "Library" / "LaunchAgents"

    def probe(self) -> None:
        if self._which("launchctl") is None:
            raise SchedulerUnsupportedError("launchctl is not available")
        if self._uid < 0:
            raise SchedulerUnsupportedError("current user UID is unavailable")

    def _path(self, native_id: str) -> Path:
        return self._agent_root / f"{native_id}.plist"

    def _run(self, argv: list[str]) -> ProcessResult:
        try:
            return self._process_runner(
                argv, cwd=None, env=None, timeout_seconds=LAUNCHD_TIMEOUT_SECONDS
            )
        except OSError as exc:
            raise SchedulerOperationalError(f"failed to run launchctl: {exc}") from exc

    @staticmethod
    def _raise_failure(operation: str, result: ProcessResult) -> None:
        if result.timed_out:
            raise SchedulerOperationalError(f"launchctl {operation} timed out")
        detail = result.stderr.strip() or result.stdout.strip() or "unknown failure"
        raise SchedulerOperationalError(f"launchctl {operation} failed: {detail}")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.touch(mode=0o600, exist_ok=False)
            temporary.chmod(0o600)
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, registration: ScheduleRegistration) -> None:
        bootout = self._run(bootout_argv(self._uid, registration.native_id))
        if (bootout.timed_out or bootout.exit_code != 0) and not _is_absent_service(
            bootout, self._uid, registration.native_id
        ):
            self._raise_failure("bootout", bootout)

        self._agent_root.mkdir(parents=True, exist_ok=True)
        plist_path = self._path(registration.native_id)
        self._atomic_write(plist_path, render_plist(registration))
        bootstrap = self._run(bootstrap_argv(self._uid, plist_path))
        if bootstrap.timed_out or bootstrap.exit_code != 0:
            self._raise_failure("bootstrap", bootstrap)

    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection:
        plist_path = self._path(identity.native_id)
        plist_present = plist_path.exists()
        printed = self._run(print_argv(self._uid, identity.native_id))
        if printed.timed_out:
            self._raise_failure("print", printed)
        loaded = printed.exit_code == 0
        if not loaded and not _is_absent_service(printed, self._uid, identity.native_id):
            self._raise_failure("print", printed)

        if expected is None:
            state = (
                NativeScheduleState.PRESENT_BUT_UNVERIFIED
                if plist_present or loaded
                else NativeScheduleState.MISSING
            )
            return NativeScheduleInspection(state)

        try:
            bytes_match = plist_path.read_bytes() == render_plist(expected)
        except OSError:
            bytes_match = False
        state = (
            NativeScheduleState.MATCHING
            if bytes_match and loaded
            else NativeScheduleState.DRIFTED
        )
        return NativeScheduleInspection(state)

    def remove(self, identity: ScheduleIdentity) -> None:
        bootout = self._run(bootout_argv(self._uid, identity.native_id))
        if (bootout.timed_out or bootout.exit_code != 0) and not _is_absent_service(
            bootout, self._uid, identity.native_id
        ):
            self._raise_failure("bootout", bootout)
        self._path(identity.native_id).unlink(missing_ok=True)


__all__ = [
    "LAUNCHD_TIMEOUT_SECONDS",
    "LaunchdAgentBackend",
    "bootout_argv",
    "bootstrap_argv",
    "launch_domain",
    "print_argv",
    "render_plist",
    "service_target",
]
