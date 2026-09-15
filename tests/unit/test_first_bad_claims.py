from __future__ import annotations

from qualock.protocols.first_bad.claims import (
    derive_chain_claim,
    derive_edge_classification,
    derive_edge_summary,
)
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadCanarySummaryV1,
    FirstBadClaimClass,
    FirstBadEdgeSummaryV1,
)
from qualock.protocols.paired_change.models import (
    CanaryClaimV1,
    ClaimClass,
    ClaimReceiptV1,
    ConditionStatus,
    ConditionType,
    ProtocolConditionV1,
)


def _protocol_condition() -> ProtocolConditionV1:
    return ProtocolConditionV1(
        type=ConditionType.EVIDENCE_BOUND,
        status=ConditionStatus.TRUE,
        reason="EvidenceVerified",
    )


def _canary_claim(canary_id: str, claim: ClaimClass) -> CanaryClaimV1:
    return CanaryClaimV1(
        canary_id=canary_id,
        conditions=(_protocol_condition(),),
        claim=claim,
    )


def _receipt(claims: dict[str, ClaimClass]) -> ClaimReceiptV1:
    return ClaimReceiptV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest="1" * 64,
        qualification_id="q1",
        evidence_manifest_sha256="2" * 64,
        protocol_evidence_sha256="3" * 64,
        baseline_state_sha256="4" * 64,
        candidate_state_sha256="5" * 64,
        changeset_sha256="6" * 64,
        qualification_conditions=(_protocol_condition(),),
        canary_claims=tuple(
            _canary_claim(canary_id, claim) for canary_id, claim in claims.items()
        ),
        verifier_name="qualock",
        verifier_version="0.1.0",
    )


def _summary(
    *,
    index: int,
    classification: EdgeClassification,
    baseline_version: str | None = None,
    candidate_version: str | None = None,
) -> FirstBadEdgeSummaryV1:
    return FirstBadEdgeSummaryV1(
        index=index,
        baseline_version=baseline_version or f"0.{index}.0",
        candidate_version=candidate_version or f"0.{index + 1}.0",
        canaries=(FirstBadCanarySummaryV1(canary_id="sample", claim=ClaimClass.UNRESOLVED),),
        classification=classification,
    )


# --- derive_edge_classification ---


def test_all_no_regression_canaries_yield_no_regression_edge() -> None:
    claims = (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.NO_REGRESSION_OBSERVED)
    assert derive_edge_classification(claims) is EdgeClassification.NO_REGRESSION_OBSERVED


def test_one_attributable_and_no_unresolved_yields_attributable_edge() -> None:
    claims = (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.ATTRIBUTABLE_CHANGESET)
    assert derive_edge_classification(claims) is EdgeClassification.ATTRIBUTABLE_CHANGESET


def test_one_attributable_and_one_unresolved_yields_unresolved_edge() -> None:
    claims = (ClaimClass.ATTRIBUTABLE_CHANGESET, ClaimClass.UNRESOLVED)
    assert derive_edge_classification(claims) is EdgeClassification.UNRESOLVED


def test_any_unresolved_alone_yields_unresolved_edge() -> None:
    claims = (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.UNRESOLVED)
    assert derive_edge_classification(claims) is EdgeClassification.UNRESOLVED


# --- derive_edge_summary ---


def test_derive_edge_summary_sorts_canaries_by_id() -> None:
    receipt = _receipt(
        {
            "zeta": ClaimClass.NO_REGRESSION_OBSERVED,
            "alpha": ClaimClass.ATTRIBUTABLE_CHANGESET,
        }
    )

    summary = derive_edge_summary(3, "0.151.0", "0.152.0", receipt)

    assert summary.index == 3
    assert summary.baseline_version == "0.151.0"
    assert summary.candidate_version == "0.152.0"
    assert [item.canary_id for item in summary.canaries] == ["alpha", "zeta"]
    assert summary.classification is EdgeClassification.ATTRIBUTABLE_CHANGESET


def test_derive_edge_summary_all_no_regression() -> None:
    receipt = _receipt(
        {
            "a": ClaimClass.NO_REGRESSION_OBSERVED,
            "b": ClaimClass.NO_REGRESSION_OBSERVED,
        }
    )

    summary = derive_edge_summary(0, "0.150.0", "0.151.0", receipt)

    assert summary.classification is EdgeClassification.NO_REGRESSION_OBSERVED


def test_derive_edge_summary_unresolved_canary_forces_unresolved_edge() -> None:
    receipt = _receipt(
        {
            "a": ClaimClass.ATTRIBUTABLE_CHANGESET,
            "b": ClaimClass.UNRESOLVED,
        }
    )

    summary = derive_edge_summary(0, "0.150.0", "0.151.0", receipt)

    assert summary.classification is EdgeClassification.UNRESOLVED


# --- derive_chain_claim ---


def test_full_no_regression_coverage_yields_no_attributable_bad_found() -> None:
    catalog = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    edges = (
        _summary(index=0, classification=EdgeClassification.NO_REGRESSION_OBSERVED),
        _summary(index=1, classification=EdgeClassification.NO_REGRESSION_OBSERVED),
        _summary(index=2, classification=EdgeClassification.NO_REGRESSION_OBSERVED),
    )

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert boundary_version is None
    assert boundary_index is None


def test_attributable_first_edge_yields_first_attributable_bad_at_index_zero() -> None:
    catalog = ("0.150.0", "0.151.0")
    edges = (
        _summary(
            index=0,
            classification=EdgeClassification.ATTRIBUTABLE_CHANGESET,
            baseline_version="0.150.0",
            candidate_version="0.151.0",
        ),
    )

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert boundary_version == "0.151.0"
    assert boundary_index == 0


def test_no_regression_prefix_then_attributable_yields_earliest_boundary() -> None:
    catalog = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    edges = (
        _summary(index=0, classification=EdgeClassification.NO_REGRESSION_OBSERVED),
        _summary(
            index=1,
            classification=EdgeClassification.ATTRIBUTABLE_CHANGESET,
            baseline_version="0.151.0",
            candidate_version="0.152.0",
        ),
    )

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert boundary_version == "0.152.0"
    assert boundary_index == 1


def test_clean_truncated_no_regression_prefix_is_unresolved() -> None:
    catalog = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    edges = (_summary(index=0, classification=EdgeClassification.NO_REGRESSION_OBSERVED),)

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.UNRESOLVED
    assert boundary_version is None
    assert boundary_index is None


def test_unresolved_prefix_edge_is_unresolved() -> None:
    catalog = ("0.150.0", "0.151.0")
    edges = (_summary(index=0, classification=EdgeClassification.UNRESOLVED),)

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.UNRESOLVED
    assert boundary_version is None
    assert boundary_index is None


def test_unresolved_boundary_before_later_attributable_never_promotes() -> None:
    catalog = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    edges = (
        _summary(index=0, classification=EdgeClassification.UNRESOLVED),
        _summary(
            index=1,
            classification=EdgeClassification.ATTRIBUTABLE_CHANGESET,
            baseline_version="0.151.0",
            candidate_version="0.152.0",
        ),
    )

    claim, boundary_version, boundary_index = derive_chain_claim(catalog, edges)

    assert claim is FirstBadClaimClass.UNRESOLVED
    assert boundary_version is None
    assert boundary_index is None
