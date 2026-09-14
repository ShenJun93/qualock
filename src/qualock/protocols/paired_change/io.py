"""Secure companion I/O for paired-change/v1 offline verification."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
from ctypes import wintypes
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, TypeVar

from pydantic import BaseModel, ValidationError

from qualock.evidence.bundle_io import canonical_json_file_bytes

from .models import ClaimReceiptV1, ProtocolEvidenceV1

PROTOCOL_EVIDENCE_FILENAME = "protocol-evidence.json"
CLAIM_RECEIPT_FILENAME = "claim-receipt.json"
PROTOCOL_EVIDENCE_MAX_BYTES = 32 * 1024 * 1024
CLAIM_RECEIPT_MAX_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_NAMES = frozenset({PROTOCOL_EVIDENCE_FILENAME, CLAIM_RECEIPT_FILENAME})

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


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


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    )


def _win_last_error() -> int:
    return ctypes.get_last_error()  # type: ignore[attr-defined,no-any-return]


def _win_create_handle(
    path: str,
    *,
    access: int,
    share: int,
    creation: int,
    flags: int,
) -> int:
    handle = ctypes.windll.kernel32.CreateFileW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(path), access, share, None, creation, flags, None
    )
    if handle in (0, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return int(handle)


def _win_handle_info(handle: int) -> _ByHandleFileInformation:
    info = _ByHandleFileInformation()
    ok = ctypes.windll.kernel32.GetFileInformationByHandle(  # type: ignore[attr-defined]
        handle, ctypes.byref(info)
    )
    if not ok:
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return info


def _win_final_path(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(  # type: ignore[attr-defined]
        handle, buffer, len(buffer), 0
    )
    if length == 0 or length >= len(buffer):
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return str(buffer.value)


def _win_close_handle(handle: int) -> None:
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def _is_missing_error(exc: OSError) -> bool:
    return exc.errno == errno.ENOENT or getattr(exc, "winerror", None) in {2, 3}


def _translate_io_error(
    reason: PairedChangeVerificationReason, field: str, exc: BaseException
) -> PairedChangeVerificationError:
    return PairedChangeVerificationError(reason, field)


class _PinnedProtocolDirectory:
    """Retain one no-follow directory identity for a companion transaction."""

    def __init__(
        self,
        path: Path,
        *,
        root_reason: PairedChangeVerificationReason,
    ) -> None:
        self.path = path
        self.root_reason = root_reason
        self._posix_fd: int | None = None
        self._win_handle: int | None = None
        self._win_final: str | None = None

    def __enter__(self) -> Self:
        if os.name == "nt":
            handle: int | None = None
            try:
                handle = _win_create_handle(
                    str(self.path),
                    access=_GENERIC_READ,
                    share=_FILE_SHARE_READ,
                    creation=_OPEN_EXISTING,
                    flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
                )
                win_info = _win_handle_info(handle)
                if (
                    win_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or not win_info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
                ):
                    raise OSError(errno.EINVAL, "unsafe protocol directory")
                final_path = _win_final_path(handle).rstrip("\\")
            except OSError as exc:
                if handle is not None:
                    _win_close_handle(handle)
                raise _translate_io_error(
                    self.root_reason, "protocol directory", exc
                ) from exc
            self._win_handle = handle
            self._win_final = final_path
            return self

        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise PairedChangeVerificationError(
                self.root_reason, "protocol directory"
            )
        fd: int | None = None
        try:
            fd = os.open(self.path, os.O_RDONLY | directory | nofollow)
            posix_info = os.fstat(fd)
            if not stat.S_ISDIR(posix_info.st_mode):
                raise OSError(errno.ENOTDIR, "protocol root is not a directory")
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise _translate_io_error(
                self.root_reason, "protocol directory", exc
            ) from exc
        self._posix_fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._posix_fd is not None:
            os.close(self._posix_fd)
            self._posix_fd = None
        if self._win_handle is not None:
            _win_close_handle(self._win_handle)
            self._win_handle = None
            self._win_final = None

    def _expected_win_child_path(self, name: str) -> str:
        if self._win_final is None:
            raise RuntimeError("Windows protocol directory is not open")
        return self._win_final + "\\" + name

    def _open_regular(
        self,
        name: str,
        *,
        reason: PairedChangeVerificationReason,
        optional: bool = False,
    ) -> BinaryIO | None:
        if name not in _ALLOWED_NAMES:
            raise PairedChangeVerificationError(reason, name)

        if os.name == "nt":
            try:
                handle = _win_create_handle(
                    str(self.path / name),
                    access=_GENERIC_READ,
                    share=_FILE_SHARE_READ,
                    creation=_OPEN_EXISTING,
                    flags=_FILE_FLAG_OPEN_REPARSE_POINT,
                )
            except OSError as exc:
                if optional and _is_missing_error(exc):
                    return None
                raise _translate_io_error(reason, name, exc) from exc
            try:
                win_file_info = _win_handle_info(handle)
                if (
                    win_file_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or win_file_info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
                ):
                    raise OSError(errno.EINVAL, "unsafe protocol file")
                final_path = _win_final_path(handle)
                if (
                    final_path.casefold()
                    != self._expected_win_child_path(name).casefold()
                ):
                    raise OSError(
                        errno.EXDEV, "protocol file escaped pinned directory"
                    )
                import msvcrt

                win_fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)  # type: ignore[attr-defined]
                handle = -1
                return os.fdopen(win_fd, "rb")
            except OSError as exc:
                raise _translate_io_error(reason, name, exc) from exc
            finally:
                if handle not in {-1, 0, _INVALID_HANDLE_VALUE}:
                    _win_close_handle(handle)

        if self._posix_fd is None:
            raise RuntimeError("POSIX protocol directory is not open")
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise PairedChangeVerificationError(reason, name)
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | nofollow,
                dir_fd=self._posix_fd,
            )
        except OSError as exc:
            if optional and _is_missing_error(exc):
                return None
            raise _translate_io_error(reason, name, exc) from exc
        try:
            posix_file_info = os.fstat(fd)
            if not stat.S_ISREG(posix_file_info.st_mode):
                raise OSError(errno.EINVAL, "protocol file is not regular")
            return os.fdopen(fd, "rb")
        except OSError as exc:
            os.close(fd)
            raise _translate_io_error(reason, name, exc) from exc

    def read_bounded(
        self,
        name: str,
        *,
        max_bytes: int,
        reason: PairedChangeVerificationReason,
        optional: bool = False,
    ) -> bytes | None:
        handle = self._open_regular(name, reason=reason, optional=optional)
        if handle is None:
            return None
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

    def inspect_receipt_inventory(self) -> None:
        reason = PairedChangeVerificationReason.MALFORMED_RECEIPT
        try:
            if os.name == "nt":
                entries = tuple(os.scandir(self.path))
            else:
                if self._posix_fd is None:
                    raise RuntimeError("POSIX protocol directory is not open")
                entries = tuple(os.scandir(self._posix_fd))
            for entry in entries:
                if entry.name not in _ALLOWED_NAMES:
                    raise PairedChangeVerificationError(
                        reason, "protocol inventory"
                    )
                handle = self._open_regular(entry.name, reason=reason)
                assert handle is not None
                handle.close()
        except PairedChangeVerificationError:
            raise
        except (OSError, RuntimeError) as exc:
            raise PairedChangeVerificationError(
                reason, "protocol directory"
            ) from exc

    def create_receipt(self, payload: bytes) -> None:
        reason = PairedChangeVerificationReason.MALFORMED_RECEIPT
        if os.name == "nt":
            try:
                handle = _win_create_handle(
                    str(self.path / CLAIM_RECEIPT_FILENAME),
                    access=_GENERIC_READ | _GENERIC_WRITE,
                    share=_FILE_SHARE_READ,
                    creation=_CREATE_NEW,
                    flags=_FILE_FLAG_OPEN_REPARSE_POINT,
                )
            except OSError as exc:
                raise _translate_io_error(
                    reason, CLAIM_RECEIPT_FILENAME, exc
                ) from exc
            try:
                win_receipt_info = _win_handle_info(handle)
                if (
                    win_receipt_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or win_receipt_info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
                ):
                    raise OSError(errno.EINVAL, "unsafe receipt file")
                final_path = _win_final_path(handle)
                if (
                    final_path.casefold()
                    != self._expected_win_child_path(
                        CLAIM_RECEIPT_FILENAME
                    ).casefold()
                ):
                    raise OSError(
                        errno.EXDEV, "receipt escaped pinned directory"
                    )
                import msvcrt

                win_receipt_fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)  # type: ignore[attr-defined]
                handle = -1
                with os.fdopen(win_receipt_fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
            except OSError as exc:
                raise _translate_io_error(
                    reason, CLAIM_RECEIPT_FILENAME, exc
                ) from exc
            finally:
                if handle not in {-1, 0, _INVALID_HANDLE_VALUE}:
                    _win_close_handle(handle)
            return

        if self._posix_fd is None:
            raise RuntimeError("POSIX protocol directory is not open")
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise PairedChangeVerificationError(
                reason, "protocol directory"
            )
        fd: int | None = None
        try:
            fd = os.open(
                CLAIM_RECEIPT_FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=self._posix_fd,
            )
            posix_receipt_info = os.fstat(fd)
            if not stat.S_ISREG(posix_receipt_info.st_mode):
                raise OSError(errno.EINVAL, "receipt is not regular")
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise _translate_io_error(reason, CLAIM_RECEIPT_FILENAME, exc) from exc
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()


def _parse_model(
    payload: bytes,
    model_type: type[ModelT],
    reason: PairedChangeVerificationReason,
    field: str,
) -> ModelT:
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
        return model_type.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise PairedChangeVerificationError(reason, field) from exc


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
        or (
            "protocol_id" in design
            and design["protocol_id"] != "paired-change/v1"
        )
    ):
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.UNSUPPORTED_PROTOCOL,
            "protocol_design",
        )

    canaries = value.get("canaries")
    if not isinstance(canaries, list):
        return
    seen_canary_ids: set[str] = set()
    for canary in canaries:
        if not isinstance(canary, dict):
            continue
        canary_id = canary.get("canary_id")
        if isinstance(canary_id, str):
            if canary_id in seen_canary_ids:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                    "duplicate canary",
                )
            seen_canary_ids.add(canary_id)
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
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                    "pair slots",
                )
            if not all(isinstance(attempt, dict) for attempt in attempts):
                continue
            sides = {attempt.get("side") for attempt in attempts}
            attempt_repetitions = {
                attempt.get("repetition") for attempt in attempts
            }
            if sides != {"baseline", "candidate"}:
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                    "pair sides",
                )
            if (
                isinstance(repetition, int)
                and attempt_repetitions != {repetition}
            ):
                raise PairedChangeVerificationError(
                    PairedChangeVerificationReason.PAIR_LAYOUT_MISMATCH,
                    "pair repetition",
                )


def _read_protocol_evidence_from_session(
    session: _PinnedProtocolDirectory,
) -> tuple[ProtocolEvidenceV1, bytes]:
    payload = session.read_bounded(
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


def _read_claim_receipt_from_session(
    session: _PinnedProtocolDirectory,
) -> ClaimReceiptV1 | None:
    payload = session.read_bounded(
        CLAIM_RECEIPT_FILENAME,
        max_bytes=CLAIM_RECEIPT_MAX_BYTES,
        reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
        optional=True,
    )
    if payload is None:
        return None
    session.inspect_receipt_inventory()
    return _parse_model(
        payload,
        ClaimReceiptV1,
        PairedChangeVerificationReason.MALFORMED_RECEIPT,
        CLAIM_RECEIPT_FILENAME,
    )


def _read_protocol_companion(
    protocol_path: Path,
) -> tuple[ProtocolEvidenceV1, bytes, ClaimReceiptV1 | None]:
    with _PinnedProtocolDirectory(
        protocol_path,
        root_reason=PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
    ) as session:
        evidence, payload = _read_protocol_evidence_from_session(session)
        receipt = _read_claim_receipt_from_session(session)
        return evidence, payload, receipt


def _read_protocol_evidence_with_bytes(
    protocol_path: Path,
) -> tuple[ProtocolEvidenceV1, bytes]:
    with _PinnedProtocolDirectory(
        protocol_path,
        root_reason=PairedChangeVerificationReason.MALFORMED_PROTOCOL_EVIDENCE,
    ) as session:
        return _read_protocol_evidence_from_session(session)


def read_protocol_evidence(protocol_path: Path) -> ProtocolEvidenceV1:
    evidence, _payload = _read_protocol_evidence_with_bytes(protocol_path)
    return evidence


def read_claim_receipt(protocol_path: Path) -> ClaimReceiptV1 | None:
    with _PinnedProtocolDirectory(
        protocol_path,
        root_reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
    ) as session:
        return _read_claim_receipt_from_session(session)


def write_claim_receipt(
    protocol_path: Path,
    receipt: ClaimReceiptV1,
) -> Path:
    payload = canonical_json_file_bytes(receipt.model_dump(mode="json"))
    with _PinnedProtocolDirectory(
        protocol_path,
        root_reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
    ) as session:
        existing_receipt = session.read_bounded(
            CLAIM_RECEIPT_FILENAME,
            max_bytes=CLAIM_RECEIPT_MAX_BYTES,
            reason=PairedChangeVerificationReason.MALFORMED_RECEIPT,
            optional=True,
        )
        if existing_receipt is not None:
            raise PairedChangeVerificationError(
                PairedChangeVerificationReason.MALFORMED_RECEIPT,
                CLAIM_RECEIPT_FILENAME,
            )
        session.inspect_receipt_inventory()
        session.create_receipt(payload)
    return protocol_path / CLAIM_RECEIPT_FILENAME
