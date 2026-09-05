from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from qualock import cli
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.release_monitor.state import project_key
from qualock.scheduler.backends import (
    SchedulerOperationalError,
    SchedulerUnsupportedError,
)
from qualock.scheduler.commands import ScheduleOutcome, ScheduleStatus
from qualock.scheduler.models import (
    SchedulerBackendKind,
    ScheduleRegistration,
    native_id_for,
)

runner = CliRunner()


def sample_registration(
    root: Path, *, hour: int = 9, minute: int = 0
) -> ScheduleRegistration:
    canonical = root.resolve()
    key = project_key(canonical)
    return ScheduleRegistration(
        project_key=key,
        project_root=canonical,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=hour,
        minute=minute,
        python_executable=canonical / "runtime" / "qualock-python",
        runner_working_directory=canonical,
        path_env="/usr/bin",
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def sample_outcome(
    status: ScheduleStatus,
    *,
    project_root: Path,
    detail: str | None = None,
) -> ScheduleOutcome:
    registration = sample_registration(project_root)
    return ScheduleOutcome(
        status=status,
        project_root=project_root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        backend_label="systemd user timer",
        log_path=project_root / "scheduler-state" / "runs.log",
        registration=registration,
        detail=detail,
    )


def raise_error(error: Exception) -> None:
    raise error


def test_schedule_enable_output_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    registration = sample_registration(root, hour=9, minute=0)
    outcome = ScheduleOutcome(
        status=ScheduleStatus.ENABLED,
        project_root=root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        backend_label="systemd user timer",
        log_path=tmp_path / "state" / "runs.log",
        registration=registration,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "enable_schedule", lambda root, at="09:00": outcome)

    result = runner.invoke(cli.app, ["schedule", "enable"])

    assert result.exit_code == 0
    assert result.stdout == (
        "QuaLock Release Schedule\n\n"
        "ENABLED\n\n"
        f"Project: {root}\n"
        "Runs: every day at 09:00 local time\n"
        "Backend: systemd user timer\n"
        f"Logs: {outcome.log_path}\n\n"
        "The scheduled job only runs `qualock monitor`.\n"
        "It does not update Codex or change your baseline.\n"
    )


def test_schedule_enable_forwards_exact_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, str]] = []
    outcome = sample_outcome(ScheduleStatus.ENABLED, project_root=tmp_path.resolve())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "enable_schedule",
        lambda root, at="09:00": seen.append((root, at)) or outcome,
    )

    result = runner.invoke(cli.app, ["schedule", "enable", "--at", "08:30"])

    assert result.exit_code == 0
    assert seen == [(tmp_path.resolve(), "08:30")]


def test_schedule_status_output_with_registration_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    outcome = sample_outcome(ScheduleStatus.ENABLED, project_root=root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "schedule_status", lambda root: outcome)

    result = runner.invoke(cli.app, ["schedule", "status"])

    assert result.exit_code == 0
    assert result.stdout == (
        "QuaLock Release Schedule\n\n"
        "ENABLED\n\n"
        f"Project: {root}\n"
        "Daily time: 09:00 local time\n"
        "Backend: systemd user timer\n"
        f"Python: {outcome.registration.python_executable}\n"
        f"Logs: {outcome.log_path}\n"
    )


def test_schedule_status_renders_dynamic_text_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project[root]"
    project.mkdir()
    outcome = sample_outcome(
        ScheduleStatus.NEEDS_REPAIR,
        project_root=project.resolve(),
        detail="native [danger] drift",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "schedule_status", lambda root: outcome)

    result = runner.invoke(cli.app, ["schedule", "status"])

    assert result.exit_code == 4
    assert "project[root]" in result.stdout
    assert "native [danger] drift" in result.stdout
    assert (
        "Run `qualock schedule enable` to repair it or "
        "`qualock schedule disable` to remove it."
    ) in result.stdout


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ScheduleStatus.ENABLED, 0),
        (ScheduleStatus.DISABLED, 0),
        (ScheduleStatus.NEEDS_REPAIR, 4),
    ],
)
def test_schedule_status_exit_mapping(
    status: ScheduleStatus,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = sample_outcome(status, project_root=tmp_path.resolve())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "schedule_status", lambda root: outcome)

    assert runner.invoke(cli.app, ["schedule", "status"]).exit_code == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SchedulerOperationalError("install failed"), 1),
        (SchedulerUnsupportedError("no user scheduler"), 3),
        (ConfigError("bad config"), 3),
        (CanaryLoadError("bad canary"), 3),
        (CommandError("release monitor supports only a Codex baseline"), 3),
        (FileNotFoundError("baseline.lock"), 3),
        (ValidationError.from_exception_data("BaselineLock", []), 3),
        (ValueError("daily time must use HH:MM"), 3),
        (BaselineStaleError("suite changed"), 4),
        (RuntimeError("unexpected"), 1),
    ],
)
def test_schedule_enable_exit_mapping(
    error: Exception,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "enable_schedule",
        lambda root, at="09:00": raise_error(error),
    )

    result = runner.invoke(cli.app, ["schedule", "enable"])

    assert result.exit_code == expected
    assert str(error) in result.stdout


@pytest.mark.parametrize(
    ("command", "error", "expected"),
    [
        ("status", SchedulerOperationalError("inspect failed"), 1),
        ("status", SchedulerUnsupportedError("unsupported"), 3),
        ("status", RuntimeError("unexpected"), 1),
        ("disable", SchedulerOperationalError("remove failed"), 1),
        ("disable", SchedulerUnsupportedError("unsupported"), 3),
        ("disable", RuntimeError("unexpected"), 1),
    ],
)
def test_schedule_status_and_disable_error_mapping(
    command: str,
    error: Exception,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    target = "schedule_status" if command == "status" else "disable_schedule"
    monkeypatch.setattr(cli, target, lambda root: raise_error(error))

    result = runner.invoke(cli.app, ["schedule", command])

    assert result.exit_code == expected
    assert result.stdout == f"{error}\n"


def test_schedule_disable_renders_disabled_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    outcome = ScheduleOutcome(
        status=ScheduleStatus.DISABLED,
        project_root=root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        backend_label="systemd user timer",
        log_path=root / "scheduler-state" / "runs.log",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "disable_schedule", lambda root: outcome)

    result = runner.invoke(cli.app, ["schedule", "disable"])

    assert result.exit_code == 0
    assert "DISABLED" in result.stdout
    assert "Daily time:" not in result.stdout
    assert "Python:" not in result.stdout


def test_uncertain_rollback_warning_is_printed_once_and_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warning = (
        "schedule enable failed: install failed; native schedule may still be enabled; "
        "run `qualock schedule status` and `qualock schedule disable`"
    )
    error = SchedulerOperationalError(warning, rollback_uncertain=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "enable_schedule", lambda root, at="09:00": raise_error(error))

    result = runner.invoke(cli.app, ["schedule", "enable"])

    assert result.exit_code == 1
    assert result.stdout == f"{warning}\n"


@pytest.mark.parametrize(
    "args",
    [
        ["schedule", "run"],
        ["schedule", "enable", "--backend", "systemd_user"],
        ["schedule", "enable", "--cron", "0 9 * * *"],
        ["schedule", "enable", "--weekdays"],
        ["schedule", "enable", "--interval", "daily"],
        ["schedule", "enable", "--timezone", "UTC"],
        ["schedule", "enable", "--schedule-time", "09:00"],
        ["schedule", "status", "--at", "09:00"],
        ["schedule", "status", "--schedule-time", "09:00"],
        ["schedule", "disable", "--at", "09:00"],
        ["schedule", "disable", "--schedule-time", "09:00"],
    ],
)
def test_schedule_rejects_non_public_commands_and_options(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("scheduler orchestration was called before CLI parse rejection")

    monkeypatch.setattr(cli, "enable_schedule", fail_if_called)
    monkeypatch.setattr(cli, "schedule_status", fail_if_called)
    monkeypatch.setattr(cli, "disable_schedule", fail_if_called)

    assert runner.invoke(cli.app, args, catch_exceptions=False).exit_code != 0


def test_schedule_help_exposes_exactly_three_commands() -> None:
    result = runner.invoke(cli.app, ["schedule", "--help"])

    assert {command.name for command in cli.schedule_app.registered_commands} == {
        "enable",
        "status",
        "disable",
    }
    assert result.exit_code == 0
    assert "enable" in result.stdout
    assert "status" in result.stdout
    assert "disable" in result.stdout
    assert "run" not in result.stdout
