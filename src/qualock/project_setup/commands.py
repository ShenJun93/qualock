from __future__ import annotations

from pathlib import Path

from qualock.project_protection.commands import execute_protect
from qualock.project_protection.models import ProjectProtectResult

from .config import ensure_qualock_project, write_protections
from .detect import detect_project
from .models import ProtectionLevel, SetupPlan
from .packs import recommend_protections


class SetupUnsupportedError(ValueError):
    pass


def build_setup_plan(root: Path, level: ProtectionLevel) -> SetupPlan:
    capabilities = detect_project(root)
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
