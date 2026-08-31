from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AgentSupportBinary:
    name: str
    path: Path
    sha256: str
    container_path: str


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


class AgentAdapter(Protocol):
    def detect_capabilities(self, binary: Path) -> AgentCapabilities: ...
