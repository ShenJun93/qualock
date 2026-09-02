from __future__ import annotations

import getpass
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

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

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
WINDOWS_TIMEOUT_SECONDS = 30.0


def _q(tag: str) -> str:
    return f"{{{TASK_NS}}}{tag}"


def runner_arguments(registration: ScheduleRegistration) -> str:
    return subprocess.list2cmdline(
        ["-m", "qualock.scheduler.runner", "--project-key", registration.project_key]
    )


def task_xml(registration: ScheduleRegistration, *, user_id: str) -> bytes:
    ET.register_namespace("", TASK_NS)
    task = ET.Element(_q("Task"), {"version": "1.4"})
    triggers = ET.SubElement(task, _q("Triggers"))
    calendar = ET.SubElement(triggers, _q("CalendarTrigger"))
    ET.SubElement(calendar, _q("StartBoundary")).text = (
        f"2000-01-01T{registration.hour:02d}:{registration.minute:02d}:00"
    )
    schedule = ET.SubElement(calendar, _q("ScheduleByDay"))
    ET.SubElement(schedule, _q("DaysInterval")).text = "1"

    principals = ET.SubElement(task, _q("Principals"))
    principal = ET.SubElement(principals, _q("Principal"), {"id": "CurrentUser"})
    ET.SubElement(principal, _q("UserId")).text = user_id
    ET.SubElement(principal, _q("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, _q("RunLevel")).text = "LeastPrivilege"

    settings = ET.SubElement(task, _q("Settings"))
    ET.SubElement(settings, _q("StartWhenAvailable")).text = "true"
    ET.SubElement(settings, _q("MultipleInstancesPolicy")).text = "IgnoreNew"

    actions = ET.SubElement(task, _q("Actions"), {"Context": "CurrentUser"})
    execute = ET.SubElement(actions, _q("Exec"))
    ET.SubElement(execute, _q("Command")).text = str(registration.python_executable)
    ET.SubElement(execute, _q("Arguments")).text = runner_arguments(registration)
    ET.SubElement(execute, _q("WorkingDirectory")).text = str(
        registration.runner_working_directory
    )
    return cast(bytes, ET.tostring(task, encoding="utf-8", xml_declaration=True))


def create_argv(native_id: str, xml_path: Path) -> list[str]:
    return ["schtasks.exe", "/Create", "/TN", native_id, "/XML", str(xml_path), "/F"]


def query_argv(native_id: str) -> list[str]:
    return ["schtasks.exe", "/Query", "/TN", native_id, "/XML"]


def delete_argv(native_id: str) -> list[str]:
    return ["schtasks.exe", "/Delete", "/TN", native_id, "/F"]


def _is_missing(stderr: str) -> bool:
    return "the system cannot find the file specified" in stderr.lower()


class WindowsTaskSchedulerBackend:
    kind = SchedulerBackendKind.WINDOWS_TASK_SCHEDULER

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        temp_dir: Path | None = None,
        user_id: str | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._which = which
        self._temp_dir = temp_dir
        self._user_id = getpass.getuser() if user_id is None else user_id

    def probe(self) -> None:
        if self._which("schtasks.exe") is None:
            raise SchedulerUnsupportedError("schtasks.exe is not available")

    def _run(self, argv: list[str]) -> ProcessResult:
        try:
            return self._process_runner(
                argv, cwd=None, env=None, timeout_seconds=WINDOWS_TIMEOUT_SECONDS
            )
        except OSError as exc:
            raise SchedulerOperationalError(f"failed to run schtasks.exe: {exc}") from exc

    @staticmethod
    def _raise_failure(operation: str, result: ProcessResult) -> None:
        if result.timed_out:
            raise SchedulerOperationalError(f"schtasks.exe {operation} timed out")
        detail = result.stderr.strip() or result.stdout.strip() or "unknown failure"
        raise SchedulerOperationalError(f"schtasks.exe {operation} failed: {detail}")

    def install(self, registration: ScheduleRegistration) -> None:
        xml_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".xml", dir=self._temp_dir, delete=False
            ) as handle:
                handle.write(task_xml(registration, user_id=self._user_id))
                xml_path = Path(handle.name)
            result = self._run(create_argv(registration.native_id, xml_path))
            if result.timed_out or result.exit_code != 0:
                self._raise_failure("create", result)
        finally:
            if xml_path is not None:
                xml_path.unlink(missing_ok=True)

    def inspect(
        self,
        identity: ScheduleIdentity,
        expected: ScheduleRegistration | None,
    ) -> NativeScheduleInspection:
        result = self._run(query_argv(identity.native_id))
        if result.timed_out or result.exit_code != 0:
            if not result.timed_out and _is_missing(result.stderr):
                return NativeScheduleInspection(NativeScheduleState.MISSING)
            self._raise_failure("query", result)
        if expected is None:
            return NativeScheduleInspection(NativeScheduleState.PRESENT_BUT_UNVERIFIED)
        return self._inspect_xml(result.stdout, expected)

    def _inspect_xml(
        self, payload: str, expected: ScheduleRegistration
    ) -> NativeScheduleInspection:
        try:
            root = ET.fromstring(payload)
            values = {
                name: root.findtext(f".//{_q(name)}")
                for name in (
                    "StartBoundary",
                    "DaysInterval",
                    "UserId",
                    "LogonType",
                    "RunLevel",
                    "StartWhenAvailable",
                    "MultipleInstancesPolicy",
                    "Command",
                    "Arguments",
                    "WorkingDirectory",
                )
            }
            if any(value is None for value in values.values()):
                raise ValueError("required task XML field is missing")
            boundary = datetime.fromisoformat(values["StartBoundary"] or "")
        except (ET.ParseError, ValueError, TypeError):
            return NativeScheduleInspection(NativeScheduleState.PRESENT_BUT_UNVERIFIED)

        matches = (
            (boundary.hour, boundary.minute) == (expected.hour, expected.minute)
            and values["DaysInterval"] == "1"
            and values["UserId"] == self._user_id
            and values["LogonType"] == "InteractiveToken"
            and values["RunLevel"] == "LeastPrivilege"
            and values["StartWhenAvailable"] == "true"
            and values["MultipleInstancesPolicy"] == "IgnoreNew"
            and values["Command"] == str(expected.python_executable)
            and values["Arguments"] == runner_arguments(expected)
            and values["WorkingDirectory"] == str(expected.runner_working_directory)
        )
        state = NativeScheduleState.MATCHING if matches else NativeScheduleState.DRIFTED
        return NativeScheduleInspection(state)

    def remove(self, identity: ScheduleIdentity) -> None:
        result = self._run(delete_argv(identity.native_id))
        if result.timed_out or result.exit_code != 0:
            if not result.timed_out and _is_missing(result.stderr):
                return
            self._raise_failure("delete", result)


__all__ = [
    "TASK_NS",
    "WINDOWS_TIMEOUT_SECONDS",
    "WindowsTaskSchedulerBackend",
    "create_argv",
    "delete_argv",
    "query_argv",
    "runner_arguments",
    "task_xml",
]
