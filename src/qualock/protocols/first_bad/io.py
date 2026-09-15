"""Secure hierarchical first-bad/v1 package I/O.

The chain package is untrusted filesystem input. The chain root is opened
once and held open for the entire read (or write) transaction; every nested
directory and file (`edges/`, each numeric edge directory, `bundle/`,
`protocol/`, and their fixed files) is opened relative to its already-pinned
parent identity (`dir_fd` + `O_DIRECTORY` + `O_NOFOLLOW` on POSIX,
`NtCreateFile` with `OBJECT_ATTRIBUTES.RootDirectory` on Windows). A
pathname is never reconstructed and reopened after its parent is pinned, so
an attacker who replaces a path component mid-transaction cannot redirect a
read that has already resolved through an open handle.

Child `bundle/` and `protocol/` payload bytes are acquired here but are not
semantically validated: that is the offline verifier's job. This module only
guarantees that what it hands back came from the fixed, no-follow, pinned
identity the caller asked for.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
import struct
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, BinaryIO, Self

from pydantic import BaseModel, ValidationError

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.evidence.bundle_models import BUNDLE_FILENAMES, FILE_MAX_BYTES
from qualock.protocols.paired_change.io import (
    CLAIM_RECEIPT_FILENAME,
    CLAIM_RECEIPT_MAX_BYTES,
    PROTOCOL_EVIDENCE_FILENAME,
    PROTOCOL_EVIDENCE_MAX_BYTES,
)

from .models import FirstBadChainEvidenceV1, FirstBadReceiptV1

CHAIN_EVIDENCE_FILENAME = "chain-evidence.json"
CHAIN_RECEIPT_FILENAME = "chain-receipt.json"
EDGES_DIRNAME = "edges"
BUNDLE_DIRNAME = "bundle"
PROTOCOL_DIRNAME = "protocol"

_MIB = 1024 * 1024
CHAIN_EVIDENCE_MAX_BYTES = 4 * _MIB
CHAIN_RECEIPT_MAX_BYTES = 4 * _MIB
MAX_CATALOG_VERSIONS = 256
MAX_EDGE_DIRS = 255
EDGE_INDEX_WIDTH = 6

_ROOT_ALLOWED_NAMES = frozenset({CHAIN_EVIDENCE_FILENAME, CHAIN_RECEIPT_FILENAME, EDGES_DIRNAME})
_EDGE_ALLOWED_NAMES = frozenset({BUNDLE_DIRNAME, PROTOCOL_DIRNAME})
_PROTOCOL_ALLOWED_NAMES = frozenset({PROTOCOL_EVIDENCE_FILENAME, CLAIM_RECEIPT_FILENAME})

_CHUNK_BYTES = 1024 * 1024

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_SYNCHRONIZE = 0x00100000
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_OPEN = 1
_FILE_CREATE = 2
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_OBJ_CASE_INSENSITIVE = 0x00000040
_FILE_NAMES_INFORMATION_CLASS = 12
_STATUS_NO_MORE_FILES = ctypes.c_long(0x80000006).value


class FirstBadVerificationReason(str, Enum):
    MALFORMED_CHAIN_EVIDENCE = "malformed_chain_evidence"
    UNSUPPORTED_CHAIN_PROTOCOL = "unsupported_chain_protocol"
    CATALOG_BINDING_MISMATCH = "catalog_binding_mismatch"
    BASELINE_BINDING_MISMATCH = "baseline_binding_mismatch"
    EDGE_LAYOUT_MISMATCH = "edge_layout_mismatch"
    EDGE_BINDING_MISMATCH = "edge_binding_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED_CHAIN_RECEIPT = "malformed_chain_receipt"
    CHAIN_CLAIM_MISMATCH = "chain_claim_mismatch"


class FirstBadVerificationError(ValueError):
    def __init__(self, reason: FirstBadVerificationReason, field: str) -> None:
        super().__init__(f"first-bad verification {reason.value}: {field}")
        self.reason = reason
        self.field = field


@dataclass(frozen=True)
class EdgePackageSnapshot:
    bundle_files: Mapping[str, bytes]
    protocol_evidence_bytes: bytes
    claim_receipt_bytes: bytes | None


@dataclass(frozen=True)
class FirstBadPackageSnapshot:
    evidence: FirstBadChainEvidenceV1
    evidence_bytes: bytes
    stored_receipt: FirstBadReceiptV1 | None
    edges: tuple[EdgePackageSnapshot, ...]


# --- Windows ctypes plumbing -----------------------------------------------------


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


class _UnicodeString(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


class _ObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    )


class _IoStatusUnion(ctypes.Union):
    _fields_ = (("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID))


class _IoStatusBlock(ctypes.Structure):
    _anonymous_ = ("result",)
    _fields_ = (("result", _IoStatusUnion), ("Information", ctypes.c_size_t))


def _configure_windows_api(kernel32: Any, ntdll: Any) -> None:
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    ]
    ntdll.NtCreateFile.restype = wintypes.LONG
    ntdll.NtQueryDirectoryFile.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.BOOLEAN,
        ctypes.POINTER(_UnicodeString),
        wintypes.BOOLEAN,
    ]
    ntdll.NtQueryDirectoryFile.restype = wintypes.LONG
    ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


def _windows_apis() -> tuple[Any, Any]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)  # type: ignore[attr-defined]
    _configure_windows_api(kernel32, ntdll)
    return kernel32, ntdll


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
    kernel32, _ntdll = _windows_apis()
    handle = kernel32.CreateFileW(path, access, share, None, creation, flags, None)
    if handle in (0, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return int(handle)


def _win_handle_info(handle: int) -> _ByHandleFileInformation:
    kernel32, _ntdll = _windows_apis()
    info = _ByHandleFileInformation()
    ok = kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info))
    if not ok:
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return info


def _win_final_path(handle: int) -> str:
    kernel32, _ntdll = _windows_apis()
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetFinalPathNameByHandleW(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise ctypes.WinError(_win_last_error())  # type: ignore[attr-defined]
    return str(buffer.value)


def _win_close_handle(handle: int) -> None:
    kernel32, _ntdll = _windows_apis()
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _win_create_file_relative(
    parent_handle: int,
    name: str,
    *,
    desired_access: int,
    create_disposition: int,
    create_options: int,
) -> int:
    _kernel32, ntdll = _windows_apis()
    name_buffer = ctypes.create_unicode_buffer(name)
    name_bytes = len(name.encode("utf-16-le"))
    unicode_name = _UnicodeString(
        Length=name_bytes,
        MaximumLength=name_bytes + ctypes.sizeof(ctypes.c_wchar),
        Buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    object_attributes = _ObjectAttributes(
        Length=ctypes.sizeof(_ObjectAttributes),
        RootDirectory=wintypes.HANDLE(parent_handle),
        ObjectName=ctypes.pointer(unicode_name),
        Attributes=_OBJ_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = _IoStatusBlock()
    raw_handle = wintypes.HANDLE()
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(raw_handle),
            desired_access,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            _FILE_ATTRIBUTE_NORMAL,
            _FILE_SHARE_READ,
            create_disposition,
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        win_error = int(ntdll.RtlNtStatusToDosError(status))
        raise ctypes.WinError(win_error)  # type: ignore[attr-defined]
    if raw_handle.value is None:
        raise OSError(errno.EIO, "NtCreateFile returned no handle")
    return int(raw_handle.value)


def _win_open_relative_existing(parent_handle: int, name: str, *, directory: bool) -> int:
    create_options = (
        _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    ) | _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_FLAG_OPEN_REPARSE_POINT
    return _win_create_file_relative(
        parent_handle,
        name,
        desired_access=_GENERIC_READ | _SYNCHRONIZE,
        create_disposition=_FILE_OPEN,
        create_options=create_options,
    )


def _win_verify_relative_handle(
    handle: int,
    parent_final: str,
    name: str,
    *,
    expect_directory: bool,
) -> str:
    info = _win_handle_info(handle)
    is_reparse = bool(info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    is_directory = bool(info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
    if is_reparse or is_directory != expect_directory:
        raise OSError(errno.EINVAL, "unsafe nested entry")
    final_path = _win_final_path(handle)
    expected = parent_final.rstrip("\\") + "\\" + name
    if final_path.casefold() != expected.casefold():
        raise OSError(errno.EXDEV, "nested entry escaped pinned parent")
    return final_path.rstrip("\\")


def _win_create_relative_file(root_handle: int, name: str) -> BinaryIO:
    handle = _win_create_file_relative(
        root_handle,
        name,
        desired_access=_GENERIC_READ | _GENERIC_WRITE | _SYNCHRONIZE,
        create_disposition=_FILE_CREATE,
        create_options=_FILE_SYNCHRONOUS_IO_NONALERT | _FILE_NON_DIRECTORY_FILE,
    )
    try:
        win_info = _win_handle_info(handle)
        if (
            win_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or win_info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
        ):
            raise OSError(errno.EINVAL, "unsafe receipt file")
        import msvcrt

        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)  # type: ignore[attr-defined]
        handle = -1
        return os.fdopen(fd, "wb")
    finally:
        if handle not in {-1, 0, _INVALID_HANDLE_VALUE}:
            _win_close_handle(handle)


def _win_query_directory_names(handle: int) -> tuple[str, ...]:
    _kernel32, ntdll = _windows_apis()
    names: list[str] = []
    buffer_size = 64 * 1024
    buffer = ctypes.create_string_buffer(buffer_size)
    restart = True
    while True:
        io_status = _IoStatusBlock()
        status = int(
            ntdll.NtQueryDirectoryFile(
                wintypes.HANDLE(handle),
                None,
                None,
                None,
                ctypes.byref(io_status),
                buffer,
                buffer_size,
                _FILE_NAMES_INFORMATION_CLASS,
                False,
                None,
                restart,
            )
        )
        if status == _STATUS_NO_MORE_FILES:
            break
        if status < 0:
            win_error = int(ntdll.RtlNtStatusToDosError(status))
            raise ctypes.WinError(win_error)  # type: ignore[attr-defined]
        restart = False
        offset = 0
        raw = buffer.raw
        while True:
            next_offset, _file_index, name_len = struct.unpack_from("<III", raw, offset)
            name_bytes = raw[offset + 12 : offset + 12 + name_len]
            name = name_bytes.decode("utf-16-le")
            if name not in (".", ".."):
                names.append(name)
            if next_offset == 0:
                break
            offset += next_offset
    return tuple(names)


def _is_missing_error(exc: OSError) -> bool:
    return exc.errno == errno.ENOENT or getattr(exc, "winerror", None) in {2, 3}


def _translate_io_error(
    reason: FirstBadVerificationReason, field: str, exc: BaseException
) -> FirstBadVerificationError:
    return FirstBadVerificationError(reason, field)


# --- pinned hierarchical transaction ----------------------------------------------


class _PinnedTree:
    """One no-follow directory-tree transaction spanning the whole chain package."""

    def __init__(self) -> None:
        self._posix_fds: list[int] = []
        self._win_handles: list[int] = []
        self._win_finals: dict[int, str] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if os.name == "nt":
            for handle in reversed(self._win_handles):
                _win_close_handle(handle)
            self._win_handles.clear()
            self._win_finals.clear()
        else:
            for fd in reversed(self._posix_fds):
                os.close(fd)
            self._posix_fds.clear()

    def open_root(self, path: Path, *, reason: FirstBadVerificationReason, field: str) -> int:
        if os.name == "nt":
            handle: int | None = None
            try:
                handle = _win_create_handle(
                    str(path),
                    access=_GENERIC_READ,
                    share=_FILE_SHARE_READ,
                    creation=_OPEN_EXISTING,
                    flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
                )
                info = _win_handle_info(handle)
                if (
                    info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
                ):
                    raise OSError(errno.EINVAL, "unsafe chain root")
                final_path = _win_final_path(handle).rstrip("\\")
            except OSError as exc:
                if handle is not None:
                    _win_close_handle(handle)
                raise _translate_io_error(reason, field, exc) from exc
            self._win_handles.append(handle)
            self._win_finals[handle] = final_path
            return handle

        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise FirstBadVerificationError(reason, field)
        fd: int | None = None
        try:
            fd = os.open(path, os.O_RDONLY | directory | nofollow)
            posix_info = os.fstat(fd)
            if not stat.S_ISDIR(posix_info.st_mode):
                raise OSError(errno.ENOTDIR, "chain root is not a directory")
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise _translate_io_error(reason, field, exc) from exc
        self._posix_fds.append(fd)
        return fd

    def open_dir(
        self, parent_fd: int, name: str, *, reason: FirstBadVerificationReason, field: str
    ) -> int:
        if os.name == "nt":
            try:
                handle = _win_open_relative_existing(parent_fd, name, directory=True)
            except OSError as exc:
                raise _translate_io_error(reason, field, exc) from exc
            try:
                parent_final = self._win_finals.get(parent_fd)
                if parent_final is None:
                    raise RuntimeError("parent directory is not pinned")
                final_path = _win_verify_relative_handle(
                    handle, parent_final, name, expect_directory=True
                )
            except OSError as exc:
                _win_close_handle(handle)
                raise _translate_io_error(reason, field, exc) from exc
            self._win_handles.append(handle)
            self._win_finals[handle] = final_path
            return handle

        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise FirstBadVerificationError(reason, field)
        try:
            fd = os.open(name, os.O_RDONLY | directory | nofollow, dir_fd=parent_fd)
        except OSError as exc:
            raise _translate_io_error(reason, field, exc) from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.ENOTDIR, "nested entry is not a directory")
        except OSError as exc:
            os.close(fd)
            raise _translate_io_error(reason, field, exc) from exc
        self._posix_fds.append(fd)
        return fd

    def read_file(
        self,
        parent_fd: int,
        name: str,
        *,
        max_bytes: int,
        reason: FirstBadVerificationReason,
        field: str,
        optional: bool = False,
    ) -> bytes | None:
        stream: BinaryIO
        if os.name == "nt":
            try:
                handle = _win_open_relative_existing(parent_fd, name, directory=False)
            except OSError as exc:
                if optional and _is_missing_error(exc):
                    return None
                raise _translate_io_error(reason, field, exc) from exc
            try:
                parent_final = self._win_finals.get(parent_fd)
                if parent_final is None:
                    raise RuntimeError("parent directory is not pinned")
                _win_verify_relative_handle(handle, parent_final, name, expect_directory=False)
                import msvcrt

                win_fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)  # type: ignore[attr-defined]
                handle = -1
                stream = os.fdopen(win_fd, "rb")
            except OSError as exc:
                raise _translate_io_error(reason, field, exc) from exc
            finally:
                if handle not in {-1, 0, _INVALID_HANDLE_VALUE}:
                    _win_close_handle(handle)
        else:
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                raise FirstBadVerificationError(reason, field)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=parent_fd)
            except OSError as exc:
                if optional and _is_missing_error(exc):
                    return None
                raise _translate_io_error(reason, field, exc) from exc
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise OSError(errno.EINVAL, "nested entry is not a regular file")
            except OSError as exc:
                os.close(fd)
                raise _translate_io_error(reason, field, exc) from exc
            stream = os.fdopen(fd, "rb")

        try:
            try:
                size = os.fstat(stream.fileno()).st_size
            except OSError as exc:
                raise _translate_io_error(reason, field, exc) from exc
            if size > max_bytes:
                raise FirstBadVerificationError(reason, field)
            data = bytearray()
            while True:
                try:
                    chunk = stream.read(_CHUNK_BYTES)
                except OSError as exc:
                    raise _translate_io_error(reason, field, exc) from exc
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise FirstBadVerificationError(reason, field)
            return bytes(data)
        finally:
            stream.close()

    def names(self, directory_fd: int) -> tuple[str, ...]:
        if os.name == "nt":
            return _win_query_directory_names(directory_fd)
        return tuple(entry.name for entry in os.scandir(directory_fd))

    def create_file(
        self,
        parent_fd: int,
        name: str,
        payload: bytes,
        *,
        reason: FirstBadVerificationReason,
        field: str,
    ) -> None:
        if os.name == "nt":
            try:
                stream = _win_create_relative_file(parent_fd, name)
                with stream:
                    stream.write(payload)
                    stream.flush()
            except OSError as exc:
                raise _translate_io_error(reason, field, exc) from exc
            return

        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise FirstBadVerificationError(reason, field)
        fd: int | None = None
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=parent_fd,
            )
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError(errno.EINVAL, "receipt is not regular")
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise _translate_io_error(reason, field, exc) from exc
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()


def _edge_dirname(index: int) -> str:
    return f"{index:0{EDGE_INDEX_WIDTH}d}"


def _require_allowed_names(
    names: tuple[str, ...],
    allowed: frozenset[str],
    *,
    reason: FirstBadVerificationReason,
    field: str,
) -> None:
    for name in names:
        if name not in allowed:
            raise FirstBadVerificationError(reason, field)


def _parse_model(
    payload: bytes,
    model_type: type[BaseModel],
    reason: FirstBadVerificationReason,
    field: str,
) -> Any:
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
        return model_type.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise FirstBadVerificationError(reason, field) from exc


def _chain_evidence_value(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirstBadVerificationError(
            FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE, CHAIN_EVIDENCE_FILENAME
        ) from exc


def _preflight_chain_payload(value: object) -> None:
    if not isinstance(value, dict):
        return
    if ("schema_version" in value and value["schema_version"] != 1) or (
        "protocol_id" in value and value["protocol_id"] != "first-bad/v1"
    ):
        raise FirstBadVerificationError(
            FirstBadVerificationReason.UNSUPPORTED_CHAIN_PROTOCOL, "protocol"
        )
    catalog_versions = value.get("catalog_versions")
    if isinstance(catalog_versions, list) and len(catalog_versions) > MAX_CATALOG_VERSIONS:
        raise FirstBadVerificationError(
            FirstBadVerificationReason.CATALOG_BINDING_MISMATCH, "catalog_versions"
        )


def _read_edge_package(tree: _PinnedTree, edges_fd: int, index: int) -> EdgePackageSnapshot:
    dirname = _edge_dirname(index)
    edge_field = f"{EDGES_DIRNAME}/{dirname}"
    edge_fd = tree.open_dir(
        edges_fd, dirname, reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, field=edge_field
    )
    _require_allowed_names(
        tree.names(edge_fd),
        _EDGE_ALLOWED_NAMES,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=edge_field,
    )

    bundle_field = f"{edge_field}/{BUNDLE_DIRNAME}"
    bundle_fd = tree.open_dir(
        edge_fd,
        BUNDLE_DIRNAME,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=bundle_field,
    )
    bundle_files: dict[str, bytes] = {}
    for name in tree.names(bundle_fd):
        if name not in BUNDLE_FILENAMES:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, f"{bundle_field}/{name}"
            )
        data = tree.read_file(
            bundle_fd,
            name,
            max_bytes=FILE_MAX_BYTES[name],
            reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
            field=f"{bundle_field}/{name}",
        )
        assert data is not None
        bundle_files[name] = data

    protocol_field = f"{edge_field}/{PROTOCOL_DIRNAME}"
    protocol_fd = tree.open_dir(
        edge_fd,
        PROTOCOL_DIRNAME,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=protocol_field,
    )
    _require_allowed_names(
        tree.names(protocol_fd),
        _PROTOCOL_ALLOWED_NAMES,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=protocol_field,
    )
    protocol_evidence_bytes = tree.read_file(
        protocol_fd,
        PROTOCOL_EVIDENCE_FILENAME,
        max_bytes=PROTOCOL_EVIDENCE_MAX_BYTES,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=f"{protocol_field}/{PROTOCOL_EVIDENCE_FILENAME}",
    )
    if protocol_evidence_bytes is None:
        raise FirstBadVerificationError(
            FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
            f"{protocol_field}/{PROTOCOL_EVIDENCE_FILENAME}",
        )
    claim_receipt_bytes = tree.read_file(
        protocol_fd,
        CLAIM_RECEIPT_FILENAME,
        max_bytes=CLAIM_RECEIPT_MAX_BYTES,
        reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
        field=f"{protocol_field}/{CLAIM_RECEIPT_FILENAME}",
        optional=True,
    )

    return EdgePackageSnapshot(
        bundle_files=MappingProxyType(bundle_files),
        protocol_evidence_bytes=protocol_evidence_bytes,
        claim_receipt_bytes=claim_receipt_bytes,
    )


def read_first_bad_package(chain_path: Path) -> FirstBadPackageSnapshot:
    with _PinnedTree() as tree:
        root_fd = tree.open_root(
            chain_path,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE,
            field="chain root",
        )
        _require_allowed_names(
            tree.names(root_fd),
            _ROOT_ALLOWED_NAMES,
            reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
            field="chain root inventory",
        )

        evidence_bytes = tree.read_file(
            root_fd,
            CHAIN_EVIDENCE_FILENAME,
            max_bytes=CHAIN_EVIDENCE_MAX_BYTES,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE,
            field=CHAIN_EVIDENCE_FILENAME,
        )
        if evidence_bytes is None:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE, CHAIN_EVIDENCE_FILENAME
            )
        value = _chain_evidence_value(evidence_bytes)
        _preflight_chain_payload(value)
        try:
            evidence = FirstBadChainEvidenceV1.model_validate(value)
        except (ValidationError, TypeError, ValueError) as exc:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE, CHAIN_EVIDENCE_FILENAME
            ) from exc

        receipt_bytes = tree.read_file(
            root_fd,
            CHAIN_RECEIPT_FILENAME,
            max_bytes=CHAIN_RECEIPT_MAX_BYTES,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
            field=CHAIN_RECEIPT_FILENAME,
            optional=True,
        )
        stored_receipt = (
            None
            if receipt_bytes is None
            else _parse_model(
                receipt_bytes,
                FirstBadReceiptV1,
                FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
                CHAIN_RECEIPT_FILENAME,
            )
        )

        edges_fd = tree.open_dir(
            root_fd,
            EDGES_DIRNAME,
            reason=FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH,
            field=EDGES_DIRNAME,
        )
        edge_names = tree.names(edges_fd)
        if len(edge_names) > MAX_EDGE_DIRS or len(edge_names) != len(evidence.edges):
            raise FirstBadVerificationError(
                FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, EDGES_DIRNAME
            )
        expected_names = {_edge_dirname(edge.index) for edge in evidence.edges}
        if set(edge_names) != expected_names:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH, EDGES_DIRNAME
            )

        edges = tuple(_read_edge_package(tree, edges_fd, edge.index) for edge in evidence.edges)

    return FirstBadPackageSnapshot(
        evidence=evidence,
        evidence_bytes=evidence_bytes,
        stored_receipt=stored_receipt,
        edges=edges,
    )


def write_first_bad_receipt(chain_path: Path, receipt: FirstBadReceiptV1) -> Path:
    payload = canonical_json_file_bytes(receipt.model_dump(mode="json"))
    with _PinnedTree() as tree:
        root_fd = tree.open_root(
            chain_path,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
            field="chain root",
        )
        _require_allowed_names(
            tree.names(root_fd),
            _ROOT_ALLOWED_NAMES,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
            field="chain root inventory",
        )
        existing = tree.read_file(
            root_fd,
            CHAIN_RECEIPT_FILENAME,
            max_bytes=CHAIN_RECEIPT_MAX_BYTES,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
            field=CHAIN_RECEIPT_FILENAME,
            optional=True,
        )
        if existing is not None:
            raise FirstBadVerificationError(
                FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT, CHAIN_RECEIPT_FILENAME
            )
        tree.create_file(
            root_fd,
            CHAIN_RECEIPT_FILENAME,
            payload,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
            field=CHAIN_RECEIPT_FILENAME,
        )
    return chain_path / CHAIN_RECEIPT_FILENAME
