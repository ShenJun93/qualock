from __future__ import annotations

import stat
from pathlib import Path

from qualock.config.io import load_config
from qualock.project import project_dir
from qualock.project_setup.commands import build_setup_plan
from qualock.project_setup.models import ProtectionLevel

from .models import StartPlan, StartProjectState


class StartStateError(ValueError):
    pass


class StartStateChangedError(RuntimeError):
    pass


def _directory_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def _require_qualock_directory(root: Path) -> Path:
    qdir = project_dir(root)
    try:
        mode = qdir.lstat().st_mode
    except FileNotFoundError:
        return qdir
    if not stat.S_ISDIR(mode):
        raise StartStateError(
            f"QuaLock project state path is not a directory: {qdir}"
        )
    return qdir


def prepare_start(root: Path, level: ProtectionLevel) -> StartPlan:
    qdir = _require_qualock_directory(root)
    lock_path = qdir / "project.lock"
    if _directory_entry_exists(lock_path):
        return StartPlan(state=StartProjectState.LOCKED, level=level)

    config_path = qdir / "config.yaml"
    if _directory_entry_exists(config_path):
        config = load_config(config_path)
        if config.protections:
            return StartPlan(
                state=StartProjectState.CONFIGURED_UNLOCKED,
                level=level,
                configured_protections=tuple(config.protections),
            )

    setup_plan = build_setup_plan(root, level)
    return StartPlan(
        state=StartProjectState.UNCONFIGURED,
        level=level,
        setup_plan=setup_plan,
    )


def assert_bootstrap_lock_absent(root: Path) -> None:
    lock_path = project_dir(root) / "project.lock"
    if _directory_entry_exists(lock_path):
        raise StartStateChangedError(
            "QuaLock project protection state changed while preparing this session. "
            "Run qualock start again."
        )
