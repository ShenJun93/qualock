from typing import Literal

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    name: Literal["codex"] = "codex"


class ModelConfig(BaseModel):
    id: str = "gpt-5.6-terra"
    snapshot: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "high"

    @property
    def effective_model(self) -> str:
        return self.snapshot or self.id


class QualificationConfig(BaseModel):
    repetitions: int = Field(default=3, ge=1)


class IntegrityConfig(BaseModel):
    reject_web_search: bool = True
    reject_mcp_calls: bool = True
    reject_protected_path_changes: bool = True


class QualockConfig(BaseModel):
    schema_version: Literal[1] = 1
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    qualification: QualificationConfig = Field(default_factory=QualificationConfig)
    integrity: IntegrityConfig = Field(default_factory=IntegrityConfig)
    canary_globs: list[str] = Field(default_factory=lambda: [".qualock/canaries/*.yaml"])
