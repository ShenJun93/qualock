from __future__ import annotations

import os
from pathlib import Path

import pytest

from qualock.config.models import ProjectProtectionConfig
from qualock.project_protection.models import ProjectProtectResult, ProtectionStatus
from qualock.project_setup.models import (
    EnvironmentReadiness,
    ProjectCapabilities,
    ProtectionLevel,
    ReadinessStatus,
    SetupPlan,
)
from qualock.project_start.commands import (
    StartStateChangedError,
    apply_start_bootstrap,
)
from qualock.project_start.models import StartPlan, StartProjectState


def protect_result(
    status: ProtectionStatus = ProtectionStatus.PASS,
    *,
    lock_created: bool = True,
) -> ProjectProtectResult:
    return ProjectProtectResult(
        operation_id="protect-test",
        created_at="2026-09-02T00:00:00+00:00",
        status=status,
        git_head="a" * 40,
        git_dirty=False,
        runs=[],
        lock_created=lock_created,
    )


def setup_plan() -> SetupPlan:
    return SetupPlan(
        capabilities=ProjectCapabilities(git=True),
        level=ProtectionLevel.RECOMMENDED,
        protections=(),
        readiness=EnvironmentReadiness(status=ReadinessStatus.READY),
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
    )


def unconfigured_plan() -> StartPlan:
    return StartPlan(
        state=StartProjectState.UNCONFIGURED,
        level=ProtectionLevel.RECOMMENDED,
        setup_plan=setup_plan(),
    )


def fail_if_called(*args, **kwargs):
    raise AssertionError("unexpected call")


def test_configured_unlocked_rechecks_lock_before_protect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "qualock.project_start.commands.assert_bootstrap_lock_absent",
        lambda root: calls.append("guard"),
    )
    monkeypatch.setattr(
        "qualock.project_start.commands.execute_protect",
        lambda root, key_path=None: calls.append("protect") or protect_result(),
    )

    result = apply_start_bootstrap(tmp_path, configured_plan())

    assert calls == ["guard", "protect"]
    assert result.bootstrap_performed is True
    assert result.protect_result is not None
    assert result.protect_result.status is ProtectionStatus.PASS


def test_unconfigured_rechecks_lock_before_apply_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "qualock.project_start.commands.assert_bootstrap_lock_absent",
        lambda root: calls.append("guard"),
    )
    monkeypatch.setattr(
        "qualock.project_start.commands.apply_setup_plan",
        lambda root, plan, key_path=None: calls.append("setup") or protect_result(),
    )

    result = apply_start_bootstrap(tmp_path, unconfigured_plan())

    assert calls == ["guard", "setup"]
    assert result.bootstrap_performed is True
    assert result.protect_result is not None


def test_locked_plan_does_no_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qualock.project_start.commands.execute_protect", fail_if_called)
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", fail_if_called)
    monkeypatch.setattr(
        "qualock.project_start.commands.assert_bootstrap_lock_absent",
        fail_if_called,
    )

    result = apply_start_bootstrap(tmp_path, locked_plan())

    assert result.bootstrap_performed is False
    assert result.protect_result is None


@pytest.mark.parametrize(
    ("status", "lock_created"),
    [
        (ProtectionStatus.FAIL, False),
        (ProtectionStatus.INCOMPLETE, False),
    ],
)
def test_bootstrap_preserves_nonpass_result_for_cli_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: ProtectionStatus,
    lock_created: bool,
) -> None:
    monkeypatch.setattr(
        "qualock.project_start.commands.assert_bootstrap_lock_absent",
        lambda root: None,
    )
    monkeypatch.setattr(
        "qualock.project_start.commands.execute_protect",
        lambda root, key_path=None: protect_result(status, lock_created=lock_created),
    )

    result = apply_start_bootstrap(tmp_path, configured_plan())

    assert result.protect_result is not None
    assert result.protect_result.status is status
    assert result.protect_result.lock_created is lock_created


@pytest.mark.parametrize("entry_kind", ["file", "directory", "dangling_symlink"])
def test_new_lock_entry_aborts_before_bootstrap_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    lock_path = qdir / "project.lock"
    if entry_kind == "file":
        lock_path.write_text("new control", encoding="utf-8")
    elif entry_kind == "directory":
        lock_path.mkdir()
    else:
        if not hasattr(os, "symlink"):
            pytest.skip("symlink support required")
        lock_path.symlink_to(qdir / "missing-target")
    monkeypatch.setattr("qualock.project_start.commands.execute_protect", fail_if_called)
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", fail_if_called)

    with pytest.raises(StartStateChangedError, match="state changed"):
        apply_start_bootstrap(tmp_path, configured_plan())

    lock_path.lstat()


def test_unconfigured_plan_requires_setup_plan(tmp_path: Path) -> None:
    plan = StartPlan(
        state=StartProjectState.UNCONFIGURED,
        level=ProtectionLevel.RECOMMENDED,
        setup_plan=None,
    )

    with pytest.raises(ValueError, match="missing setup plan"):
        apply_start_bootstrap(tmp_path, plan)


@pytest.mark.parametrize("parent_kind", ["file", "dangling_symlink", "directory_symlink"])
def test_bootstrap_rejects_changed_qualock_parent_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_kind: str,
) -> None:
    qdir = tmp_path / ".qualock"
    external = tmp_path / "external-qualock"
    if parent_kind == "file":
        qdir.write_text("changed parent", encoding="utf-8")
    else:
        if not hasattr(os, "symlink"):
            pytest.skip("symlink support required")
        if parent_kind == "directory_symlink":
            external.mkdir()
            qdir.symlink_to(external, target_is_directory=True)
        else:
            qdir.symlink_to(tmp_path / "missing-qualock", target_is_directory=True)
    monkeypatch.setattr("qualock.project_start.commands.execute_protect", fail_if_called)
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", fail_if_called)

    with pytest.raises(StartStateChangedError, match="state changed"):
        apply_start_bootstrap(tmp_path, configured_plan())

    qdir.lstat()
    if parent_kind == "directory_symlink":
        assert list(external.iterdir()) == []


def _manual_protection(name: str) -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id="tests",
        name=name,
        command=["python", "-m", "pytest", "-q"],
        timeout_seconds=120,
    )


def _write_manual_config(root: Path, name: str) -> None:
    qdir = root / ".qualock"
    qdir.mkdir(exist_ok=True)
    (qdir / "config.yaml").write_text(
        "schema_version: 1\n"
        "protections:\n"
        "  - id: tests\n"
        f"    name: {name}\n"
        "    command: [python, -m, pytest, -q]\n"
        "    timeout_seconds: 120\n",
        encoding="utf-8",
    )


def test_configured_bootstrap_rejects_protection_change_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = StartPlan(
        state=StartProjectState.CONFIGURED_UNLOCKED,
        level=ProtectionLevel.RECOMMENDED,
        configured_protections=(_manual_protection("Approved check"),),
    )
    _write_manual_config(tmp_path, "Changed check")
    monkeypatch.setattr("qualock.project_start.commands.execute_protect", fail_if_called)

    with pytest.raises(StartStateChangedError, match="state changed"):
        apply_start_bootstrap(tmp_path, plan)


def test_unconfigured_bootstrap_rejects_new_manual_protections_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_manual_config(tmp_path, "New manual check")
    monkeypatch.setattr("qualock.project_start.commands.apply_setup_plan", fail_if_called)

    with pytest.raises(StartStateChangedError, match="state changed"):
        apply_start_bootstrap(tmp_path, unconfigured_plan())

    assert "New manual check" in (tmp_path / ".qualock/config.yaml").read_text(encoding="utf-8")
