from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from qualock.config.io import ConfigError
from qualock.project_setup.models import (
    EnvironmentReadiness,
    ProjectCapabilities,
    ProtectionLevel,
    ReadinessStatus,
    SetupPlan,
)
from qualock.project_start.commands import (
    StartStateChangedError,
    StartStateError,
    assert_bootstrap_lock_absent,
    prepare_start,
)
from qualock.project_start.models import StartProjectState


def fake_setup_plan(level: ProtectionLevel = ProtectionLevel.RECOMMENDED) -> SetupPlan:
    return SetupPlan(
        capabilities=ProjectCapabilities(git=True),
        level=level,
        protections=(),
        readiness=EnvironmentReadiness(status=ReadinessStatus.READY),
    )


def write_config(root: Path, *, protections: list[dict] | None = None) -> None:
    qdir = root / ".qualock"
    qdir.mkdir(exist_ok=True)
    raw = {
        "schema_version": 1,
        "protections": protections or [],
    }
    (qdir / "config.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )


def protection_raw() -> dict:
    return {
        "id": "tests",
        "name": "Tests still pass",
        "command": ["python", "-m", "pytest", "-q"],
        "timeout_seconds": 120,
    }


def fail_build(*args, **kwargs):
    raise AssertionError("build_setup_plan must not be called")


def test_regular_lock_entry_classifies_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "project.lock").write_bytes(b"not-even-json")
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", fail_build)

    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)

    assert plan.state is StartProjectState.LOCKED
    assert plan.setup_plan is None
    assert plan.configured_protections == ()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support required")
def test_dangling_lock_symlink_classifies_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "project.lock").symlink_to(qdir / "missing-target")
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", fail_build)

    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)

    assert plan.state is StartProjectState.LOCKED


def test_lock_directory_classifies_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".qualock" / "project.lock"
    path.mkdir(parents=True)
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", fail_build)

    assert prepare_start(tmp_path, ProtectionLevel.RECOMMENDED).state is StartProjectState.LOCKED


def test_qualock_parent_file_is_error(tmp_path: Path) -> None:
    (tmp_path / ".qualock").write_text("not a directory", encoding="utf-8")

    with pytest.raises(StartStateError, match="\\.qualock"):
        prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)


def test_existing_protections_classify_configured_unlocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_config(tmp_path, protections=[protection_raw()])
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", fail_build)

    plan = prepare_start(tmp_path, ProtectionLevel.STRONG)

    assert plan.state is StartProjectState.CONFIGURED_UNLOCKED
    assert plan.level is ProtectionLevel.STRONG
    assert tuple(item.id for item in plan.configured_protections) == ("tests",)
    assert plan.setup_plan is None


def test_missing_config_builds_unconfigured_setup_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = fake_setup_plan()
    monkeypatch.setattr(
        "qualock.project_start.commands.build_setup_plan",
        lambda root, level: expected,
    )

    plan = prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)

    assert plan.state is StartProjectState.UNCONFIGURED
    assert plan.setup_plan is expected
    assert plan.configured_protections == ()


def test_valid_config_without_protections_uses_unconfigured_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_config(tmp_path)
    expected = fake_setup_plan(ProtectionLevel.MINIMAL)
    monkeypatch.setattr(
        "qualock.project_start.commands.build_setup_plan",
        lambda root, level: expected,
    )

    plan = prepare_start(tmp_path, ProtectionLevel.MINIMAL)

    assert plan.state is StartProjectState.UNCONFIGURED
    assert plan.setup_plan is expected


def test_malformed_config_does_not_fallback_to_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "config.yaml").write_text("protections: [", encoding="utf-8")
    monkeypatch.setattr("qualock.project_start.commands.build_setup_plan", fail_build)

    with pytest.raises(ConfigError):
        prepare_start(tmp_path, ProtectionLevel.RECOMMENDED)


@pytest.mark.parametrize("entry_kind", ["file", "directory", "dangling_symlink"])
def test_bootstrap_lock_guard_rejects_any_new_directory_entry(
    tmp_path: Path,
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

    with pytest.raises(StartStateChangedError, match="state changed"):
        assert_bootstrap_lock_absent(tmp_path)

    lock_path.lstat()


def test_bootstrap_lock_guard_accepts_absent_entry(tmp_path: Path) -> None:
    assert_bootstrap_lock_absent(tmp_path)
