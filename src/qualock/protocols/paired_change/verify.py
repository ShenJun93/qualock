"""Offline structural verification and recomputable paired-change claims."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

import qualock
from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.evidence.bundle_models import EvidenceManifest, VerifiedEvidenceBundle
from qualock.evidence.verify import (
    _read_evidence_bundle_payloads,
    verify_evidence_bundle_payloads,
)

from .claims import derive_canary_claim
from .conditions import evaluate_canary_conditions, evaluate_qualification_conditions
from .design import PROTOCOL_DIGEST, derive_changeset
from .fingerprint import digest_model
from .io import (
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
    _parse_model,
    _preflight_protocol_payload,
    _protocol_payload_value,
    _read_protocol_companion,
)
from .models import (
    AgentDependencyStateV1,
    ClaimReceiptV1,
    ProtocolEvidenceV1,
)


@dataclass(frozen=True)
class VerifiedPairedChangeV1:
    bundle: VerifiedEvidenceBundle
    evidence: ProtocolEvidenceV1
    receipt: ClaimReceiptV1
    protocol_evidence_sha256: str


def _fail(reason: PairedChangeVerificationReason, field: str) -> None:
    raise PairedChangeVerificationError(reason, field)


def _identity_matches(
    state: AgentDependencyStateV1, manifest: EvidenceManifest, *, baseline: bool
) -> bool:
    identity = manifest.baseline_identity if baseline else manifest.candidate_identity
    return (
        state.agent_name == identity.name
        and state.version == identity.version
        and state.binary_sha256 == identity.binary_sha256
        and state.support_sha256 == identity.support_sha256
        and state.model.model_dump(mode="json") == manifest.model.model_dump(mode="json")
    )


def _verify_protocol_identity(evidence: ProtocolEvidenceV1) -> None:
    if (
        evidence.protocol_id != "paired-change/v1"
        or evidence.protocol_digest != PROTOCOL_DIGEST
        or evidence.protocol_design.protocol_id != "paired-change/v1"
        or evidence.protocol_design.protocol_digest != PROTOCOL_DIGEST
    ):
        _fail(PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL, "protocol")
    if digest_model(evidence.protocol_design) != evidence.protocol_design_sha256:
        _fail(PairedChangeVerificationReason.DIGEST_MISMATCH, "protocol_design_sha256")
    if any(
        attempt.protocol_design_sha256 != evidence.protocol_design_sha256
        for canary in evidence.canaries
        for pair in canary.pairs
        for attempt in pair.attempts
    ):
        _fail(PairedChangeVerificationReason.DIGEST_MISMATCH, "attempt protocol design")


def _verify_evidence_binding(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> None:
    manifest = bundle.manifest
    if (
        evidence.evidence_manifest_sha256 != bundle.manifest_sha256
        or evidence.qualification_id != manifest.qualification_id
        or evidence.qualification_id != bundle.report.qualification_id
        or evidence.protocol_design.suite_sha256 != manifest.suite_sha256
        or evidence.protocol_design.config_sha256 != manifest.config_sha256
    ):
        _fail(PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH, "bundle binding")

    public_canaries = {item.canary_id: item for item in bundle.canaries.canaries}
    design_canaries = {item.canary_id: item for item in evidence.protocol_design.canaries}
    manifest_canaries = bundle.manifest.canaries
    if set(design_canaries) != set(public_canaries) or set(design_canaries) != set(
        manifest_canaries
    ):
        _fail(PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH, "canary set")
    for canary_id, design in design_canaries.items():
        public = public_canaries[canary_id]
        stored = manifest_canaries[canary_id]
        if not (
            design.canary_fingerprint_sha256
            == public.canary_fingerprint_sha256
            == stored.canary_fingerprint_sha256
            and public.repetitions
            == stored.repetitions
            == evidence.protocol_design.repetitions
        ):
            _fail(
                PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH,
                "canary fingerprint or repetitions",
            )


def _verify_state_binding(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> None:
    if not _identity_matches(evidence.baseline_state, bundle.manifest, baseline=True):
        _fail(PairedChangeVerificationReason.STATE_BINDING_MISMATCH, "baseline_state")
    if not _identity_matches(evidence.candidate_state, bundle.manifest, baseline=False):
        _fail(PairedChangeVerificationReason.STATE_BINDING_MISMATCH, "candidate_state")
    expected_changeset = derive_changeset(evidence.baseline_state, evidence.candidate_state)
    if digest_model(expected_changeset) != evidence.changeset_sha256:
        _fail(PairedChangeVerificationReason.DIGEST_MISMATCH, "changeset_sha256")


def _verify_pair_layout(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> None:
    public_executions = {item.canary_id: item for item in bundle.report.executions}
    run_canaries = {item.canary_id: item for item in evidence.canaries}
    expected_run_canaries = {
        canary_id for canary_id, execution in public_executions.items() if execution.attempts
    }
    if set(run_canaries) != expected_run_canaries:
        _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "run canary set")

    observed_order: list[tuple[str, str, int]] = []
    repetitions = evidence.protocol_design.repetitions
    expected_repetitions = set(range(1, repetitions + 1))
    expected_slots = {
        (side, repetition)
        for repetition in expected_repetitions
        for side in ("baseline", "candidate")
    }
    public_canaries = {item.canary_id: item for item in bundle.canaries.canaries}

    for order_item in bundle.report.run_order:
        if order_item[0] not in public_executions:
            _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "run_order canary")

    for canary_id, run_canary in run_canaries.items():
        if (
            run_canary.canary_fingerprint_sha256
            != public_canaries[canary_id].canary_fingerprint_sha256
        ):
            _fail(
                PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH,
                "run canary fingerprint",
            )
        if len(run_canary.pairs) != repetitions or {
            pair.repetition for pair in run_canary.pairs
        } != expected_repetitions:
            _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "repetitions")

        run_attempts: dict[tuple[str, int], str] = {}
        for pair in run_canary.pairs:
            pair_slots = {(attempt.side, attempt.repetition) for attempt in pair.attempts}
            if (
                len(pair.attempts) != 2
                or pair_slots
                != {("baseline", pair.repetition), ("candidate", pair.repetition)}
            ):
                _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "pair slots")
            for attempt in pair.attempts:
                key = (attempt.side, attempt.repetition)
                if key in run_attempts:
                    _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "duplicate slot")
                run_attempts[key] = attempt.events_sha256
                observed_order.append((canary_id, attempt.side, attempt.repetition))
        if set(run_attempts) != expected_slots:
            _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "attempt slots")

        public_attempts = {
            (attempt.side, attempt.repetition): attempt.events_sha256
            for attempt in public_executions[canary_id].attempts
        }
        if run_attempts != public_attempts:
            _fail(PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH, "events_sha256")

    if tuple(observed_order) != bundle.report.run_order:
        _fail(PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "run_order")


def _build_receipt(
    bundle: VerifiedEvidenceBundle,
    evidence: ProtocolEvidenceV1,
    *,
    protocol_evidence_sha256: str,
) -> ClaimReceiptV1:
    qualification_conditions = evaluate_qualification_conditions(bundle, evidence)
    claims = []
    for design_canary in sorted(
        evidence.protocol_design.canaries, key=lambda item: item.canary_id
    ):
        conditions = evaluate_canary_conditions(bundle, evidence, design_canary.canary_id)
        claims.append(
            derive_canary_claim(
                bundle,
                evidence,
                design_canary.canary_id,
                qualification_conditions,
                conditions,
            )
        )
    return ClaimReceiptV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest=evidence.protocol_digest,
        qualification_id=evidence.qualification_id,
        evidence_manifest_sha256=bundle.manifest_sha256,
        protocol_evidence_sha256=protocol_evidence_sha256,
        baseline_state_sha256=digest_model(evidence.baseline_state),
        candidate_state_sha256=digest_model(evidence.candidate_state),
        changeset_sha256=evidence.changeset_sha256,
        qualification_conditions=qualification_conditions,
        canary_claims=tuple(claims),
        verifier_name="qualock",
        verifier_version=qualock.__version__,
    )


def verify_paired_change_payloads(
    bundle_files: Mapping[str, bytes],
    protocol_evidence_bytes: bytes,
    claim_receipt_bytes: bytes | None,
) -> VerifiedPairedChangeV1:
    bundle = verify_evidence_bundle_payloads(bundle_files)
    value = _protocol_payload_value(protocol_evidence_bytes)
    _preflight_protocol_payload(value)
    try:
        evidence = ProtocolEvidenceV1.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
            "protocol-evidence.json",
        ) from exc
    stored = (
        None
        if claim_receipt_bytes is None
        else _parse_model(
            claim_receipt_bytes,
            ClaimReceiptV1,
            PairedChangeVerificationReason.MALFORMED_RECEIPT,
            "claim-receipt.json",
        )
    )
    _verify_protocol_identity(evidence)
    _verify_evidence_binding(bundle, evidence)
    _verify_state_binding(bundle, evidence)
    _verify_pair_layout(bundle, evidence)

    receipt = _build_receipt(
        bundle,
        evidence,
        protocol_evidence_sha256=hashlib.sha256(protocol_evidence_bytes).hexdigest(),
    )
    if stored is not None and stored != receipt:
        _fail(PairedChangeVerificationReason.CLAIM_MISMATCH, "claim-receipt.json")
    return VerifiedPairedChangeV1(
        bundle=bundle,
        evidence=evidence,
        receipt=receipt,
        protocol_evidence_sha256=receipt.protocol_evidence_sha256,
    )


def verify_paired_change_details(
    bundle_path: Path, protocol_path: Path
) -> VerifiedPairedChangeV1:
    bundle_files = _read_evidence_bundle_payloads(bundle_path)
    _evidence, protocol_evidence_bytes, stored = _read_protocol_companion(protocol_path)
    stored_bytes = (
        None
        if stored is None
        else canonical_json_file_bytes(stored.model_dump(mode="json"))
    )
    return verify_paired_change_payloads(
        bundle_files, protocol_evidence_bytes, stored_bytes
    )


def verify_paired_change(bundle_path: Path, protocol_path: Path) -> ClaimReceiptV1:
    return verify_paired_change_details(bundle_path, protocol_path).receipt
