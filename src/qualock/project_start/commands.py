from __future__ import annotations

import stat
from pathlib import Path

from qualock.config.io import ConfigError, load_config
from qualock.config.models import ProjectProtectionConfig
from qualock.project import project_dir
from qualock.project_protection.commands import execute_protect
from qualock.project_setup.commands import apply_setup_plan, build_setup_plan
from qualock.project_setup.models import ProtectionLevel

from .models import StartBootstrapResult, StartPlan, StartProjectState


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
    qdir = project_dir(root)
    try:
        mode = qdir.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISDIR(mode):
        raise StartStateChangedError(
            "QuaLock project protection state changed while preparing this session. "
            "Run qualock start again."
        )

    lock_path = qdir / "project.lock"
    if _directory_entry_exists(lock_path):
        raise StartStateChangedError(
            "QuaLock project protection state changed while preparing this session. "
            "Run qualock start again."
        )


def _raise_start_state_changed() -> None:
    raise StartStateChangedError(
        "QuaLock project protection state changed while preparing this session. "
        "Run qualock start again."
    )


def _assert_bootstrap_config_compatible(root: Path, plan: StartPlan) -> None:
    qdir = project_dir(root)
    try:
        mode = qdir.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISDIR(mode):
        _raise_start_state_changed()

    config_path = qdir / "config.yaml"
    current_protections: tuple[ProjectProtectionConfig, ...]
    if not _directory_entry_exists(config_path):
        current_protections = ()
    else:
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            raise StartStateChangedError(
                "QuaLock project protection state changed while preparing this session. "
                "Run qualock start again."
            ) from exc
        current_protections = tuple(config.protections)

    if plan.state is StartProjectState.CONFIGURED_UNLOCKED:
        if current_protections != plan.configured_protections:
            _raise_start_state_changed()
    elif plan.state is StartProjectState.UNCONFIGURED and current_protections:
        _raise_start_state_changed()


def apply_start_bootstrap(
    root: Path,
    plan: StartPlan,
    *,
    key_path: Path | None = None,
) -> StartBootstrapResult:
    if plan.state is StartProjectState.LOCKED:
        return StartBootstrapResult(protect_result=None, bootstrap_performed=False)

    _assert_bootstrap_config_compatible(root, plan)
    assert_bootstrap_lock_absent(root)

    if plan.state is StartProjectState.CONFIGURED_UNLOCKED:
        result = execute_protect(root, key_path=key_path)
    elif plan.state is StartProjectState.UNCONFIGURED:
        if plan.setup_plan is None:
            raise ValueError("unconfigured start plan is missing setup plan")
        result = apply_setup_plan(root, plan.setup_plan, key_path=key_path)
    else:
        raise ValueError(f"unsupported start state: {plan.state}")

    return StartBootstrapResult(
        protect_result=result,
        bootstrap_performed=True,
    )
