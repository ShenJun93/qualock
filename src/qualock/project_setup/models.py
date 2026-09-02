from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict

from qualock.config.models import ProjectProtectionConfig


class ProtectionLevel(str, Enum):
    MINIMAL = "minimal"
    RECOMMENDED = "recommended"
    STRONG = "strong"


class PythonRunner(str, Enum):
    UV = "uv"
    POETRY = "poetry"
    VENV = "venv"
    NONE = "none"



class ReadinessStatus(str, Enum):
    READY = "ready"
    NEEDS_SETUP = "needs_setup"


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    name: str
    status: ReadinessStatus
    detail: str | None = None
    recommendation: str | None = None


@dataclass(frozen=True)
class EnvironmentReadiness:
    status: ReadinessStatus
    checks: tuple[ReadinessCheck, ...] = ()

class ProjectCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    git: bool = False
    python: bool = False
    pytest: bool = False
    node: bool = False
    react: bool = False
    vite: bool = False
    django: bool = False
    fastapi: bool = False
    nextjs: bool = False
    typescript: bool = False
    npm_scripts: tuple[str, ...] = ()
    python_targets: tuple[str, ...] = ()
    python_runner: PythonRunner = PythonRunner.NONE
    python_environment: str | None = None
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
            (self.python_runner is PythonRunner.UV, "uv"),
            (self.python_runner is PythonRunner.POETRY, "Poetry"),
            (self.python_runner is PythonRunner.VENV, "venv"),
            (self.django, "Django"),
            (self.fastapi, "FastAPI"),
            (self.node, "Node/npm"),
            (self.nextjs, "Next.js"),
            (self.react, "React"),
            (self.vite, "Vite"),
            (self.typescript, "TypeScript"),
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
