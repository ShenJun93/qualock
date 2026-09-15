"""Minimal, deterministic, literal-safe terminal rendering for first-bad/v1 receipts."""

from __future__ import annotations

from .models import (
    EdgeClassification,
    FirstBadClaimClass,
    FirstBadEdgeSummaryV1,
    FirstBadReceiptV1,
)

_EDGE_LABELS = {
    EdgeClassification.NO_REGRESSION_OBSERVED: "NO REGRESSION",
    EdgeClassification.ATTRIBUTABLE_CHANGESET: "ATTRIBUTABLE CHANGE",
    EdgeClassification.UNRESOLVED: "UNRESOLVED",
}

_CLAIM_LABELS = {
    FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND: "NO ATTRIBUTABLE BAD FOUND",
    FirstBadClaimClass.UNRESOLVED: "UNRESOLVED",
}


_TERMINAL_UNRESOLVED_COPY = "No first-attributable-bad claim can be made beyond this edge.\n"


def render_first_bad_title() -> str:
    return "QuaLock First-Bad Causal Scan\n"


def render_first_bad_range(display_name: str, baseline_version: str, upper_version: str) -> str:
    return f"{display_name} baseline {baseline_version} -> upper {upper_version}\n"


def render_first_bad_edge_line(summary: FirstBadEdgeSummaryV1) -> str:
    return (
        f"{summary.baseline_version} -> {summary.candidate_version}  "
        f"{_EDGE_LABELS[summary.classification]}\n"
    )


def render_first_bad_terminal(receipt: FirstBadReceiptV1) -> str:
    if receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD:
        return f"FIRST ATTRIBUTABLE BAD: {receipt.boundary_version}\n"
    if receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND:
        return "No attributable bad found\n"
    return _TERMINAL_UNRESOLVED_COPY


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
