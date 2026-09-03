from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from qualock.qualification.models import Verdict


class PrClassification(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    UPGRADE = "upgrade"
    INVALID_SCOPE = "invalid_scope"


class PrReportVerdict(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    INCOMPLETE = "incomplete"


class PrReasonCode(str, Enum):
    INVALID_SCOPE = "invalid_scope"
    INVALID_PROPOSED_LOCK = "invalid_proposed_lock"
    PROPOSED_LOCK_UNAVAILABLE = "proposed_lock_unavailable"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    TRUSTED_BASELINE_STALE = "trusted_baseline_stale"
    QUALIFICATION_FAILED = "qualification_failed"
    INSUFFICIENT_VALID_ATTEMPTS = "insufficient_valid_attempts"
    UNSTABLE_BASELINE = "unstable_baseline"
    QUALITY_REGRESSION = "quality_regression"
    CRITICAL_REGRESSION = "critical_regression"


_SHA_PATTERN = r"^[0-9a-f]{40}$"
_REPOSITORY_PATTERN = r"^[^/\s]+/[^/\s]+$"
_MAX_CHANGED_PATHS = 3000
_MAX_CHANGED_PATH_LENGTH = 4096
_ChangedPath = Annotated[str, Field(max_length=_MAX_CHANGED_PATH_LENGTH)]


class PrCanarySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    canary_id: str = Field(min_length=1, max_length=256)
    baseline_successes: int = Field(ge=0)
    baseline_valid: int = Field(ge=0)
    candidate_successes: int = Field(ge=0)
    candidate_valid: int = Field(ge=0)
    verdict: Verdict


class PullRequestContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    repository_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=_REPOSITORY_PATTERN, max_length=256)
    pr_number: int = Field(gt=0)
    pr_author_login: str = Field(min_length=1, max_length=100)
    base_sha: str = Field(pattern=_SHA_PATTERN)
    head_sha: str = Field(pattern=_SHA_PATTERN)
    producer_run_id: int = Field(gt=0)
    changed_paths: Annotated[tuple[_ChangedPath, ...], Field(max_length=_MAX_CHANGED_PATHS)]
    classification: PrClassification


class PullRequestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    repository_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=_REPOSITORY_PATTERN, max_length=256)
    pr_number: int = Field(gt=0)
    base_sha: str = Field(pattern=_SHA_PATTERN)
    head_sha: str = Field(pattern=_SHA_PATTERN)
    producer_run_id: int = Field(gt=0)
    classification: PrClassification
    baseline_version: str | None = None
    candidate_version: str | None = None
    qualification_id: str | None = None
    qualock_version: str
    verdict: PrReportVerdict
    canaries: tuple[PrCanarySummary, ...] = ()
    reason_codes: tuple[PrReasonCode, ...] = ()
    credential_unavailable: bool = False
    qualification_completed: bool = False
