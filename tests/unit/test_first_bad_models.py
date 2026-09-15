from __future__ import annotations

import pytest
from pydantic import ValidationError

from qualock.protocols.first_bad.fingerprint import digest_catalog, digest_chain_evidence
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadCanarySummaryV1,
    FirstBadChainEvidenceV1,
    FirstBadClaimClass,
    FirstBadConditionType,
    FirstBadConditionV1,
    FirstBadEdgeEvidenceV1,
    FirstBadEdgeSummaryV1,
    FirstBadReceiptV1,
)
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    ClaimClass,
    ConditionStatus,
    ModelDeclarationV1,
)

SHA = "a" * 64


def _state(*, binary: str = SHA, support: str | None = "b" * 64) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name="codex",
        version="0.150.0",
        binary_sha256=binary,
        support_sha256=support,
        model=ModelDeclarationV1(id="gpt-5.6-sol", snapshot=None, reasoning_effort="high"),
    )


def _edge(
    *,
    index: int = 0,
    baseline_version: str = "0.150.0",
    candidate_version: str = "0.151.0",
    bundle_manifest_sha256: str = "c" * 64,
    protocol_evidence_sha256: str = "d" * 64,
) -> FirstBadEdgeEvidenceV1:
    return FirstBadEdgeEvidenceV1(
        index=index,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        baseline_runtime_identity=_state(),
        candidate_runtime_identity=_state(binary="e" * 64),
        bundle_manifest_sha256=bundle_manifest_sha256,
        protocol_evidence_sha256=protocol_evidence_sha256,
    )


def _chain(
    *,
    catalog_versions: tuple[str, ...] = ("0.150.0", "0.151.0"),
    edges: tuple[FirstBadEdgeEvidenceV1, ...] | None = None,
    chain_sha256: str = "f" * 64,
) -> FirstBadChainEvidenceV1:
    return FirstBadChainEvidenceV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        agent_name="codex",
        baseline_version=catalog_versions[0],
        baseline_runtime_identity=_state(),
        upper_version=catalog_versions[-1],
        catalog_versions=catalog_versions,
        catalog_sha256="1" * 64,
        suite_sha256="2" * 64,
        config_sha256="3" * 64,
        model_pin=ModelDeclarationV1(id="gpt-5.6-sol", snapshot=None, reasoning_effort="high"),
        protocol_design_sha256="4" * 64,
        edges=edges if edges is not None else (_edge(),),
        chain_sha256=chain_sha256,
    )


def _condition(
    condition_type: FirstBadConditionType = FirstBadConditionType.CATALOG_BOUND,
    status: ConditionStatus = ConditionStatus.TRUE,
    reason: str = "catalog digest and declared range are bound",
) -> FirstBadConditionV1:
    return FirstBadConditionV1(type=condition_type, status=status, reason=reason)


def _canary_summary(
    canary_id: str = "sample", claim: ClaimClass = ClaimClass.NO_REGRESSION_OBSERVED
) -> FirstBadCanarySummaryV1:
    return FirstBadCanarySummaryV1(canary_id=canary_id, claim=claim)


def _edge_summary(
    *,
    index: int = 0,
    baseline_version: str = "0.150.0",
    candidate_version: str = "0.151.0",
    canaries: tuple[FirstBadCanarySummaryV1, ...] | None = None,
    classification: EdgeClassification = EdgeClassification.NO_REGRESSION_OBSERVED,
) -> FirstBadEdgeSummaryV1:
    return FirstBadEdgeSummaryV1(
        index=index,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        canaries=canaries if canaries is not None else (_canary_summary(),),
        classification=classification,
    )


def _receipt(
    *,
    edges: tuple[FirstBadEdgeSummaryV1, ...] | None = None,
    claim: FirstBadClaimClass = FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND,
) -> FirstBadReceiptV1:
    return FirstBadReceiptV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        chain_sha256="5" * 64,
        catalog_sha256="6" * 64,
        conditions=(_condition(),),
        edges=edges if edges is not None else (_edge_summary(),),
        claim=claim,
        boundary_version=None,
        boundary_edge_index=None,
    )


# --- strict model / extra-forbid coverage ---


def test_edge_evidence_forbids_unknown_fields() -> None:
    payload = _edge().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        FirstBadEdgeEvidenceV1.model_validate(payload)


def test_chain_evidence_forbids_unknown_fields() -> None:
    payload = _chain().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        FirstBadChainEvidenceV1.model_validate(payload)


def test_receipt_forbids_unknown_fields() -> None:
    payload = _receipt().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        FirstBadReceiptV1.model_validate(payload)


def test_condition_type_and_status_are_closed_enums() -> None:
    with pytest.raises(ValidationError):
        FirstBadConditionV1(type="NotACondition", status="TRUE", reason="x")
    with pytest.raises(ValidationError):
        FirstBadConditionV1(type="CatalogBound", status="MAYBE", reason="x")


def test_protocol_id_and_schema_version_are_pinned_literals() -> None:
    payload = _chain().model_dump(mode="json")
    payload["protocol_id"] = "paired-change/v1"
    with pytest.raises(ValidationError):
        FirstBadChainEvidenceV1.model_validate(payload)

    payload = _chain().model_dump(mode="json")
    payload["schema_version"] = 2
    with pytest.raises(ValidationError):
        FirstBadChainEvidenceV1.model_validate(payload)


# --- bounds ---


def test_edge_index_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _edge(index=-1)


def test_version_text_bounds() -> None:
    with pytest.raises(ValidationError):
        _edge(baseline_version="")
    with pytest.raises(ValidationError):
        _edge(baseline_version="x" * 129)


def test_catalog_versions_length_bounds() -> None:
    with pytest.raises(ValidationError):
        _chain(catalog_versions=("0.150.0",))

    at_min = _chain(catalog_versions=("0.150.0", "0.151.0"))
    assert len(at_min.catalog_versions) == 2

    at_max = tuple(f"0.{i}.0" for i in range(256))
    chain = _chain(catalog_versions=at_max)
    assert len(chain.catalog_versions) == 256

    over_max = tuple(f"0.{i}.0" for i in range(257))
    with pytest.raises(ValidationError):
        _chain(catalog_versions=over_max)


def test_edges_length_bounds() -> None:
    with pytest.raises(ValidationError):
        _chain(edges=())

    at_max = tuple(_edge(index=i) for i in range(255))
    chain = _chain(edges=at_max)
    assert len(chain.edges) == 255

    over_max = tuple(_edge(index=i) for i in range(256))
    with pytest.raises(ValidationError):
        _chain(edges=over_max)


def test_condition_reason_length_bounds() -> None:
    with pytest.raises(ValidationError):
        _condition(reason="")
    with pytest.raises(ValidationError):
        _condition(reason="x" * 513)


def test_canary_summary_id_length_bounds() -> None:
    with pytest.raises(ValidationError):
        _canary_summary(canary_id="")
    with pytest.raises(ValidationError):
        _canary_summary(canary_id="x" * 257)


def test_sha_fields_require_lowercase_64_hex() -> None:
    with pytest.raises(ValidationError):
        _edge(bundle_manifest_sha256="A" * 64)


# --- duplicate rejection (structural only) ---


def test_edge_summary_rejects_duplicate_canary_ids() -> None:
    dup = (_canary_summary(canary_id="sample"), _canary_summary(canary_id="sample"))
    with pytest.raises(ValidationError):
        _edge_summary(canaries=dup)


def test_receipt_rejects_duplicate_edge_indices() -> None:
    dup = (_edge_summary(index=0), _edge_summary(index=0))
    with pytest.raises(ValidationError):
        _receipt(edges=dup)


def test_receipt_permits_out_of_order_edge_indices() -> None:
    out_of_order = (
        _edge_summary(index=1, baseline_version="0.151.0", candidate_version="0.152.0"),
        _edge_summary(index=0, baseline_version="0.150.0", candidate_version="0.151.0"),
    )
    receipt = _receipt(edges=out_of_order)
    assert [item.index for item in receipt.edges] == [1, 0]


def test_edge_summary_permits_out_of_order_canary_ids() -> None:
    out_of_order = (_canary_summary(canary_id="zeta"), _canary_summary(canary_id="alpha"))
    summary = _edge_summary(canaries=out_of_order)
    assert [item.canary_id for item in summary.canaries] == ["zeta", "alpha"]


# --- semantic catalog errors are NOT pydantic parse failures ---


def test_duplicate_or_reordered_catalog_versions_are_not_rejected_by_pydantic() -> None:
    duplicated = _chain(catalog_versions=("0.150.0", "0.150.0", "0.151.0"))
    assert duplicated.catalog_versions == ("0.150.0", "0.150.0", "0.151.0")

    reordered = _chain(catalog_versions=("0.151.0", "0.150.0"))
    assert reordered.catalog_versions == ("0.151.0", "0.150.0")


# --- enum vocabulary ---


def test_first_bad_enum_vocabularies_are_exact() -> None:
    assert {item.value for item in FirstBadClaimClass} == {
        "FIRST_ATTRIBUTABLE_BAD",
        "NO_ATTRIBUTABLE_BAD_FOUND",
        "UNRESOLVED",
    }
    assert {item.value for item in EdgeClassification} == {
        "NO_REGRESSION_OBSERVED",
        "ATTRIBUTABLE_CHANGESET",
        "UNRESOLVED",
    }
    assert [item.value for item in FirstBadConditionType] == [
        "CatalogBound",
        "BaselineAnchored",
        "EdgesContiguous",
        "VersionsOrdered",
        "SuiteFrozen",
        "ConfigFrozen",
        "DesignFrozen",
        "EveryEdgeVerified",
        "PrefixNoRegression",
        "BoundaryAttributable",
    ]
    assert len(FirstBadConditionType) == 10


# --- digests ---


def test_digest_catalog_is_deterministic_and_order_sensitive() -> None:
    left = digest_catalog(("0.150.0", "0.151.0", "0.152.0"))
    right = digest_catalog(("0.150.0", "0.151.0", "0.152.0"))
    reordered = digest_catalog(("0.151.0", "0.150.0", "0.152.0"))

    assert left == right
    assert left != reordered


def test_digest_catalog_over_max_length_boundary() -> None:
    versions = tuple(f"0.{i}.0" for i in range(256))
    assert digest_catalog(versions) == digest_catalog(versions)


def test_digest_chain_evidence_excludes_self_field() -> None:
    base = _chain(chain_sha256="0" * 64)
    other_chain_sha = base.model_copy(update={"chain_sha256": "1" * 64})

    assert digest_chain_evidence(base) == digest_chain_evidence(other_chain_sha)


def test_digest_chain_evidence_changes_with_other_fields() -> None:
    base = _chain()
    mutated = base.model_copy(update={"upper_version": "0.199.0"})

    assert digest_chain_evidence(base) != digest_chain_evidence(mutated)


def test_digest_chain_evidence_is_deterministic() -> None:
    base = _chain()
    reconstructed = FirstBadChainEvidenceV1.model_validate(base.model_dump(mode="json"))

    assert digest_chain_evidence(base) == digest_chain_evidence(reconstructed)
