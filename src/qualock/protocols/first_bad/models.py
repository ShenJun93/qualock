"""Strict first-bad/v1 chain evidence, condition, and receipt models."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    ClaimClass,
    ConditionStatus,
    ModelDeclarationV1,
    Sha256Hex,
    StrictModel,
)

__all__ = [
    "EdgeClassification",
    "FirstBadCanarySummaryV1",
    "FirstBadChainEvidenceV1",
    "FirstBadClaimClass",
    "FirstBadConditionType",
    "FirstBadConditionV1",
    "FirstBadEdgeEvidenceV1",
    "FirstBadEdgeSummaryV1",
    "FirstBadReceiptV1",
]


class FirstBadConditionType(str, Enum):
    CATALOG_BOUND = "CatalogBound"
    BASELINE_ANCHORED = "BaselineAnchored"
    EDGES_CONTIGUOUS = "EdgesContiguous"
    VERSIONS_ORDERED = "VersionsOrdered"
    SUITE_FROZEN = "SuiteFrozen"
    CONFIG_FROZEN = "ConfigFrozen"
    DESIGN_FROZEN = "DesignFrozen"
    EVERY_EDGE_VERIFIED = "EveryEdgeVerified"
    PREFIX_NO_REGRESSION = "PrefixNoRegression"
    BOUNDARY_ATTRIBUTABLE = "BoundaryAttributable"


class FirstBadClaimClass(str, Enum):
    FIRST_ATTRIBUTABLE_BAD = "FIRST_ATTRIBUTABLE_BAD"
    NO_ATTRIBUTABLE_BAD_FOUND = "NO_ATTRIBUTABLE_BAD_FOUND"
    UNRESOLVED = "UNRESOLVED"


class EdgeClassification(str, Enum):
    NO_REGRESSION_OBSERVED = "NO_REGRESSION_OBSERVED"
    ATTRIBUTABLE_CHANGESET = "ATTRIBUTABLE_CHANGESET"
    UNRESOLVED = "UNRESOLVED"


class FirstBadEdgeEvidenceV1(StrictModel):
    index: int = Field(ge=0)
    baseline_version: str = Field(min_length=1, max_length=128)
    candidate_version: str = Field(min_length=1, max_length=128)
    baseline_runtime_identity: AgentDependencyStateV1
    candidate_runtime_identity: AgentDependencyStateV1
    bundle_manifest_sha256: Sha256Hex
    protocol_evidence_sha256: Sha256Hex


class FirstBadChainEvidenceV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["first-bad/v1"]
    agent_name: str = Field(min_length=1, max_length=64)
    baseline_version: str = Field(min_length=1, max_length=128)
    baseline_runtime_identity: AgentDependencyStateV1
    upper_version: str = Field(min_length=1, max_length=128)
    catalog_versions: tuple[str, ...] = Field(min_length=2, max_length=256)
    catalog_sha256: Sha256Hex
    suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    model_pin: ModelDeclarationV1
    protocol_design_sha256: Sha256Hex
    edges: tuple[FirstBadEdgeEvidenceV1, ...] = Field(min_length=1, max_length=255)
    chain_sha256: Sha256Hex


class FirstBadConditionV1(StrictModel):
    type: FirstBadConditionType
    status: ConditionStatus
    reason: str = Field(min_length=1, max_length=512)


class FirstBadCanarySummaryV1(StrictModel):
    canary_id: str = Field(min_length=1, max_length=256)
    claim: ClaimClass


class FirstBadEdgeSummaryV1(StrictModel):
    index: int = Field(ge=0)
    baseline_version: str = Field(min_length=1, max_length=128)
    candidate_version: str = Field(min_length=1, max_length=128)
    canaries: tuple[FirstBadCanarySummaryV1, ...]
    classification: EdgeClassification

    @model_validator(mode="after")
    def unique_canary_ids(self) -> FirstBadEdgeSummaryV1:
        ids = [item.canary_id for item in self.canaries]
        if len(set(ids)) != len(ids):
            raise ValueError("canary summary ids must be unique")
        return self


class FirstBadReceiptV1(StrictModel):
    schema_version: Literal[1]
    protocol_id: Literal["first-bad/v1"]
    chain_sha256: Sha256Hex
    catalog_sha256: Sha256Hex
    conditions: tuple[FirstBadConditionV1, ...]
    edges: tuple[FirstBadEdgeSummaryV1, ...]
    claim: FirstBadClaimClass
    boundary_version: str | None = Field(default=None, max_length=128)
    boundary_edge_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def unique_edge_indices(self) -> FirstBadReceiptV1:
        indices = [item.index for item in self.edges]
        if len(set(indices)) != len(indices):
            raise ValueError("edge summary indices must be unique")
        return self
