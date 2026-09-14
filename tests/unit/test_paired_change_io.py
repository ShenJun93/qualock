"""RED tests for secure, offline, no-follow paired-change companion I/O.

`qualock.protocols.paired_change.io` does not exist yet (Task 9 Step 1). These
tests specify the untrusted-filesystem posture required of
`read_protocol_evidence`, `read_claim_receipt`, and `write_claim_receipt`
before any implementation exists: no symlink is ever followed, no
non-regular file is ever read, every read is bounded from opened-handle
metadata rather than a prior check-then-read stat, and the fixed companion
inventory is exactly `protocol-evidence.json` plus optional
`claim-receipt.json`. Collection failure because `io.py` is missing is
acceptable RED evidence for this phase.
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.protocols.paired_change.io import (
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
    read_claim_receipt,
    read_protocol_evidence,
    write_claim_receipt,
)
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    CanaryProtocolDesignV1,
    CanaryRunEvidenceV1,
    ClaimReceiptV1,
    ModelDeclarationV1,
    ProtocolDesignV1,
    ProtocolEvidenceV1,
)

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only filesystem shape")
_PROTOCOL_EVIDENCE_FILENAME = "protocol-evidence.json"
_CLAIM_RECEIPT_FILENAME = "claim-receipt.json"
_OVERSIZED_BYTES = 64 * 1024 * 1024


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _protocol_design() -> ProtocolDesignV1:
    return ProtocolDesignV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest=_sha("protocol-digest"),
        suite_sha256=_sha("suite"),
        config_sha256=_sha("config"),
        repetitions=1,
        order_policy="alternating-v1",
        lifecycle="FRESH",
        canaries=(
            CanaryProtocolDesignV1(
                canary_id="c1",
                canary_fingerprint_sha256=_sha("c1-fingerprint"),
            ),
        ),
    )


def _agent_state(label: str) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name="claude",
        version="1.0.0",
        binary_sha256=_sha(f"{label}-binary"),
        support_sha256=None,
        model=ModelDeclarationV1(id="claude-sonnet-5", snapshot=None, reasoning_effort="medium"),
    )


def _protocol_evidence() -> ProtocolEvidenceV1:
    design = _protocol_design()
    return ProtocolEvidenceV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest=design.protocol_digest,
        protocol_design=design,
        protocol_design_sha256=_sha("design"),
        qualification_id="q1",
        evidence_manifest_sha256=_sha("manifest"),
        baseline_state=_agent_state("baseline"),
        candidate_state=_agent_state("candidate"),
        changeset_sha256=_sha("changeset"),
        canaries=(
            CanaryRunEvidenceV1(
                canary_id="c1",
                canary_fingerprint_sha256=_sha("c1-fingerprint"),
                prepared_target_sha256=None,
                pairs=(),
            ),
        ),
    )


def _claim_receipt() -> ClaimReceiptV1:
    return ClaimReceiptV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest=_sha("protocol-digest"),
        qualification_id="q1",
        evidence_manifest_sha256=_sha("manifest"),
        protocol_evidence_sha256=_sha("protocol-evidence"),
        baseline_state_sha256=_sha("baseline"),
        candidate_state_sha256=_sha("candidate"),
        changeset_sha256=_sha("changeset"),
        qualification_conditions=(),
        canary_claims=(),
        verifier_name="qualock",
        verifier_version="0.0.1",
    )


def _write_protocol_evidence_file(protocol_path: Path) -> bytes:
    protocol_path.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_file_bytes(_protocol_evidence().model_dump(mode="json"))
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).write_bytes(payload)
    return payload


def _truncate_to(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.truncate(size)


# --- read_protocol_evidence ----------------------------------------------------


def test_read_protocol_evidence_parses_valid_companion(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    assert read_protocol_evidence(protocol_path) == _protocol_evidence()


@_POSIX_ONLY
def test_read_protocol_evidence_rejects_protocol_dir_symlink(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    _write_protocol_evidence_file(real_dir)
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(link_dir)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


@_POSIX_ONLY
def test_read_protocol_evidence_rejects_symlinked_payload(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).symlink_to(outside)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


@_POSIX_ONLY
def test_read_protocol_evidence_rejects_non_regular_file(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    os.mkfifo(protocol_path / _PROTOCOL_EVIDENCE_FILENAME)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


def test_read_protocol_evidence_rejects_oversized_file(tmp_path: Path) -> None:
    # Rejection must come from handle-derived size metadata, not from
    # buffering the whole (potentially huge) payload into memory first.
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    _truncate_to(protocol_path / _PROTOCOL_EVIDENCE_FILENAME, _OVERSIZED_BYTES)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


def test_read_protocol_evidence_rejects_missing_file(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


def test_read_protocol_evidence_rejects_malformed_utf8(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


def test_read_protocol_evidence_rejects_malformed_json(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).write_bytes(b"{not valid json")
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


def test_read_protocol_evidence_rejects_schema_violation(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).write_bytes(b'{"schema_version":2}\n')
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL


@_POSIX_ONLY
def test_read_protocol_evidence_rejects_replacement_to_link_race(tmp_path: Path) -> None:
    # A valid regular file existed at open-attempt time; it is then replaced
    # by a symlink before the read completes. Detection must come from the
    # metadata of the actually-opened handle (fstat), never from a
    # check-then-read stat performed before opening.
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).unlink()
    (protocol_path / _PROTOCOL_EVIDENCE_FILENAME).symlink_to(outside)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_protocol_evidence(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE


# --- read_claim_receipt ---------------------------------------------------------


def test_read_claim_receipt_returns_none_when_absent(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    assert read_claim_receipt(protocol_path) is None


def test_read_claim_receipt_returns_stored_receipt(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    receipt = _claim_receipt()
    (protocol_path / _CLAIM_RECEIPT_FILENAME).write_bytes(
        canonical_json_file_bytes(receipt.model_dump(mode="json"))
    )
    assert read_claim_receipt(protocol_path) == receipt


@_POSIX_ONLY
def test_read_claim_receipt_rejects_symlinked_payload(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (protocol_path / _CLAIM_RECEIPT_FILENAME).symlink_to(outside)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


@_POSIX_ONLY
def test_read_claim_receipt_rejects_non_regular_file(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    os.mkfifo(protocol_path / _CLAIM_RECEIPT_FILENAME)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_read_claim_receipt_rejects_oversized_file(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    _truncate_to(protocol_path / _CLAIM_RECEIPT_FILENAME, _OVERSIZED_BYTES)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_read_claim_receipt_rejects_malformed_utf8(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    (protocol_path / _CLAIM_RECEIPT_FILENAME).write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_read_claim_receipt_rejects_malformed_json(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    (protocol_path / _CLAIM_RECEIPT_FILENAME).write_bytes(b"{not valid json")
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_read_claim_receipt_rejects_schema_violation(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    (protocol_path / _CLAIM_RECEIPT_FILENAME).write_bytes(b'{"schema_version":2}\n')
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


@_POSIX_ONLY
def test_read_claim_receipt_rejects_replacement_to_link_race(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    receipt_path = protocol_path / _CLAIM_RECEIPT_FILENAME
    receipt_path.write_bytes(canonical_json_file_bytes(_claim_receipt().model_dump(mode="json")))
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    receipt_path.unlink()
    receipt_path.symlink_to(outside)
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


def test_read_claim_receipt_rejects_unexpected_companion_file(tmp_path: Path) -> None:
    # The fixed companion inventory is exactly protocol-evidence.json plus
    # optional claim-receipt.json. Any other entry must fail closed once a
    # stored receipt is being checked.
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    (protocol_path / _CLAIM_RECEIPT_FILENAME).write_bytes(
        canonical_json_file_bytes(_claim_receipt().model_dump(mode="json"))
    )
    (protocol_path / "unexpected.json").write_bytes(b"{}")
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        read_claim_receipt(protocol_path)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT


# --- write_claim_receipt ---------------------------------------------------------


def test_write_claim_receipt_produces_canonical_deterministic_bytes(tmp_path: Path) -> None:
    receipt = _claim_receipt()
    protocol_path_a = tmp_path / "a"
    protocol_path_a.mkdir()
    protocol_path_b = tmp_path / "b"
    protocol_path_b.mkdir()

    path_a = write_claim_receipt(protocol_path_a, receipt)
    path_b = write_claim_receipt(protocol_path_b, receipt)

    assert path_a.name == _CLAIM_RECEIPT_FILENAME
    assert path_b.name == _CLAIM_RECEIPT_FILENAME
    bytes_a = path_a.read_bytes()
    bytes_b = path_b.read_bytes()
    assert bytes_a == bytes_b
    assert bytes_a == canonical_json_file_bytes(receipt.model_dump(mode="json"))


def test_write_claim_receipt_round_trips_through_read_claim_receipt(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    _write_protocol_evidence_file(protocol_path)
    receipt = _claim_receipt()
    write_claim_receipt(protocol_path, receipt)
    assert read_claim_receipt(protocol_path) == receipt


def test_write_claim_receipt_never_overwrites_existing_receipt(tmp_path: Path) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    write_claim_receipt(protocol_path, _claim_receipt())
    original_bytes = (protocol_path / _CLAIM_RECEIPT_FILENAME).read_bytes()

    other_receipt = _claim_receipt().model_copy(update={"verifier_version": "0.0.2"})
    with pytest.raises(PairedChangeVerificationError) as exc_info:
        write_claim_receipt(protocol_path, other_receipt)
    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT

    assert (protocol_path / _CLAIM_RECEIPT_FILENAME).read_bytes() == original_bytes


def test_verification_reason_inventory_is_exact() -> None:
    assert {item.name for item in PairedChangeVerificationReason} == {
        "MALFORMED_PROTOCOL_EVIDENCE",
        "UNSUPPORTED_PROTOCOL",
        "EVIDENCE_BINDING_MISMATCH",
        "STATE_BINDING_MISMATCH",
        "PAIR_LAYOUT_MISMATCH",
        "DIGEST_MISMATCH",
        "MALFORMED_RECEIPT",
        "CLAIM_MISMATCH",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only O_NOFOLLOW contract")
def test_write_claim_receipt_fails_closed_without_o_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol_path = tmp_path / "bundle.paired-change-v1"
    protocol_path.mkdir()
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        write_claim_receipt(protocol_path, _claim_receipt())

    assert exc_info.value.reason is PairedChangeVerificationReason.MALFORMED_RECEIPT
    assert not (protocol_path / _CLAIM_RECEIPT_FILENAME).exists()


def test_windows_root_open_failure_maps_to_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.protocols.paired_change import io as paired_io

    monkeypatch.setattr(paired_io.os, "name", "nt")

    def fail_open(*args: object, **kwargs: object) -> int:
        raise OSError(2, "missing protocol directory")

    monkeypatch.setattr(paired_io, "_win_create_handle", fail_open)

    with pytest.raises(PairedChangeVerificationError) as exc_info:
        paired_io.read_protocol_evidence(tmp_path / "missing")

    assert (
        exc_info.value.reason
        is PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE
    )


def test_windows_receipt_creation_uses_pinned_root_relative_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io as stdlib_io

    from qualock.protocols.paired_change import io as paired_io

    session = paired_io._PinnedProtocolDirectory(
        tmp_path,
        root_reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
    )
    session._win_handle = 123
    session._win_final = r"\?\C:\pinned"
    monkeypatch.setattr(paired_io.os, "name", "nt")
    calls: list[tuple[int, str]] = []

    def relative_create(root_handle: int, name: str):
        calls.append((root_handle, name))
        return stdlib_io.BytesIO()

    monkeypatch.setattr(
        paired_io, "_win_create_relative_file", relative_create, raising=False
    )
    monkeypatch.setattr(
        paired_io,
        "_win_create_handle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("receipt creation reopened a pathname")
        ),
    )

    session.create_receipt(b"receipt")

    assert calls == [(123, _CLAIM_RECEIPT_FILENAME)]


def test_windows_api_signature_configuration_is_pointer_safe() -> None:
    from types import SimpleNamespace

    from qualock.protocols.paired_change import io as paired_io

    class FakeFunction:
        argtypes: object = None
        restype: object = None

    kernel32 = SimpleNamespace(
        CreateFileW=FakeFunction(),
        GetFileInformationByHandle=FakeFunction(),
        GetFinalPathNameByHandleW=FakeFunction(),
        CloseHandle=FakeFunction(),
    )
    ntdll = SimpleNamespace(
        NtCreateFile=FakeFunction(),
        RtlNtStatusToDosError=FakeFunction(),
    )

    paired_io._configure_windows_api(kernel32, ntdll)

    assert kernel32.CreateFileW.restype is paired_io.wintypes.HANDLE
    assert kernel32.CloseHandle.argtypes == [paired_io.wintypes.HANDLE]
    assert ntdll.NtCreateFile.argtypes[0] == paired_io.ctypes.POINTER(
        paired_io.wintypes.HANDLE
    )
