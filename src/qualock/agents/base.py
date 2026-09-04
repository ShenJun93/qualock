from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from qualock.evidence.models import AgentEvidence


@dataclass(frozen=True)
class AgentSupportBinary:
    name: str
    path: Path
    sha256: str
    container_path: str


@dataclass(frozen=True)
class AgentRuntimeDependency:
    command: str
    apt_package: str


@dataclass(frozen=True)
class AgentBinary:
    name: str
    version: str
    path: Path
    sha256: str
    support_binaries: tuple[AgentSupportBinary, ...] = ()


@dataclass(frozen=True)
class AgentCapabilities:
    exec: bool = False
    json: bool = False
    ephemeral: bool = False
    ignore_user_config: bool = False
    ignore_rules: bool = False
    workspace_write: bool = False
    model: bool = False

    @property
    def common_contract(self) -> bool:
        return all(
            (
                self.exec,
                self.json,
                self.ephemeral,
                self.ignore_user_config,
                self.ignore_rules,
                self.workspace_write,
                self.model,
            )
        )


@dataclass(frozen=True)
class AgentMount:
    host_path: Path
    container_path: str
    mode: Literal["ro", "rw"]


@dataclass(frozen=True)
class AgentInvocation:
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    mounts: tuple[AgentMount, ...] = ()
    tmpfs_mounts: tuple[str, ...] = ()
    bootstrap_copy: tuple[str, str] | None = None
    stdin_secret_env: tuple[str, str] | None = field(default=None, repr=False)
    container_binary_path: str = "/opt/qualock/agent"


class AgentAdapter(Protocol):
    @property
    def runtime_dependencies(self) -> tuple[AgentRuntimeDependency, ...]: ...

    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> AbstractContextManager[AgentInvocation]: ...

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence: ...
