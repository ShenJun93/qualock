from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RepositorySpec(BaseModel):
    url: str = Field(min_length=1)
    base_sha: str = Field(min_length=7)


class RuntimeSpec(BaseModel):
    image: str = Field(min_length=1)


class AgentLimits(BaseModel):
    timeout_seconds: int = Field(gt=0)


class GraderSpec(BaseModel):
    patch: Path
    command: list[str] = Field(min_length=1)


class ConstraintSpec(BaseModel):
    protected_paths: list[str] = Field(default_factory=list)


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
