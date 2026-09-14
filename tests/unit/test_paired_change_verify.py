"""Task 9 verifier contract: structural failures stay errors, claims recompute offline."""

import hashlib
import json
import shutil
import socket
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.evidence.export import export_evidence_bundle
from qualock.protocols.paired_change.io import (
    CLAIM_RECEIPT_FILENAME,
    PROTOCOL_EVIDENCE_FILENAME,
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
    read_protocol_evidence,
)
from qualock.protocols.paired_change.models import ClaimClass
from qualock.protocols.paired_change.verify import (
    verify_paired_change,
    verify_paired_change_details,
    verify_paired_change_payloads,
)
from tests.unit.test_evidence_export import _create_synthetic_qualification

SHA_F = "f" * 64
GOLDEN_VECTOR = (
    Path(__file__).parents[1] / "fixtures" / "paired_change_v1" / "attributable-clean"
)


def _exported(tmp_path: Path):
    project_root = tmp_path / "project"
    qualification_id = "check-verify-claim"
    _create_synthetic_qualification(
        project_root, qualification_id=qualification_id, events_jsonl="verify-events"
    )
    exported = export_evidence_bundle(
        project_root, qualification_id, tmp_path / "bundle"
    )
    assert exported.protocol_path is not None
    return project_root, exported.path, exported.protocol_path


def _replace_evidence(protocol_path: Path, **updates: object) -> None:
    evidence = read_protocol_evidence(protocol_path).model_copy(update=updates)
    (protocol_path / PROTOCOL_EVIDENCE_FILENAME).write_bytes(
        canonical_json_file_bytes(evidence.model_dump(mode="json"))
    )


def _bundle_payloads(bundle_path: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in bundle_path.iterdir()}


def test_verify_paired_change_payloads_returns_recomputed_details() -> None:
    bundle_path = GOLDEN_VECTOR / "bundle"
    protocol_path = GOLDEN_VECTOR / "protocol"
    protocol_bytes = (protocol_path / PROTOCOL_EVIDENCE_FILENAME).read_bytes()

    verified = verify_paired_change_payloads(
        _bundle_payloads(bundle_path), protocol_bytes, None
    )

    assert verified.receipt == verify_paired_change(bundle_path, protocol_path)
    assert verified.evidence.candidate_state.version == "0.151.0"
    assert verified.protocol_evidence_sha256 == hashlib.sha256(protocol_bytes).hexdigest()


def test_verify_paired_change_details_returns_immutable_verified_result() -> None:
    bundle_path = GOLDEN_VECTOR / "bundle"
    protocol_path = GOLDEN_VECTOR / "protocol"

    verified = verify_paired_change_details(bundle_path, protocol_path)

    assert verified.receipt == verify_paired_change(bundle_path, protocol_path)
    with pytest.raises(FrozenInstanceError):
        verified.protocol_evidence_sha256 = SHA_F  # type: ignore[misc]


def test_verify_paired_change_details_delegates_exact_companion_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    receipt_path = protocol_path / CLAIM_RECEIPT_FILENAME
    receipt_value = verify_paired_change(bundle_path, protocol_path).model_dump(
        mode="json"
    )
    raw_receipt = json.dumps(receipt_value, indent=2).encode("utf-8")
    receipt_path.write_bytes(raw_receipt)
    raw_protocol = (protocol_path / PROTOCOL_EVIDENCE_FILENAME).read_bytes()
    captured: tuple[bytes, bytes | None] | None = None

    from qualock.protocols.paired_change import verify as paired_verify

    expected = paired_verify.verify_paired_change_payloads(
        _bundle_payloads(bundle_path), raw_protocol, raw_receipt
    )

    def capture_payloads(
        bundle_files: dict[str, bytes],
        protocol_evidence_bytes: bytes,
        claim_receipt_bytes: bytes | None,
    ):
        nonlocal captured
        captured = (protocol_evidence_bytes, claim_receipt_bytes)
        return expected

    monkeypatch.setattr(paired_verify, "verify_paired_change_payloads", capture_payloads)

    assert paired_verify.verify_paired_change_details(bundle_path, protocol_path) == expected
    assert captured == (raw_protocol, raw_receipt)


def test_verify_paired_change_payloads_preserves_stored_claim_mismatch() -> None:
    bundle_path = GOLDEN_VECTOR / "bundle"
    protocol_path = GOLDEN_VECTOR / "protocol"
    protocol_bytes = (protocol_path / PROTOCOL_EVIDENCE_FILENAME).read_bytes()
    receipt = verify_paired_change(bundle_path, protocol_path)
    stored = receipt.model_copy(update={"verifier_version": "tampered"})
    stored_bytes = canonical_json_file_bytes(stored.model_dump(mode="json"))

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change_payloads(
            _bundle_payloads(bundle_path), protocol_bytes, stored_bytes
        )
    assert exc_info.value.reason is PairedChangeVerificationReason.CLAIM_MISMATCH


def test_receipt_binds_canonical_protocol_evidence_file_bytes(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)

    receipt = verify_paired_change(bundle_path, protocol_path)
    protocol_bytes = (protocol_path / PROTOCOL_EVIDENCE_FILENAME).read_bytes()

    assert receipt.protocol_evidence_sha256 == hashlib.sha256(protocol_bytes).hexdigest()




def test_receipt_binds_actual_protocol_evidence_bytes(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    path = protocol_path / PROTOCOL_EVIDENCE_FILENAME
    value = json.loads(path.read_text(encoding="utf-8"))
    noncanonical = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(noncanonical)

    receipt = verify_paired_change(bundle_path, protocol_path)

    assert receipt.protocol_evidence_sha256 == hashlib.sha256(noncanonical).hexdigest()

def test_verify_paired_change_recomputes_deterministic_receipt(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)

    first = verify_paired_change(bundle_path, protocol_path)
    second = verify_paired_change(bundle_path, protocol_path)

    assert first == second
    assert first.qualification_id == "check-verify-claim"
    assert first.protocol_evidence_sha256
    assert first.canary_claims
    assert canonical_json_file_bytes(first.model_dump(mode="json")) == canonical_json_file_bytes(
        second.model_dump(mode="json")
    )


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"evidence_manifest_sha256": SHA_F}, PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH),
        ({"qualification_id": "other"}, PairedChangeVerificationReason.EVIDENCE_BINDING_MISMATCH),
    ],
)
def test_verify_paired_change_rejects_evidence_binding_mismatch(
    tmp_path: Path, updates: dict[str, object], reason: PairedChangeVerificationReason
) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    _replace_evidence(protocol_path, **updates)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is reason


def test_verify_paired_change_rejects_state_binding_mismatch(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    evidence = read_protocol_evidence(protocol_path)
    changed = evidence.baseline_state.model_copy(update={"version": "9.9.9"})
    _replace_evidence(protocol_path, baseline_state=changed)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.STATE_BINDING_MISMATCH


def test_verify_paired_change_rejects_impossible_pair_layout(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    evidence = read_protocol_evidence(protocol_path)
    canary = evidence.canaries[0].model_copy(update={"pairs": ()})
    _replace_evidence(protocol_path, canaries=(canary,))

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH


def test_verify_paired_change_rejects_design_digest_tamper(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    _replace_evidence(protocol_path, protocol_design_sha256=SHA_F)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.DIGEST_MISMATCH


def test_verify_paired_change_rejects_unsupported_protocol_digest(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    _replace_evidence(protocol_path, protocol_digest=SHA_F)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL


def test_verify_paired_change_rejects_malformed_stored_receipt(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    (protocol_path / CLAIM_RECEIPT_FILENAME).write_bytes(b"{not-json")

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_verify_paired_change_rejects_manually_upgraded_stored_claim(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    receipt = verify_paired_change(bundle_path, protocol_path)
    original = receipt.canary_claims[0]
    upgraded = original.model_copy(
        update={
            "claim": (
                ClaimClass.ATTRIBUTABLE_CHANGESET
                if original.claim is not ClaimClass.ATTRIBUTABLE_CHANGESET
                else ClaimClass.NO_REGRESSION_OBSERVED
            )
        }
    )
    stored = receipt.model_copy(update={"canary_claims": (upgraded,)})
    (protocol_path / CLAIM_RECEIPT_FILENAME).write_bytes(
        canonical_json_file_bytes(stored.model_dump(mode="json"))
    )

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.CLAIM_MISMATCH


def test_verify_paired_change_accepts_exact_stored_receipt(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    receipt = verify_paired_change(bundle_path, protocol_path)
    (protocol_path / CLAIM_RECEIPT_FILENAME).write_bytes(
        canonical_json_file_bytes(receipt.model_dump(mode="json"))
    )
    assert verify_paired_change(bundle_path, protocol_path) == receipt


def test_verify_paired_change_is_offline_and_project_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, bundle_path, protocol_path = _exported(tmp_path)
    shutil.rmtree(project_root)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("offline verifier attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    receipt = verify_paired_change(bundle_path, protocol_path)
    assert receipt.qualification_id == "check-verify-claim"


def test_verify_paired_change_rejects_changeset_digest_contradiction(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    _replace_evidence(protocol_path, changeset_sha256=SHA_F)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.DIGEST_MISMATCH


@pytest.mark.parametrize(
    "field,value",
    [("schema_version", 2), ("protocol_id", "paired-change/v2")],
)
def test_verify_paired_change_classifies_unsupported_protocol_before_model_validation(
    tmp_path: Path, field: str, value: object
) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    path = protocol_path / PROTOCOL_EVIDENCE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_bytes(canonical_json_file_bytes(payload))

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL


def test_verify_paired_change_classifies_duplicate_pair_as_layout_mismatch(tmp_path: Path) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    path = protocol_path / PROTOCOL_EVIDENCE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    pairs = payload["canaries"][0]["pairs"]
    pairs.append(dict(pairs[0]))
    path.write_bytes(canonical_json_file_bytes(payload))

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH


import os


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename semantics required")
def test_verify_paired_change_pins_companion_directory_across_receipt_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    expected = verify_paired_change(bundle_path, protocol_path)
    replacement_receipt = expected.model_copy(update={"verifier_version": "replacement"})

    from qualock.protocols.paired_change import io as paired_io

    original_preflight = paired_io._preflight_protocol_payload
    swapped = False

    def swap_after_protocol_read(value: object) -> None:
        nonlocal swapped
        original_preflight(value)
        if swapped:
            return
        swapped = True
        moved = protocol_path.with_name(protocol_path.name + ".moved")
        protocol_path.rename(moved)
        protocol_path.mkdir()
        (protocol_path / PROTOCOL_EVIDENCE_FILENAME).write_bytes(
            (moved / PROTOCOL_EVIDENCE_FILENAME).read_bytes()
        )
        (protocol_path / CLAIM_RECEIPT_FILENAME).write_bytes(
            canonical_json_file_bytes(replacement_receipt.model_dump(mode="json"))
        )

    monkeypatch.setattr(paired_io, "_preflight_protocol_payload", swap_after_protocol_read)

    assert verify_paired_change(bundle_path, protocol_path) == expected


def test_verify_paired_change_classifies_duplicate_canary_as_layout_mismatch(
    tmp_path: Path,
) -> None:
    _, bundle_path, protocol_path = _exported(tmp_path)
    path = protocol_path / PROTOCOL_EVIDENCE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["canaries"].append(dict(payload["canaries"][0]))
    path.write_bytes(canonical_json_file_bytes(payload))

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        verify_paired_change(bundle_path, protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH
