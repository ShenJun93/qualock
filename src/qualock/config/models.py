from typing import Literal

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    name: Literal["codex", "claude", "antigravity", "gemini"] = "codex"


class ModelConfig(BaseModel):
    id: str = "gpt-5.6-terra"
    snapshot: str | None = None
    reasoning_effort: Literal[
        "low", "medium", "high", "xhigh", "provider-default"
    ] = "high"

    @property
    def effective_model(self) -> str:
        return self.snapshot or self.id


class QualificationConfig(BaseModel):
    repetitions: int = Field(default=3, ge=1)


class IntegrityConfig(BaseModel):
    reject_web_search: bool = True
    reject_mcp_calls: bool = True
    reject_protected_path_changes: bool = True


class ProjectProtectionConfig(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=120, gt=0)


class QualockConfig(BaseModel):
    schema_version: Literal[1] = 1
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    qualification: QualificationConfig = Field(default_factory=QualificationConfig)
    integrity: IntegrityConfig = Field(default_factory=IntegrityConfig)
    canary_globs: list[str] = Field(default_factory=lambda: [".qualock/canaries/*.yaml"])
    protections: list[ProjectProtectionConfig] = Field(default_factory=list)


def validate_agent_model_contract(config: QualockConfig) -> None:
    effort = config.model.reasoning_effort
    if config.agent.name == "gemini":
        if effort != "provider-default":
            raise ValueError("Gemini requires reasoning_effort: provider-default")
        if config.model.effective_model == "gpt-5.6-terra":
            raise ValueError("Gemini requires an explicit Gemini CLI model")
    elif effort == "provider-default":
        raise ValueError("provider-default reasoning effort is only supported by Gemini")
