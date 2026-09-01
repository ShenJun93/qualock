from __future__ import annotations

from pathlib import Path

from qualock.project_protection.commands import execute_protect
from qualock.project_protection.models import ProjectProtectResult
from qualock.run.process import run_process

from .config import ensure_qualock_project, write_protections
from .detect import detect_project
from .models import ProtectionLevel, SetupPlan
from .packs import recommend_protections


class SetupUnsupportedError(ValueError):
    pass


def _require_committed_head(root: Path) -> None:
    try:
        result = run_process(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            timeout_seconds=5,
        )
    except OSError as exc:
        raise SetupUnsupportedError("QuaLock setup could not inspect Git HEAD.") from exc
    if result.timed_out or result.exit_code != 0:
        raise SetupUnsupportedError(
            "QuaLock project protection requires a Git repository with a committed HEAD."
        )


def build_setup_plan(root: Path, level: ProtectionLevel) -> SetupPlan:
    capabilities = detect_project(root)
    if not capabilities.supported:
        raise SetupUnsupportedError(
            "QuaLock could not detect a supported project with a protection check."
        )
    if not capabilities.git:
        raise SetupUnsupportedError("QuaLock project protection requires a Git repository.")
    _require_committed_head(root)
    protections = recommend_protections(capabilities, level)
    if not protections:
        raise SetupUnsupportedError(
            "QuaLock could not detect a supported project with a protection check."
        )
    return SetupPlan(
        capabilities=capabilities,
        level=level,
        protections=protections,
    )


def apply_setup_plan(
    root: Path,
    plan: SetupPlan,
    *,
    key_path: Path | None = None,
) -> ProjectProtectResult:
    config_path = ensure_qualock_project(root)
    write_protections(config_path, plan.protections)
    return execute_protect(root, key_path=key_path)
