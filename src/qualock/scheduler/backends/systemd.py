from __future__ import annotations

import os
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

SYSTEMD_TIMEOUT_SECONDS = 30.0


def systemd_escape_argument(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def service_name(key: str) -> str:
    return f"qualock-release-monitor-{key}.service"


def render_service(registration: ScheduleRegistration) -> str:
    argv = [
        str(registration.python_executable),
        "-m",
        "qualock.scheduler.runner",
        "--project-key",
        registration.project_key,
    ]
    command = " ".join(systemd_escape_argument(value) for value in argv)
    return (
        "[Unit]\n"
        f"Description=QuaLock release monitor {registration.project_key}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={systemd_escape_argument(str(registration.runner_working_directory))}\n"
        f"ExecStart={command}\n"
    )


def render_timer(registration: ScheduleRegistration) -> str:
    return (
        "[Unit]\n"
        f"Description=QuaLock daily release monitor {registration.project_key}\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {registration.hour:02d}:{registration.minute:02d}:00\n"
        "Persistent=true\n"
        "AccuracySec=1min\n"
        f"Unit={service_name(registration.project_key)}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def manager_probe_argv() -> list[str]:
    return ["systemctl", "--user", "show-environment"]


def reload_argv() -> list[str]:
    return ["systemctl", "--user", "daemon-reload"]


def enable_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "enable", "--now", timer]


def enabled_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "is-enabled", timer]


def active_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "is-active", timer]


def load_state_argv(timer: str) -> list[str]:
    return [
        "systemctl",
        "--user",
        "show",
        "--property=LoadState",
        "--value",
        timer,
    ]


def disable_argv(timer: str) -> list[str]:
    return ["systemctl", "--user", "disable", "--now", timer]


def _is_missing_load_state(result: ProcessResult) -> bool:
    return result.stdout.strip() == "not-found" and not result.stderr.strip()


def _is_missing_unit(result: ProcessResult, timer: str) -> bool:
    if result.stdout.strip() or not result.stderr.strip():
        return False
    target = re.escape(timer)
    patterns = (
        rf"(?:Failed to disable unit: )?Unit (?:file )?{target} does not exist\.?",
        rf"Unit {target} (?:not found|could not be found|not loaded)\.?",
        rf"Failed to stop {target}: Unit {target} not loaded\.?",
    )
    return all(
        any(re.fullmatch(pattern, line) for pattern in patterns)
        for line in result.stderr.splitlines()
        if line
    )


def _state_output(result: ProcessResult) -> str:
    return result.stdout.strip().lower()


class SystemdUserBackend:
    kind = SchedulerBackendKind.SYSTEMD_USER

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        config_home: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._which = which
        resolved_home = Path.home() if home is None else home
        if config_home is None:
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", resolved_home / ".config"))
        self._unit_root = config_home / "systemd" / "user"

    def probe(self) -> None:
        if self._which("systemctl") is None:
            raise SchedulerUnsupportedError("systemctl is not available")
        try:
            result = self._run(manager_probe_argv())
        except SchedulerOperationalError as exc:
            raise SchedulerUnsupportedError("systemd user manager is unavailable") from exc
        if result.timed_out or result.exit_code != 0:
            raise SchedulerUnsupportedError("systemd user manager is unavailable")

    def _run(self, argv: list[str]) -> ProcessResult:
        try:
            return self._process_runner(
                argv, cwd=None, env=None, timeout_seconds=SYSTEMD_TIMEOUT_SECONDS
            )
        except OSError as exc:
            raise SchedulerOperationalError(f"failed to run systemctl: {exc}") from exc

    @staticmethod
    def _raise_failure(operation: str, result: ProcessResult) -> None:
        if result.timed_out:
            raise SchedulerOperationalError(f"systemctl {operation} timed out")
        detail = result.stderr.strip() or result.stdout.strip() or "unknown failure"
        raise SchedulerOperationalError(f"systemctl {operation} failed: {detail}")

    def _paths(self, key: str, timer: str) -> tuple[Path, Path]:
        return self._unit_root / service_name(key), self._unit_root / timer

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(payload.encode("utf-8"))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, registration: ScheduleRegistration) -> None:
        self._unit_root.mkdir(parents=True, exist_ok=True)
        service_path, timer_path = self._paths(
            registration.project_key, registration.native_id
        )
        self._atomic_write(service_path, render_service(registration))
        self._atomic_write(timer_path, render_timer(registration))
        reload_result = self._run(reload_argv())
        if reload_result.timed_out or reload_result.exit_code != 0:
            self._raise_failure("daemon-reload", reload_result)
        enable_result = self._run(enable_argv(registration.native_id))
        if enable_result.timed_out or enable_result.exit_code != 0:
            self._raise_failure("enable", enable_result)

    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection:
        service_path, timer_path = self._paths(identity.project_key, identity.native_id)
        files_present = service_path.exists() or timer_path.exists()
        if not files_present:
            load_state = self._run(load_state_argv(identity.native_id))
            if load_state.timed_out:
                self._raise_failure("show LoadState", load_state)
            if _is_missing_load_state(load_state):
                return NativeScheduleInspection(NativeScheduleState.MISSING)
            known_load_states = {
                "bad-setting",
                "error",
                "loaded",
                "masked",
                "merged",
                "stub",
            }
            if load_state.exit_code != 0 or _state_output(load_state) not in known_load_states:
                self._raise_failure("show LoadState", load_state)
        if expected is None:
            return NativeScheduleInspection(NativeScheduleState.PRESENT_BUT_UNVERIFIED)

        try:
            bytes_match = (
                service_path.read_bytes() == render_service(expected).encode("utf-8")
                and timer_path.read_bytes() == render_timer(expected).encode("utf-8")
            )
        except OSError:
            bytes_match = False
        enabled = self._run(enabled_argv(identity.native_id))
        known_enabled_states = {
            "alias",
            "bad",
            "disabled",
            "enabled",
            "enabled-runtime",
            "generated",
            "indirect",
            "linked",
            "linked-runtime",
            "masked",
            "masked-runtime",
            "not-found",
            "static",
            "transient",
        }
        if (
            enabled.timed_out
            or _state_output(enabled) not in known_enabled_states
            or (enabled.exit_code != 0 and bool(enabled.stderr.strip()))
        ):
            self._raise_failure("is-enabled", enabled)
        active = self._run(active_argv(identity.native_id))
        known_active_states = {
            "active",
            "activating",
            "deactivating",
            "failed",
            "inactive",
            "maintenance",
            "reloading",
            "unknown",
        }
        if (
            active.timed_out
            or _state_output(active) not in known_active_states
            or (active.exit_code != 0 and bool(active.stderr.strip()))
        ):
            self._raise_failure("is-active", active)
        matches = (
            bytes_match
            and enabled.exit_code == 0
            and not enabled.timed_out
            and enabled.stdout.strip() == "enabled"
            and active.exit_code == 0
            and not active.timed_out
            and active.stdout.strip() == "active"
        )
        state = NativeScheduleState.MATCHING if matches else NativeScheduleState.DRIFTED
        return NativeScheduleInspection(state)

    def remove(self, identity: ScheduleIdentity) -> None:
        result = self._run(disable_argv(identity.native_id))
        if (result.timed_out or result.exit_code != 0) and (
            result.timed_out or not _is_missing_unit(result, identity.native_id)
        ):
            self._raise_failure("disable", result)
        service_path, timer_path = self._paths(identity.project_key, identity.native_id)
        service_path.unlink(missing_ok=True)
        timer_path.unlink(missing_ok=True)
        reload_result = self._run(reload_argv())
        if reload_result.timed_out or reload_result.exit_code != 0:
            self._raise_failure("daemon-reload", reload_result)


__all__ = [
    "SYSTEMD_TIMEOUT_SECONDS",
    "SystemdUserBackend",
    "active_argv",
    "disable_argv",
    "enable_argv",
    "enabled_argv",
    "load_state_argv",
    "manager_probe_argv",
    "reload_argv",
    "render_service",
    "render_timer",
    "service_name",
    "systemd_escape_argument",
]
