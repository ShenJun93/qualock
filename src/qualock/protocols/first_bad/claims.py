"""Pure first-bad/v1 per-edge aggregation and chain claim transition."""

from __future__ import annotations

from collections.abc import Sequence

from qualock.protocols.paired_change.models import ClaimClass, ClaimReceiptV1

from .models import (
    EdgeClassification,
    FirstBadCanarySummaryV1,
    FirstBadClaimClass,
    FirstBadEdgeSummaryV1,
)


def derive_edge_classification(canary_claims: Sequence[ClaimClass]) -> EdgeClassification:
    if any(claim is ClaimClass.UNRESOLVED for claim in canary_claims):
        return EdgeClassification.UNRESOLVED
    if any(claim is ClaimClass.ATTRIBUTABLE_CHANGESET for claim in canary_claims):
        return EdgeClassification.ATTRIBUTABLE_CHANGESET
    return EdgeClassification.NO_REGRESSION_OBSERVED


def derive_edge_summary(
    index: int,
    baseline_version: str,
    candidate_version: str,
    receipt: ClaimReceiptV1,
) -> FirstBadEdgeSummaryV1:
    canaries = tuple(
        FirstBadCanarySummaryV1(canary_id=item.canary_id, claim=item.claim)
        for item in sorted(receipt.canary_claims, key=lambda item: item.canary_id)
    )
    classification = derive_edge_classification(tuple(item.claim for item in canaries))
    return FirstBadEdgeSummaryV1(
        index=index,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        canaries=canaries,
        classification=classification,
    )


def derive_chain_claim(
    catalog_versions: tuple[str, ...],
    edge_summaries: tuple[FirstBadEdgeSummaryV1, ...],
) -> tuple[FirstBadClaimClass, str | None, int | None]:
    """Derive the chain claim from a structurally validated, index-ordered edge prefix."""

    required_edges = len(catalog_versions) - 1
    for summary in edge_summaries:
        if summary.classification is EdgeClassification.ATTRIBUTABLE_CHANGESET:
            return (
                FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD,
                summary.candidate_version,
                summary.index,
            )
        if summary.classification is EdgeClassification.UNRESOLVED:
            return (FirstBadClaimClass.UNRESOLVED, None, None)

    if len(edge_summaries) == required_edges:
        return (FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND, None, None)
    return (FirstBadClaimClass.UNRESOLVED, None, None)
