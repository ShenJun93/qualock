from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock.cli import app
from qualock.project_protection.models import ProjectVerifyResult, ProtectionStatus
from qualock.project_protection.runner import ProjectProtectionError
from qualock.project_protection.signing import ProjectLockIntegrityError
from qualock.project_watch.control import WatchControlChangedError
from qualock.project_watch.models import WatchEvent, WatchEventKind, WatchOutcome
from qualock.project_watch.render import render_watch_event
from qualock.project_watch.snapshot import ProjectWatchSnapshotError

runner = CliRunner()


def _result(status: ProtectionStatus, operation_id: str = "verify-watch") -> ProjectVerifyResult:
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


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (WatchEventKind.CONTROL_VERIFIED, "Signed protection lock verified."),
        (WatchEventKind.CHECKING, "Checking protected behavior..."),
        (WatchEventKind.WATCHING, "Watching for changes..."),
        (WatchEventKind.CHANGED, "Changes detected..."),
        (WatchEventKind.SETTLING, "Waiting for edits to settle..."),
        (
            WatchEventKind.STALE,
            "Project changed while QuaLock was checking; checking again after edits settle.",
        ),
        (WatchEventKind.INSTABILITY_INCOMPLETE, "CHECK COULD NOT FINISH"),
    ],
)
def test_render_watch_event_has_plain_language_message(kind: WatchEventKind, text: str) -> None:
    assert text in render_watch_event(WatchEvent(kind=kind))


def test_render_watch_event_falls_back_if_internal_message_mapping_is_missing(monkeypatch) -> None:
    import qualock.project_watch.render as watch_render

    monkeypatch.delitem(watch_render._MESSAGES, WatchEventKind.CHANGED)

    assert watch_render.render_watch_event(WatchEvent(WatchEventKind.CHANGED)) == "Watch state changed.\n"


def test_watch_cli_renders_authoritative_result_and_pass_exit(tmp_path: Path, monkeypatch) -> None:
    result = _result(ProtectionStatus.PASS)

    def fake_watch(root: Path, **kwargs):
        emit = kwargs["on_event"]
        emit(WatchEvent(WatchEventKind.CONTROL_VERIFIED))
        emit(WatchEvent(WatchEventKind.CHECKING))
        emit(WatchEvent(WatchEventKind.RESULT, result))
        emit(WatchEvent(WatchEventKind.WATCHING))
        return WatchOutcome(last_result=result, exit_status=ProtectionStatus.PASS, interrupted=True)

    monkeypatch.setattr("qualock.cli.run_project_watch", fake_watch)
    monkeypatch.chdir(tmp_path)

    cli_result = runner.invoke(app, ["watch"])

    assert cli_result.exit_code == 0
    assert "QuaLock Watch" in cli_result.stdout
    assert "Signed protection lock verified." in cli_result.stdout
    assert "SAFE TO KEEP" in cli_result.stdout
    assert "Technical evidence: .qualock/results/verify-watch/" in cli_result.stdout
    assert "Watching for changes..." in cli_result.stdout


@pytest.mark.parametrize(
    ("status", "exit_code", "expected_text"),
    [
        (ProtectionStatus.FAIL, 2, "DON'T KEEP THIS CHANGE"),
        (ProtectionStatus.INCOMPLETE, 4, "CHECK COULD NOT FINISH"),
    ],
)
def test_watch_cli_renders_authoritative_non_pass_result(
    tmp_path: Path,
    monkeypatch,
    status: ProtectionStatus,
    exit_code: int,
    expected_text: str,
) -> None:
    result = _result(status)

    def fake_watch(root: Path, **kwargs):
        kwargs["on_event"](WatchEvent(WatchEventKind.RESULT, result))
        return WatchOutcome(last_result=result, exit_status=status, interrupted=True)

    monkeypatch.setattr("qualock.cli.run_project_watch", fake_watch)
    monkeypatch.chdir(tmp_path)

    cli_result = runner.invoke(app, ["watch"])

    assert cli_result.exit_code == exit_code
    assert expected_text in cli_result.stdout
    assert "SAFE TO KEEP" not in cli_result.stdout


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (ProtectionStatus.FAIL, 2),
        (ProtectionStatus.INCOMPLETE, 4),
        (None, 4),
    ],
)
def test_watch_cli_maps_last_authoritative_status_to_exit(
    tmp_path: Path,
    monkeypatch,
    status: ProtectionStatus | None,
    exit_code: int,
) -> None:
    monkeypatch.setattr(
        "qualock.cli.run_project_watch",
        lambda *args, **kwargs: WatchOutcome(last_result=None, exit_status=status, interrupted=True),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["watch"])

    assert result.exit_code == exit_code


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (FileNotFoundError(".qualock/project.lock"), 3),
        (ProjectLockIntegrityError("bad signature"), 4),
        (WatchControlChangedError("restart qualock watch"), 4),
        (ProjectWatchSnapshotError("git discovery failed"), 1),
        (ProjectProtectionError("unable to inspect Git state"), 1),
    ],
)
def test_watch_cli_maps_fatal_errors(tmp_path: Path, monkeypatch, error: Exception, exit_code: int) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("qualock.cli.run_project_watch", fail)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["watch"])

    assert result.exit_code == exit_code
    assert str(error) in result.stdout


def test_watch_cli_never_renders_stale_result_without_result_event(tmp_path: Path, monkeypatch) -> None:
    stale = _result(ProtectionStatus.PASS, "stale-result")

    def fake_watch(root: Path, **kwargs):
        emit = kwargs["on_event"]
        emit(WatchEvent(WatchEventKind.STALE, stale))
        return WatchOutcome(last_result=None, exit_status=ProtectionStatus.INCOMPLETE, interrupted=True)

    monkeypatch.setattr("qualock.cli.run_project_watch", fake_watch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["watch"])

    assert result.exit_code == 4
    assert "Project changed while QuaLock was checking" in result.stdout
    assert "SAFE TO KEEP" not in result.stdout
    assert "stale-result" not in result.stdout
