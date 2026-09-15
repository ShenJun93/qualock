"""Pure, offline verification of portable first-bad/v1 chain snapshots."""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

from qualock.evidence.bundle_models import EvidenceBundleError
from qualock.protocols.paired_change.io import PairedChangeVerificationError
from qualock.protocols.paired_change.models import ConditionStatus
from qualock.protocols.paired_change.verify import (
    VerifiedPairedChangeV1,
    verify_paired_change_payloads,
)

from .claims import derive_chain_claim, derive_edge_summary
from .fingerprint import digest_catalog, digest_chain_evidence
from .io import (
    FirstBadPackageSnapshot,
    FirstBadVerificationError,
    FirstBadVerificationReason,
    read_first_bad_package,
)
from .models import (
    EdgeClassification,
    FirstBadChainEvidenceV1,
    FirstBadClaimClass,
    FirstBadConditionType,
    FirstBadConditionV1,
    FirstBadEdgeSummaryV1,
    FirstBadReceiptV1,
)

_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

_TRUE_REASONS = {
    FirstBadConditionType.CATALOG_BOUND: "catalog digest and declared range are bound",
    FirstBadConditionType.BASELINE_ANCHORED: "first edge baseline matches the chain anchor",
    FirstBadConditionType.EDGES_CONTIGUOUS: "carried edges form a contiguous catalog prefix",
    FirstBadConditionType.VERSIONS_ORDERED: "catalog versions are strictly increasing stable releases",
    FirstBadConditionType.SUITE_FROZEN: "suite identity is frozen across carried edges",
    FirstBadConditionType.CONFIG_FROZEN: "config identity is frozen across carried edges",
    FirstBadConditionType.DESIGN_FROZEN: "model and paired-change design are frozen across carried edges",
    FirstBadConditionType.EVERY_EDGE_VERIFIED: "all carried edges passed structural paired-change verification",
    FirstBadConditionType.PREFIX_NO_REGRESSION: (
        "all edges before any attributable boundary are verified no-regression"
    ),
}
_BOUNDARY_TRUE_REASON = "terminal edge is an attributable boundary"
_BOUNDARY_FALSE_REASON = "complete range contains no attributable boundary"
_BOUNDARY_UNKNOWN_REASON = "carried prefix does not justify an attributable boundary"
_PREFIX_UNKNOWN_REASON = "causal prefix contains an unresolved edge"


def _fail(reason: FirstBadVerificationReason, field: str) -> None:
    raise FirstBadVerificationError(reason, field)


def _version_key(version: str) -> tuple[int, int, int] | None:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _verify_protocol_identity(evidence: FirstBadChainEvidenceV1) -> None:
    if evidence.schema_version != 1 or evidence.protocol_id != "first-bad/v1":
        _fail(FirstBadVerificationReason.UNSUPPORTED_CHAIN_PROTOCOL, "protocol")


def _verify_digests(evidence: FirstBadChainEvidenceV1) -> None:
    if digest_catalog(evidence.catalog_versions) != evidence.catalog_sha256:
        _fail(FirstBadVerificationReason.DIGEST_MISMATCH, "catalog_sha256")
    if digest_chain_evidence(evidence) != evidence.chain_sha256:
        _fail(FirstBadVerificationReason.DIGEST_MISMATCH, "chain_sha256")


def _verify_catalog(evidence: FirstBadChainEvidenceV1) -> None:
    versions = evidence.catalog_versions
    keys = tuple(_version_key(version) for version in versions)
    if any(key is None for key in keys):
        _fail(FirstBadVerificationReason.CATALOG_BINDING_MISMATCH, "catalog_versions")
    numeric = tuple(key for key in keys if key is not None)
    if any(left >= right for left, right in pairwise(numeric)):
        _fail(FirstBadVerificationReason.CATALOG_BINDING_MISMATCH, "catalog_versions")
    if evidence.baseline_version != versions[0] or evidence.upper_version != versions[-1]:
        _fail(FirstBadVerificationReason.CATALOG_BINDING_MISMATCH, "catalog endpoints")


def _verify_layout(snapshot: FirstBadPackageSnapshot) -> None:
    evidence = snapshot.evidence
    if len(snapshot.edges) != len(evidence.edges):
        _fail(FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, "edge package count")
    if len(evidence.edges) > len(evidence.catalog_versions) - 1:
        _fail(FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, "edge count")
    for expected_index, edge in enumerate(evidence.edges):
        if edge.index != expected_index:
            _fail(FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, "edge indices")
        if (
            edge.baseline_version != evidence.catalog_versions[expected_index]
            or edge.candidate_version != evidence.catalog_versions[expected_index + 1]
        ):
            _fail(FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, "catalog adjacency")


def _verify_children(snapshot: FirstBadPackageSnapshot) -> tuple[VerifiedPairedChangeV1, ...]:
    verified: list[VerifiedPairedChangeV1] = []
    for index, edge_snapshot in enumerate(snapshot.edges):
        try:
            child = verify_paired_change_payloads(
                edge_snapshot.bundle_files,
                edge_snapshot.protocol_evidence_bytes,
                edge_snapshot.claim_receipt_bytes,
            )
        except (EvidenceBundleError, PairedChangeVerificationError) as exc:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.EDGE_BINDING_MISMATCH,
                f"edge[{index:06d}]",
            ) from exc
        verified.append(child)
    return tuple(verified)


def _verify_declared_children(
    evidence: FirstBadChainEvidenceV1,
    children: tuple[VerifiedPairedChangeV1, ...],
) -> None:
    for index, (declared, child) in enumerate(zip(evidence.edges, children, strict=True)):
        if (
            declared.bundle_manifest_sha256 != child.bundle.manifest_sha256
            or declared.protocol_evidence_sha256 != child.protocol_evidence_sha256
        ):
            _fail(FirstBadVerificationReason.DIGEST_MISMATCH, f"edge[{index:06d}] digest")
        if (
            declared.baseline_runtime_identity != child.evidence.baseline_state
            or declared.candidate_runtime_identity != child.evidence.candidate_state
            or declared.baseline_runtime_identity.version != declared.baseline_version
            or declared.candidate_runtime_identity.version != declared.candidate_version
        ):
            _fail(FirstBadVerificationReason.EDGE_BINDING_MISMATCH, f"edge[{index:06d}]")


def _verify_baseline_anchor(evidence: FirstBadChainEvidenceV1) -> None:
    first = evidence.edges[0]
    anchor = evidence.baseline_runtime_identity
    if (
        anchor != first.baseline_runtime_identity
        or anchor.version != evidence.catalog_versions[0]
        or evidence.baseline_version != anchor.version
    ):
        _fail(FirstBadVerificationReason.BASELINE_BINDING_MISMATCH, "baseline anchor")


def _verify_runtime_continuity(evidence: FirstBadChainEvidenceV1) -> None:
    for left, right in pairwise(evidence.edges):
        if left.candidate_runtime_identity != right.baseline_runtime_identity:
            _fail(FirstBadVerificationReason.EDGE_BINDING_MISMATCH, "runtime continuity")


def _verify_frozen_identity(
    evidence: FirstBadChainEvidenceV1,
    children: tuple[VerifiedPairedChangeV1, ...],
) -> None:
    for index, child in enumerate(children):
        baseline = child.evidence.baseline_state
        candidate = child.evidence.candidate_state
        if baseline.agent_name != evidence.agent_name or candidate.agent_name != evidence.agent_name:
            _fail(FirstBadVerificationReason.EDGE_BINDING_MISMATCH, f"edge[{index:06d}] agent")
        if (
            child.bundle.manifest.suite_sha256 != evidence.suite_sha256
            or child.bundle.manifest.config_sha256 != evidence.config_sha256
            or baseline.model != evidence.model_pin
            or candidate.model != evidence.model_pin
            or child.evidence.protocol_design_sha256 != evidence.protocol_design_sha256
        ):
            _fail(FirstBadVerificationReason.EDGE_BINDING_MISMATCH, f"edge[{index:06d}] frozen")


def _edge_summaries(
    evidence: FirstBadChainEvidenceV1,
    children: tuple[VerifiedPairedChangeV1, ...],
) -> tuple[FirstBadEdgeSummaryV1, ...]:
    summaries = tuple(
        derive_edge_summary(edge.index, edge.baseline_version, edge.candidate_version, child.receipt)
        for edge, child in zip(evidence.edges, children, strict=True)
    )
    attributable = [
        summary.index
        for summary in summaries
        if summary.classification is EdgeClassification.ATTRIBUTABLE_CHANGESET
    ]
    if attributable and attributable[0] != len(summaries) - 1:
        _fail(FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, "edge after attributable boundary")
    return summaries


def _condition(
    condition_type: FirstBadConditionType,
    status: ConditionStatus,
    reason: str,
) -> FirstBadConditionV1:
    return FirstBadConditionV1(type=condition_type, status=status, reason=reason)


def _conditions(
    evidence: FirstBadChainEvidenceV1,
    summaries: tuple[FirstBadEdgeSummaryV1, ...],
    claim: FirstBadClaimClass,
) -> tuple[FirstBadConditionV1, ...]:
    structural_types = (
        FirstBadConditionType.CATALOG_BOUND,
        FirstBadConditionType.BASELINE_ANCHORED,
        FirstBadConditionType.EDGES_CONTIGUOUS,
        FirstBadConditionType.VERSIONS_ORDERED,
        FirstBadConditionType.SUITE_FROZEN,
        FirstBadConditionType.CONFIG_FROZEN,
        FirstBadConditionType.DESIGN_FROZEN,
        FirstBadConditionType.EVERY_EDGE_VERIFIED,
    )
    result = [
        _condition(condition_type, ConditionStatus.TRUE, _TRUE_REASONS[condition_type])
        for condition_type in structural_types
    ]
    has_unresolved = any(
        summary.classification is EdgeClassification.UNRESOLVED for summary in summaries
    )
    if has_unresolved:
        result.append(
            _condition(
                FirstBadConditionType.PREFIX_NO_REGRESSION,
                ConditionStatus.UNKNOWN,
                _PREFIX_UNKNOWN_REASON,
            )
        )
    else:
        result.append(
            _condition(
                FirstBadConditionType.PREFIX_NO_REGRESSION,
                ConditionStatus.TRUE,
                _TRUE_REASONS[FirstBadConditionType.PREFIX_NO_REGRESSION],
            )
        )
    if claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD:
        result.append(
            _condition(
                FirstBadConditionType.BOUNDARY_ATTRIBUTABLE,
                ConditionStatus.TRUE,
                _BOUNDARY_TRUE_REASON,
            )
        )
    elif claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND:
        result.append(
            _condition(
                FirstBadConditionType.BOUNDARY_ATTRIBUTABLE,
                ConditionStatus.FALSE,
                _BOUNDARY_FALSE_REASON,
            )
        )
    else:
        result.append(
            _condition(
                FirstBadConditionType.BOUNDARY_ATTRIBUTABLE,
                ConditionStatus.UNKNOWN,
                _BOUNDARY_UNKNOWN_REASON,
            )
        )
    return tuple(result)


def verify_first_bad_snapshot(snapshot: FirstBadPackageSnapshot) -> FirstBadReceiptV1:
    evidence = snapshot.evidence
    _verify_protocol_identity(evidence)
    _verify_digests(evidence)
    _verify_catalog(evidence)
    _verify_layout(snapshot)
    children = _verify_children(snapshot)
    _verify_declared_children(evidence, children)
    _verify_baseline_anchor(evidence)
    _verify_runtime_continuity(evidence)
    _verify_frozen_identity(evidence, children)
    summaries = _edge_summaries(evidence, children)
    claim, boundary_version, boundary_index = derive_chain_claim(evidence.catalog_versions, summaries)
    receipt = FirstBadReceiptV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        chain_sha256=evidence.chain_sha256,
        catalog_sha256=evidence.catalog_sha256,
        conditions=_conditions(evidence, summaries, claim),
        edges=summaries,
        claim=claim,
        boundary_version=boundary_version,
        boundary_edge_index=boundary_index,
    )
    if snapshot.stored_receipt is not None and snapshot.stored_receipt != receipt:
        _fail(FirstBadVerificationReason.CHAIN_CLAIM_MISMATCH, "chain-receipt.json")
    return receipt


def verify_first_bad(chain_path: Path) -> FirstBadReceiptV1:
    return verify_first_bad_snapshot(read_first_bad_package(chain_path))
