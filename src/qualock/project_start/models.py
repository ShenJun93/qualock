from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from qualock.config.models import ProjectProtectionConfig
from qualock.project_protection.models import ProjectProtectResult
from qualock.project_setup.models import ProtectionLevel, SetupPlan


class StartProjectState(str, Enum):
    LOCKED = "locked"
    CONFIGURED_UNLOCKED = "configured_unlocked"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class StartPlan:
    state: StartProjectState
    level: ProtectionLevel
    setup_plan: SetupPlan | None = None
    configured_protections: tuple[ProjectProtectionConfig, ...] = ()


@dataclass(frozen=True)
class StartBootstrapResult:
    protect_result: ProjectProtectResult | None
    bootstrap_performed: bool
