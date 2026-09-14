from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MaterialDimension(str, Enum):
    AGENT_BINARY = "AGENT_BINARY"
    AGENT_SUPPORT = "AGENT_SUPPORT"
    MODEL_DECLARATION = "MODEL_DECLARATION"
    PROJECT_CONFIG = "PROJECT_CONFIG"
    PREPARED_TARGET = "PREPARED_TARGET"
    RUNTIME_PROFILE = "RUNTIME_PROFILE"
    PREPARATION_PROFILE = "PREPARATION_PROFILE"
    ISOLATION_PROFILE = "ISOLATION_PROFILE"
    RESOURCE_POLICY = "RESOURCE_POLICY"


class ConditionType(str, Enum):
    EVIDENCE_BOUND = "EvidenceBound"
    DESIGN_FROZEN = "DesignFrozen"
    STATE_BOUND = "StateBound"
    PREPARATION_EQUIVALENT = "PreparationEquivalent"
    BASELINE_STABLE = "BaselineStable"
    ATTEMPTS_COMPLETE = "AttemptsComplete"
    ATTEMPTS_ISOLATED = "AttemptsIsolated"
    ORDER_VALID = "OrderValid"
    TEMPORAL_PAIR_VALID = "TemporalPairValid"
    MATERIAL_DIMENSIONS_CONTROLLED = "MaterialDimensionsControlled"


class ConditionStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class ClaimClass(str, Enum):
    NO_REGRESSION_OBSERVED = "NO_REGRESSION_OBSERVED"
    ATTRIBUTABLE_CHANGESET = "ATTRIBUTABLE_CHANGESET"
    UNRESOLVED = "UNRESOLVED"


class ModelDeclarationV1(StrictModel):
    id: str = Field(min_length=1)
    snapshot: str | None = None
    reasoning_effort: str = Field(min_length=1)


class AgentDependencyStateV1(StrictModel):
    agent_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    binary_sha256: Sha256Hex
    support_sha256: Sha256Hex | None = None
    model: ModelDeclarationV1


class ChangeSetV1(StrictModel):
    baseline_state_sha256: Sha256Hex
    candidate_state_sha256: Sha256Hex
    changed_dimensions: tuple[MaterialDimension, ...]

    @model_validator(mode="after")
    def unique_dimensions(self) -> ChangeSetV1:
        if len(set(self.changed_dimensions)) != len(self.changed_dimensions):
            raise ValueError("changed_dimensions must be unique")
        return self


class CanaryProtocolDesignV1(StrictModel):
    canary_id: str = Field(min_length=1)
    canary_fingerprint_sha256: Sha256Hex
    material_dimensions: tuple[MaterialDimension, ...] | None = None
    max_pair_gap_ms: int | None = Field(default=None, gt=0)
    preparation_sha256: Sha256Hex | None = None
    isolation_sha256: Sha256Hex | None = None
    resource_sha256: Sha256Hex | None = None
    runtime_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def unique_material_dimensions(self) -> CanaryProtocolDesignV1:
        if self.material_dimensions is not None and len(set(self.material_dimensions)) != len(
            self.material_dimensions
        ):
            raise ValueError("material_dimensions must be unique")
        return self


class ProtocolDesignV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["paired-change/v1"]
    protocol_digest: Sha256Hex
    suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    repetitions: int = Field(gt=0)
    order_policy: Literal["alternating-v1"]
    lifecycle: Literal["FRESH"]
    canaries: tuple[CanaryProtocolDesignV1, ...]

    @model_validator(mode="after")
    def unique_canaries(self) -> ProtocolDesignV1:
        ids = [item.canary_id for item in self.canaries]
        if len(set(ids)) != len(ids):
            raise ValueError("canary ids must be unique")
        return self


class AttemptProtocolContextV1(StrictModel):
    side: Literal["baseline", "candidate"]
    repetition: int = Field(gt=0)
    events_sha256: Sha256Hex
    protocol_design_sha256: Sha256Hex
    started_offset_ms: int | None = Field(default=None, ge=0)
    finished_offset_ms: int | None = Field(default=None, ge=0)
    isolation_instance_sha256: Sha256Hex | None = None
    preparation_sha256: Sha256Hex | None = None
    isolation_sha256: Sha256Hex | None = None
    resource_sha256: Sha256Hex | None = None
    runtime_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def valid_offsets(self) -> AttemptProtocolContextV1:
        if (
            self.started_offset_ms is not None
            and self.finished_offset_ms is not None
            and self.finished_offset_ms < self.started_offset_ms
        ):
            raise ValueError("finished_offset_ms precedes started_offset_ms")
        return self


class PairEvidenceV1(StrictModel):
    repetition: int = Field(gt=0)
    attempts: tuple[AttemptProtocolContextV1, AttemptProtocolContextV1]

    @model_validator(mode="after")
    def valid_pair(self) -> PairEvidenceV1:
        if {item.side for item in self.attempts} != {"baseline", "candidate"}:
            raise ValueError("pair must contain one baseline and one candidate attempt")
        if any(item.repetition != self.repetition for item in self.attempts):
            raise ValueError("pair repetition does not match attempt repetition")
        return self


class CanaryRunEvidenceV1(StrictModel):
    canary_id: str = Field(min_length=1)
    canary_fingerprint_sha256: Sha256Hex
    prepared_target_sha256: Sha256Hex | None = None
    pairs: tuple[PairEvidenceV1, ...]

    @model_validator(mode="after")
    def unique_repetitions(self) -> CanaryRunEvidenceV1:
        repetitions = [pair.repetition for pair in self.pairs]
        if len(set(repetitions)) != len(repetitions):
            raise ValueError("pair repetitions must be unique")
        return self


class _RunBaseV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["paired-change/v1"]
    protocol_digest: Sha256Hex
    protocol_design: ProtocolDesignV1
    protocol_design_sha256: Sha256Hex
    qualification_id: str = Field(min_length=1)
    baseline_state: AgentDependencyStateV1
    candidate_state: AgentDependencyStateV1
    changeset_sha256: Sha256Hex
    canaries: tuple[CanaryRunEvidenceV1, ...]

    @model_validator(mode="after")
    def unique_canaries(self) -> _RunBaseV1:
        ids = [item.canary_id for item in self.canaries]
        if len(set(ids)) != len(ids):
            raise ValueError("canary ids must be unique")
        return self


class PairedChangeRunV1(_RunBaseV1):
    pass


class ProtocolEvidenceV1(_RunBaseV1):
    evidence_manifest_sha256: Sha256Hex


class ProtocolConditionV1(StrictModel):
    type: ConditionType
    status: ConditionStatus
    reason: str = Field(min_length=1)
    evidence_sha256: Sha256Hex | None = None


class CanaryClaimV1(StrictModel):
    canary_id: str = Field(min_length=1)
    conditions: tuple[ProtocolConditionV1, ...]
    claim: ClaimClass


class ClaimReceiptV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["paired-change/v1"]
    protocol_digest: Sha256Hex
    qualification_id: str = Field(min_length=1)
    evidence_manifest_sha256: Sha256Hex
    protocol_evidence_sha256: Sha256Hex
    baseline_state_sha256: Sha256Hex
    candidate_state_sha256: Sha256Hex
    changeset_sha256: Sha256Hex
    qualification_conditions: tuple[ProtocolConditionV1, ...]
    canary_claims: tuple[CanaryClaimV1, ...]
    verifier_name: str = Field(min_length=1)
    verifier_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_canary_claims(self) -> ClaimReceiptV1:
        canary_ids = [item.canary_id for item in self.canary_claims]
        if len(set(canary_ids)) != len(canary_ids):
            raise ValueError("canary claim ids must be unique")
        return self
