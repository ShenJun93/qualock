from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qualock.change_targeting.models import AssessmentStatus, CoverageAssessmentV0
from qualock.qualification.models import Verdict

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TargetedExecutionError(ValueError):
    pass


def _require_unique_sorted_strings(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    if list(values) != sorted(values):
        raise ValueError(f"{label} must be lexically sorted")


class TargetedQualificationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["targeted"] = "targeted"
    qualification_id: str = Field(min_length=1)
    baseline_version: str
    candidate_version: str
    verdict: Verdict
    run_order: tuple[tuple[str, str, int], ...]
    max_attempts: int | None = None
    max_tokens: int | None = None
    attempts_used: int = 0
    observed_tokens: int | None = None
    selected_sources: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_sources(self) -> "TargetedQualificationV1":
        _require_unique_sorted_strings(self.selected_sources, "selected_sources")
        return self


class TargetedReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["targeted"] = "targeted"
    selected_sources: tuple[str, ...] = Field(min_length=1)
    assessment: CoverageAssessmentV0
    result: dict[str, Any]

    @model_validator(mode="after")
    def _validate_sources(self) -> "TargetedReportV1":
        _require_unique_sorted_strings(self.selected_sources, "selected_sources")
        return self


class TargetedArtifactHashesV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targeted_report_json: Sha256Hex
    targeted_qualification_json: Sha256Hex
    targeted_evidence_provenance_v1_json: Sha256Hex
    targeted_paired_change_run_v1_json: Sha256Hex


class TargetedRunV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    protocol_id: Literal["selected-source-execution/v1"] = "selected-source-execution/v1"
    scope: Literal["targeted"] = "targeted"
    qualification_id: str = Field(min_length=1)
    assessment: CoverageAssessmentV0
    assessment_sha256: Sha256Hex
    project_suite_sha256: Sha256Hex
    selected_suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    baseline_lock_sha256: Sha256Hex
    selected_sources: tuple[str, ...] = Field(min_length=1)
    attempted_sources: tuple[str, ...] = ()
    qualification_verdict: Verdict
    run_order_sha256: Sha256Hex
    artifacts: TargetedArtifactHashesV1

    @model_validator(mode="after")
    def _validate_invariants(self) -> "TargetedRunV1":
        if self.assessment.status is not AssessmentStatus.READY:
            raise ValueError("targeted execution requires READY assessment")
        if self.selected_sources != self.assessment.selected_sources:
            raise ValueError("selected_sources must exactly match assessment selected_sources")
        _require_unique_sorted_strings(self.selected_sources, "selected_sources")
        _require_unique_sorted_strings(self.attempted_sources, "attempted_sources")
        if not set(self.attempted_sources).issubset(self.selected_sources):
            raise ValueError("attempted_sources must be a subset of selected_sources")
        return self
