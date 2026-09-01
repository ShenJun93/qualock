from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from qualock.config.models import ProjectProtectionConfig


class ProtectionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"


class ProtectionRun(BaseModel):
    id: str
    name: str
    command: list[str]
    timeout_seconds: int
    status: ProtectionStatus
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(ge=0)
    error: str | None = None


class ProjectLock(BaseModel):
    schema_version: Literal[1] = 1
    created_at: str
    git_head: str
    git_dirty: bool
    protections: list[ProjectProtectionConfig]
    baseline: list[ProtectionRun]


class SignedProjectLock(BaseModel):
    schema_version: Literal[2] = 2
    lock: ProjectLock
    hmac_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectProtectResult(BaseModel):
    operation_id: str
    created_at: str
    status: ProtectionStatus
    git_head: str
    git_dirty: bool
    runs: list[ProtectionRun]
    lock_created: bool


class ProjectVerifyResult(BaseModel):
    operation_id: str
    created_at: str
    status: ProtectionStatus
    baseline_git_head: str
    baseline_git_dirty: bool
    current_git_head: str
    current_git_dirty: bool
    runs: list[ProtectionRun]
