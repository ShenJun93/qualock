"""Secure companion I/O for paired-change/v1 offline verification."""

from __future__ import annotations

import errno
import json
import os
import stat
from enum import Enum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from qualock.evidence.bundle_io import (
    _open_validated_regular_file,
    canonical_json_file_bytes,
)
from qualock.evidence.bundle_models import EvidenceBundleError

from .models import ClaimReceiptV1, ProtocolEvidenceV1

PROTOCOL_EVIDENCE_FILENAME = "protocol-evidence.json"
CLAIM_RECEIPT_FILENAME = "claim-receipt.json"
PROTOCOL_EVIDENCE_MAX_BYTES = 32 * 1024 * 1024
CLAIM_RECEIPT_MAX_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_NAMES = frozenset({PROTOCOL_EVIDENCE_FILENAME, CLAIM_RECEIPT_FILENAME})


class PairedChangeVerificationReason(str, Enum):
    MALFORMED_PROTOCOL_EVIDENCE = "malformed_protocol_evidence"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    EVIDENCE_BINDING_MISMATCH = "evidence_binding_mismatch"
    STATE_BINDING_MISMATCH = "state_binding_mismatch"
    PAIR_LAYOUT_MISMATCH = "pair_layout_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED_RECEIPT = "malformed_receipt"
    CLAIM_MISMATCH = "claim_mismatch"


class PairedChangeVerificationError(ValueError):
    def __init__(self, reason: PairedChangeVerificationReason, field: str) -> None:
        super().__init__(f"paired-change verification {reason.value}: {field}")
        self.reason = reason
        self.field = field


ModelT = TypeVar("ModelT", bound=BaseModel)


def _translate_io_error(
    reason: PairedChangeVerificationReason, field: str, exc: BaseException
) -> PairedChangeVerificationError:
    return PairedChangeVerificationError(reason, field)


def _read_bounded(
    root: Path,
    name: str,
    *,
    max_bytes: int,
    reason: PairedChangeVerificationReason,
    optional: bool = False,
) -> bytes | None:
    try:
        handle = _open_validated_regular_file(root, name)
    except EvidenceBundleError as exc:
        cause = exc.__cause__
        if (
            optional
            and exc.label == name
            and isinstance(cause, OSError)
            and cause.errno == errno.ENOENT
        ):
            return None
        raise _translate_io_error(reason, name, exc) from exc
    try:
        try:
            size = os.fstat(handle.fileno()).st_size
        except OSError as exc:
            raise _translate_io_error(reason, name, exc) from exc
        if size > max_bytes:
            raise PairedChangeVerificationError(reason, name)
        data = bytearray()
        while True:
            try:
                chunk = handle.read(_CHUNK_BYTES)
            except OSError as exc:
                raise _translate_io_error(reason, name, exc) from exc
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise PairedChangeVerificationError(reason, name)
        return bytes(data)
    finally:
        handle.close()


def _parse_model(
    payload: bytes, model_type: type[ModelT], reason: PairedChangeVerificationReason, field: str
) -> ModelT:
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
        return model_type.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise PairedChangeVerificationError(reason, field) from exc


def _inspect_receipt_inventory(root: Path) -> None:
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol directory"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol directory"
        )
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name not in _ALLOWED_NAMES:
                    raise PairedChangeVerificationError(
                        PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol inventory"
                    )
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise PairedChangeVerificationError(
                        PairedChangeVerificationReason.MALFORMED_RECEIPT, entry.name
                    )
    except PairedChangeVerificationError:
        raise
    except OSError as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol directory"
        ) from exc


def _protocol_payload_value(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
            PROTOCOL_EVIDENCE_FILENAME,
        ) from exc


def _preflight_protocol_payload(value: object) -> None:
    if not isinstance(value, dict):
        return
    if (
        ("schema_version" in value and value["schema_version"] != 1)
        or ("protocol_id" in value and value["protocol_id"] != "paired-change/v1")
    ):
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL, "protocol"
        )
    design = value.get("protocol_design")
    if isinstance(design, dict) and (
        ("schema_version" in design and design["schema_version"] != 1)
        or ("protocol_id" in design and design["protocol_id"] != "paired-change/v1")
    ):
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL, "protocol_design"
        )
    canaries = value.get("canaries")
    if not isinstance(canaries, list):
        return
    for canary in canaries:
        if not isinstance(canary, dict):
            continue
        pairs = canary.get("pairs")
        if not isinstance(pairs, list):
            continue
        seen_repetitions: set[int] = set()
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            repetition = pair.get("repetition")
            if isinstance(repetition, int) and repetition > 0:
                if repetition in seen_repetitions:
                    raise PairedChangeVerificationError(
                        PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                        "duplicate pair repetition",
                    )
                seen_repetitions.add(repetition)
            attempts = pair.get("attempts")
            if not isinstance(attempts, list):
                continue
            if len(attempts) != 2:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "pair slots"
                )
            if not all(isinstance(attempt, dict) for attempt in attempts):
                continue
            sides = {attempt.get("side") for attempt in attempts}
            attempt_repetitions = {attempt.get("repetition") for attempt in attempts}
            if sides != {"baseline", "candidate"}:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH, "pair sides"
                )
            if isinstance(repetition, int) and attempt_repetitions != {repetition}:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                    "pair repetition",
                )


def _read_protocol_evidence_with_bytes(
    protocol_path: Path,
) -> tuple[ProtocolEvidenceV1, bytes]:
    payload = _read_bounded(
        protocol_path,
        PROTOCOL_EVIDENCE_FILENAME,
        max_bytes=PROTOCOL_EVIDENCE_MAX_BYTES,
        reason=PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
    )
    assert payload is not None
    value = _protocol_payload_value(payload)
    _preflight_protocol_payload(value)
    try:
        evidence = ProtocolEvidenceV1.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
            PROTOCOL_EVIDENCE_FILENAME,
        ) from exc
    return evidence, payload


def read_protocol_evidence(protocol_path: Path) -> ProtocolEvidenceV1:
    evidence, _payload = _read_protocol_evidence_with_bytes(protocol_path)
    return evidence


def read_claim_receipt(protocol_path: Path) -> ClaimReceiptV1 | None:
    payload = _read_bounded(
        protocol_path,
        CLAIM_RECEIPT_FILENAME,
        max_bytes=CLAIM_RECEIPT_MAX_BYTES,
        reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
        optional=True,
    )
    if payload is None:
        return None
    _inspect_receipt_inventory(protocol_path)
    return _parse_model(
        payload,
        ClaimReceiptV1,
        PairedChangeVerificationReason.MALFORMED_RECEIPT,
        CLAIM_RECEIPT_FILENAME,
    )


def _validate_write_root(protocol_path: Path) -> None:
    try:
        info = os.lstat(protocol_path)
    except OSError as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol directory"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, "protocol directory"
        )


def write_claim_receipt(protocol_path: Path, receipt: ClaimReceiptV1) -> Path:
    _validate_write_root(protocol_path)
    target = protocol_path / CLAIM_RECEIPT_FILENAME
    payload = canonical_json_file_bytes(receipt.model_dump(mode="json"))
    try:
        if os.name != "nt":
            nofollow = getattr(os, "O_NOFOLLOW", None)
            directory = getattr(os, "O_DIRECTORY", None)
            if nofollow is None or directory is None:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.MALFORMED_RECEIPT,
                    "protocol directory",
                )
            root_fd = os.open(protocol_path, os.O_RDONLY | directory | nofollow)
            try:
                fd = os.open(
                    CLAIM_RECEIPT_FILENAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                    0o600,
                    dir_fd=root_fd,
                )
            finally:
                os.close(root_fd)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
        else:
            with target.open("xb") as handle:
                handle.write(payload)
    except (OSError, FileExistsError) as exc:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.MALFORMED_RECEIPT, CLAIM_RECEIPT_FILENAME
        ) from exc
    return target
