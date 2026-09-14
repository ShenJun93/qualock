"""Task 9 verifier contract: structural failures stay errors, claims recompute offline."""

import hashlib
import json
import shutil
import socket
import subprocess
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
from qualock.protocols.paired_change.verify import verify_paired_change
from tests.unit.test_evidence_export import _create_synthetic_qualification

SHA_F = "f" * 64


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
