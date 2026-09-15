"""Minimal, deterministic, literal-safe terminal rendering for first-bad/v1 receipts."""

from __future__ import annotations

from .models import EdgeClassification, FirstBadClaimClass, FirstBadReceiptV1

_EDGE_LABELS = {
    EdgeClassification.NO_REGRESSION_OBSERVED: "NO REGRESSION",
    EdgeClassification.ATTRIBUTABLE_CHANGESET: "ATTRIBUTABLE CHANGE",
    EdgeClassification.UNRESOLVED: "UNRESOLVED",
}

_CLAIM_LABELS = {
    FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND: "NO ATTRIBUTABLE BAD FOUND",
    FirstBadClaimClass.UNRESOLVED: "UNRESOLVED",
}


def render_first_bad_receipt(receipt: FirstBadReceiptV1) -> str:
    lines = ["QuaLock First-Bad Verification"]
    if receipt.edges:
        lines.append(
            f"Range: {receipt.edges[0].baseline_version} -> {receipt.edges[-1].candidate_version}"
        )
        lines.extend(
            f"{edge.baseline_version} -> {edge.candidate_version}  "
            f"{_EDGE_LABELS[edge.classification]}"
            for edge in receipt.edges
        )
    if receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD:
        lines.append(f"FIRST ATTRIBUTABLE BAD: {receipt.boundary_version}")
    else:
        lines.append(_CLAIM_LABELS[receipt.claim])
    return "\n".join(lines) + "\n"
