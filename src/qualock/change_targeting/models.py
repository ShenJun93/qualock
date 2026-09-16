from enum import Enum
from typing import Literal

from packaging.version import InvalidVersion, Version
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .contracts import validate_contract_id

ScalarValue = StrictStr | StrictBool | StrictInt | None


def _validate_scalar_map_keys(value: dict[str, ScalarValue]) -> dict[str, ScalarValue]:
    for key in value:
        if not isinstance(key, str) or key == "":
            raise ValueError("map keys must be non-empty strings")
    return value


def _validate_version_string(value: str) -> str:
    try:
        Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"invalid version: {value}") from exc
    return value


def _require_unique_sorted_strings(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    if list(values) != sorted(values):
        raise ValueError(f"{label} must be lexically sorted")


class SourceProvenanceV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class ChangeImpactV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    scope_requirements: dict[StrictStr, ScalarValue] = Field(default_factory=dict)

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        return validate_contract_id(value)

    @field_validator("scope_requirements")
    @classmethod
    def _validate_scope_requirements(
        cls, value: dict[str, ScalarValue]
    ) -> dict[str, ScalarValue]:
        return _validate_scalar_map_keys(value)


class ChangeSignalV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[0]
    agent: Literal["codex", "claude", "antigravity", "gemini"]
    baseline_version: str
    candidate_version: str
    impacts: tuple[ChangeImpactV0, ...] = Field(min_length=1)
    source: SourceProvenanceV0 | None = None

    @field_validator("baseline_version", "candidate_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _validate_version_string(value)


class TargetContextV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[0]
    facts: dict[StrictStr, ScalarValue] = Field(default_factory=dict)

    @field_validator("facts")
    @classmethod
    def _validate_facts(cls, value: dict[str, ScalarValue]) -> dict[str, ScalarValue]:
        return _validate_scalar_map_keys(value)


class CoverageDeclarationV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    source_kind: Literal["canary"] = "canary"
    contract_id: str
    context_requirements: dict[StrictStr, ScalarValue] = Field(default_factory=dict)

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        return validate_contract_id(value)

    @field_validator("context_requirements")
    @classmethod
    def _validate_context_requirements(
        cls, value: dict[str, ScalarValue]
    ) -> dict[str, ScalarValue]:
        return _validate_scalar_map_keys(value)


class AssessmentStatus(str, Enum):
    READY = "READY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"


class IncompleteReason(str, Enum):
    COVERAGE_GAP = "COVERAGE_GAP"
    TARGET_CONTEXT_UNKNOWN = "TARGET_CONTEXT_UNKNOWN"
    COVERAGE_CONTEXT_UNKNOWN = "COVERAGE_CONTEXT_UNKNOWN"


class UncoveredV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    reason: Literal[IncompleteReason.COVERAGE_GAP] = IncompleteReason.COVERAGE_GAP

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        return validate_contract_id(value)


class UnresolvedV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    reason: Literal[
        IncompleteReason.TARGET_CONTEXT_UNKNOWN,
        IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
    ]
    missing_context_keys: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        return validate_contract_id(value)

    @model_validator(mode="after")
    def _validate_invariants(self) -> "UnresolvedV0":
        if not self.missing_context_keys:
            raise ValueError("unresolved rows require non-empty missing_context_keys")
        _require_unique_sorted_strings(self.missing_context_keys, "missing_context_keys")
        _require_unique_sorted_strings(self.source_ids, "source_ids")
        if self.reason is IncompleteReason.TARGET_CONTEXT_UNKNOWN and self.source_ids:
            raise ValueError("TARGET_CONTEXT_UNKNOWN rows must have empty source_ids")
        if self.reason is IncompleteReason.COVERAGE_CONTEXT_UNKNOWN and not self.source_ids:
            raise ValueError("COVERAGE_CONTEXT_UNKNOWN rows require non-empty source_ids")
        return self


class CoverageAssessmentV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[0] = 0
    signal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: AssessmentStatus
    relevant_contracts: tuple[str, ...] = ()
    selected_sources: tuple[str, ...] = ()
    uncovered: tuple[UncoveredV0, ...] = ()
    unresolved: tuple[UnresolvedV0, ...] = ()

    @model_validator(mode="after")
    def _validate_invariants(self) -> "CoverageAssessmentV0":
        _require_unique_sorted_strings(self.relevant_contracts, "relevant_contracts")
        _require_unique_sorted_strings(self.selected_sources, "selected_sources")

        uncovered_contracts = tuple(item.contract_id for item in self.uncovered)
        _require_unique_sorted_strings(uncovered_contracts, "uncovered contract_id")

        unresolved_keys = [(item.contract_id, item.reason.value) for item in self.unresolved]
        if len(set(unresolved_keys)) != len(unresolved_keys):
            raise ValueError("unresolved rows must be unique by (contract_id, reason)")
        if unresolved_keys != sorted(unresolved_keys):
            raise ValueError("unresolved must be ordered by (contract_id, reason)")

        if self.status is AssessmentStatus.READY:
            if not self.relevant_contracts or not self.selected_sources:
                raise ValueError(
                    "READY requires non-empty relevant_contracts and selected_sources"
                )
            if self.uncovered or self.unresolved:
                raise ValueError("READY requires empty uncovered/unresolved")
        elif self.status is AssessmentStatus.NOT_APPLICABLE:
            if self.relevant_contracts or self.selected_sources or self.uncovered or self.unresolved:
                raise ValueError("NOT_APPLICABLE requires all companion collections empty")
        elif self.status is AssessmentStatus.INCOMPLETE:
            if self.selected_sources:
                raise ValueError("INCOMPLETE requires empty selected_sources")
            if not self.uncovered and not self.unresolved:
                raise ValueError(
                    "INCOMPLETE requires at least one uncovered or unresolved entry"
                )
        return self
