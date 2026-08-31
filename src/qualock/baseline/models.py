from typing import Literal

from pydantic import BaseModel, Field


class AgentPin(BaseModel):
    name: str
    version: str
    binary_sha256: str


class ModelPin(BaseModel):
    id: str
    snapshot: str | None = None
    reasoning_effort: str


class CanaryStability(BaseModel):
    valid_runs: int = Field(ge=0)
    successes: int = Field(ge=0)


class BaselineLock(BaseModel):
    schema_version: Literal[1]
    created_at: str
    agent: AgentPin
    model: ModelPin
    qualock_version: str
    suite_sha256: str
    config_sha256: str
    canaries: dict[str, CanaryStability]
