from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock import cli
from qualock.config.io import ConfigError
from qualock.config.models import ProjectProtectionConfig
from qualock.project_protection.commands import ProjectProtectionConfigError
from qualock.project_protection.models import ProjectProtectResult, ProtectionStatus
from qualock.project_protection.runner import ProjectProtectionError
from qualock.project_protection.signing import ProjectLockIntegrityError
from qualock.project_setup.commands import SetupReadinessError, SetupUnsupportedError
from qualock.project_setup.models import (
    EnvironmentReadiness,
    ProjectCapabilities,
    ProtectionLevel,
    ReadinessStatus,
    SetupPlan,
)
from qualock.project_start.commands import StartStateChangedError
from qualock.project_start.models import (
    StartBootstrapResult,
    StartPlan,
    StartProjectState,
)
from qualock.project_watch.control import WatchControlChangedError
from qualock.project_watch.models import WatchOutcome
from qualock.project_watch.snapshot import ProjectWatchSnapshotError

runner = CliRunner()


def protection() -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id="tests",
        name="Tests [literal] still pass",
        command=["python", "-m", "pytest", "-q"],
        timeout_seconds=120,
    )


def protect_result(
    status: ProtectionStatus = ProtectionStatus.PASS,
    *,
    lock_created: bool = True,
) -> ProjectProtectResult:
    return ProjectProtectResult(
        operation_id="protect-start",
        created_at="2026-09-02T00:00:00+00:00",
        status=status,
        git_head="a" * 40,
        git_dirty=False,
        runs=[],
        lock_created=lock_created,
    )


def watch_outcome(status: ProtectionStatus | None) -> WatchOutcome:
    return WatchOutcome(last_result=None, exit_status=status, interrupted=True)


def setup_plan(status: ReadinessStatus = ReadinessStatus.READY) -> SetupPlan:
    return SetupPlan(
        capabilities=ProjectCapabilities(git=True),
        level=ProtectionLevel.RECOMMENDED,
        protections=(protection(),),
        readiness=EnvironmentReadiness(status=status),
    )


def locked_plan() -> StartPlan:
    return StartPlan(
        state=StartProjectState.LOCKED,
        level=ProtectionLevel.RECOMMENDED,
    )


def configured_plan() -> StartPlan:
    return StartPlan(
        state=StartProjectState.CONFIGURED_UNLOCKED,
        level=ProtectionLevel.RECOMMENDED,
        configured_protections=(protection(),),
    )


def unconfigured_plan(status: ReadinessStatus = ReadinessStatus.READY) -> StartPlan:
    return StartPlan(
        state=StartProjectState.UNCONFIGURED,
        level=ProtectionLevel.RECOMMENDED,
        setup_plan=setup_plan(status),
    )


def patch_start(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: StartPlan,
    bootstrap: StartBootstrapResult | None = None,
    watch_status: ProtectionStatus | None = ProtectionStatus.PASS,
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(cli, "prepare_start", lambda root, level: plan, raising=False)
    monkeypatch.setattr(
        cli,
        "apply_start_bootstrap",
        lambda root, start_plan: calls.append("bootstrap") or bootstrap,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "run_project_watch",
        lambda root, on_event: calls.append("watch") or watch_outcome(watch_status),
    )
    monkeypatch.setattr(cli, "render_setup_plan", lambda plan: "SETUP PLAN\n")
    return calls


def test_start_locked_enters_watch_without_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=locked_plan())

    result = runner.invoke(cli.app, ["start"])

    assert result.exit_code == 0
    assert calls == ["watch"]
    assert "Protect this state" not in result.stdout
    assert "QuaLock Watch" in result.stdout


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (ProtectionStatus.PASS, 0),
        (ProtectionStatus.FAIL, 2),
        (ProtectionStatus.INCOMPLETE, 4),
        (None, 4),
    ],
)
def test_start_locked_propagates_watch_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: ProtectionStatus | None,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    patch_start(monkeypatch, plan=locked_plan(), watch_status=status)

    result = runner.invoke(cli.app, ["start"])

    assert result.exit_code == expected_exit


def test_start_configured_cancel_is_zero_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=configured_plan())

    result = runner.invoke(cli.app, ["start"], input="n\n")

    assert result.exit_code == 0
    assert calls == []
    assert "Tests [literal] still pass" in result.stdout
    assert "CURRENT state" in result.stdout
    normalized_output = " ".join(result.stdout.split())
    assert "only if every protected check passes" in normalized_output
    assert "Start cancelled. No files changed." in result.stdout


def test_start_configured_yes_bootstraps_then_watches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap = StartBootstrapResult(
        protect_result=protect_result(),
        bootstrap_performed=True,
    )
    calls = patch_start(
        monkeypatch,
        plan=configured_plan(),
        bootstrap=bootstrap,
    )

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 0
    assert calls == ["bootstrap", "watch"]
    assert "PROTECTED" in result.stdout


@pytest.mark.parametrize(
    "status",
    [ProtectionStatus.FAIL, ProtectionStatus.INCOMPLETE],
)
def test_start_configured_nonpass_does_not_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: ProtectionStatus,
) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap = StartBootstrapResult(
        protect_result=protect_result(status, lock_created=False),
        bootstrap_performed=True,
    )
    calls = patch_start(
        monkeypatch,
        plan=configured_plan(),
        bootstrap=bootstrap,
    )

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 4
    assert calls == ["bootstrap"]


def test_start_unconfigured_needs_setup_exits_before_prompt_or_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(
        monkeypatch,
        plan=unconfigured_plan(ReadinessStatus.NEEDS_SETUP),
    )

    result = runner.invoke(cli.app, ["start"])

    assert result.exit_code == 4
    assert calls == []
    assert "SETUP PLAN" in result.stdout


def test_start_unconfigured_ready_cancel_does_not_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=unconfigured_plan())

    result = runner.invoke(cli.app, ["start"], input="n\n")

    assert result.exit_code == 0
    assert calls == []
    assert "Start cancelled. No files changed." in result.stdout


def test_start_unconfigured_yes_bootstraps_then_watches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap = StartBootstrapResult(
        protect_result=protect_result(),
        bootstrap_performed=True,
    )
    calls = patch_start(
        monkeypatch,
        plan=unconfigured_plan(),
        bootstrap=bootstrap,
    )

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 0
    assert calls == ["bootstrap", "watch"]
    assert "SETUP PLAN" in result.stdout


def test_start_requires_lock_created_before_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap = StartBootstrapResult(
        protect_result=protect_result(lock_created=False),
        bootstrap_performed=True,
    )
    calls = patch_start(
        monkeypatch,
        plan=configured_plan(),
        bootstrap=bootstrap,
    )

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 4
    assert calls == ["bootstrap"]


def test_start_bootstrap_pass_does_not_replace_watch_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap = StartBootstrapResult(
        protect_result=protect_result(),
        bootstrap_performed=True,
    )
    calls = patch_start(
        monkeypatch,
        plan=configured_plan(),
        bootstrap=bootstrap,
        watch_status=ProtectionStatus.FAIL,
    )

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 2
    assert calls == ["bootstrap", "watch"]


def test_start_locked_integrity_failure_never_falls_back_to_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=locked_plan())

    def fail_watch(root, on_event):
        raise ProjectLockIntegrityError("signature does not match")

    monkeypatch.setattr(cli, "run_project_watch", fail_watch)

    result = runner.invoke(cli.app, ["start"])

    assert result.exit_code == 4
    assert calls == []
    assert "signature does not match" in result.stdout


def test_start_stale_unlocked_plan_exits_4_without_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=configured_plan())

    def stale(root, start_plan):
        calls.append("bootstrap")
        raise StartStateChangedError("state changed")

    monkeypatch.setattr(cli, "apply_start_bootstrap", stale)

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 4
    assert calls == ["bootstrap"]
    assert "state changed" in result.stdout


def test_start_invalid_preparation_exits_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def invalid(root, level):
        raise ConfigError("invalid Qualock config")

    monkeypatch.setattr(cli, "prepare_start", invalid)

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == 3
    assert "invalid Qualock config" in result.stdout


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (FileNotFoundError(".qualock/project.lock"), 3),
        (WatchControlChangedError("control changed"), 4),
        (ProjectWatchSnapshotError("snapshot failed"), 1),
        (ProjectProtectionError("protection failed"), 1),
        (OSError("watch I/O failed"), 1),
    ],
)
def test_start_locked_maps_existing_watch_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=locked_plan())

    def fail_watch(root, on_event):
        raise error

    monkeypatch.setattr(cli, "run_project_watch", fail_watch)

    result = runner.invoke(cli.app, ["start"])

    assert result.exit_code == expected_exit
    assert calls == []
    assert str(error) in result.stdout


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (SetupReadinessError("environment not ready"), 4),
        (ProjectLockIntegrityError("lock integrity failed"), 4),
        (StartStateChangedError("state changed"), 4),
        (ConfigError("invalid config"), 3),
        (ProjectProtectionConfigError("invalid protection config"), 3),
        (SetupUnsupportedError("unsupported project"), 3),
        (ValueError("invalid input"), 3),
        (ProjectProtectionError("protection failed"), 1),
        (OSError("bootstrap I/O failed"), 1),
    ],
)
def test_start_bootstrap_maps_errors_without_entering_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = patch_start(monkeypatch, plan=configured_plan())

    def fail_bootstrap(root, start_plan):
        calls.append("bootstrap")
        raise error

    monkeypatch.setattr(cli, "apply_start_bootstrap", fail_bootstrap)

    result = runner.invoke(cli.app, ["start", "--yes"])

    assert result.exit_code == expected_exit
    assert calls == ["bootstrap"]
    assert str(error) in result.stdout
