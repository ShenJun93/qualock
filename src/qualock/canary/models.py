from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MaterialDimensionName = Literal[
    "AGENT_BINARY",
    "AGENT_SUPPORT",
    "MODEL_DECLARATION",
    "PROJECT_CONFIG",
    "PREPARED_TARGET",
    "RUNTIME_PROFILE",
    "PREPARATION_PROFILE",
    "ISOLATION_PROFILE",
    "RESOURCE_POLICY",
]


class RepositorySpec(BaseModel):
    url: str = Field(min_length=1)
    base_sha: str = Field(min_length=7)


class RuntimeSpec(BaseModel):
    execution: Literal["container", "linux-host"] = "container"
    image: str | None = None

    @model_validator(mode="after")
    def validate_runtime(self) -> "RuntimeSpec":
        if self.execution == "container" and not self.image:
            raise ValueError("container runtime requires image")
        if self.execution == "linux-host" and self.image is not None:
            raise ValueError("linux-host runtime must not set image")
        return self


class AgentLimits(BaseModel):
    timeout_seconds: int = Field(gt=0)


class GraderSpec(BaseModel):
    patch: Path
    command: list[str] = Field(min_length=1)


class ConstraintSpec(BaseModel):
    protected_paths: list[str] = Field(default_factory=list)


class PairedChangeCanarySpec(BaseModel):
    material_dimensions: tuple[MaterialDimensionName, ...] = Field(min_length=1)
    max_pair_gap_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_unique_material_dimensions(self) -> "PairedChangeCanarySpec":
        if len(set(self.material_dimensions)) != len(self.material_dimensions):
            raise ValueError("paired_change material_dimensions must be unique")
        return self


class CanarySpec(BaseModel):
    schema_version: Literal[1]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repository: RepositorySpec
    runtime: RuntimeSpec
    task: str = Field(min_length=1)
    setup: list[str] = Field(default_factory=list)
    agent: AgentLimits
    grader: GraderSpec
    constraints: ConstraintSpec = Field(default_factory=ConstraintSpec)
    critical: bool = False
    paired_change: PairedChangeCanarySpec | None = None
