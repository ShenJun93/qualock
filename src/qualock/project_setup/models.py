from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict

from qualock.config.models import ProjectProtectionConfig


class ProtectionLevel(str, Enum):
    MINIMAL = "minimal"
    RECOMMENDED = "recommended"
    STRONG = "strong"


class ProjectCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    git: bool = False
    python: bool = False
    pytest: bool = False
    node: bool = False
    react: bool = False
    vite: bool = False
    npm_scripts: tuple[str, ...] = ()
    python_targets: tuple[str, ...] = ()
    python_executable: str | None = None

    @property
    def supported(self) -> bool:
        return self.git or self.python or self.node

    @property
    def labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        for enabled, label in (
            (self.python, "Python"),
            (self.pytest, "pytest"),
            (self.node, "Node/npm"),
            (self.react, "React"),
            (self.vite, "Vite"),
            (self.git, "Git"),
        ):
            if enabled:
                labels.append(label)
        return tuple(labels)


@dataclass(frozen=True)
class SetupPlan:
    capabilities: ProjectCapabilities
    level: ProtectionLevel
    protections: tuple[ProjectProtectionConfig, ...]
