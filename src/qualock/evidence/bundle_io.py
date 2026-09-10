"""Untrusted, no-follow filesystem access for the V1 offline evidence bundle.

Every helper here treats the bundle directory as attacker-controlled: no
symlink is ever followed, no non-regular file is ever read, and every
read is bounded by an exact byte cap enforced from trusted handle metadata
and while streaming.
"""

import ctypes
import hashlib
import os
import stat
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO

from qualock.evidence.bundle_models import (
    BUNDLE_FILENAMES,
    EvidenceBundleError,
    EvidenceBundleReason,
)
from qualock.evidence.fingerprint import canonical_json

_CHUNK_BYTES = 1024 * 1024


def canonical_json_file_bytes(value: object) -> bytes:
    return canonical_json(value) + b"\n"


def _require_fixed_name(name: str) -> None:
    if name not in BUNDLE_FILENAMES:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle entry")


def inspect_bundle_files(root: Path) -> tuple[str, ...]:
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root")

    names: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
                if name not in BUNDLE_FILENAMES:
                    raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle entry")
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name) from exc
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name)
                names.append(name)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root") from exc

    return tuple(sorted(names))


def _posix_open_regular_nofollow(root: Path, name: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root") from exc
    try:
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root")
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=root_fd)
        except (OSError, NotImplementedError) as exc:
            raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name) from exc
    finally:
        os.close(root_fd)

    file_info = os.fstat(file_fd)
    if not stat.S_ISREG(file_info.st_mode):
        os.close(file_fd)
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name)
    return file_fd


_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


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


def _win_create_file(path: str, *, is_dir: bool) -> int:
    flags = _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if is_dir else 0)
    handle = ctypes.windll.kernel32.CreateFileW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle in (0, _INVALID_HANDLE_VALUE):
        raise OSError(_win_last_error(), "CreateFileW failed")
    return int(handle)


def _win_file_attributes(handle: int) -> int:
    info = _ByHandleFileInformation()
    ok = ctypes.windll.kernel32.GetFileInformationByHandle(  # type: ignore[attr-defined]
        handle, ctypes.byref(info)
    )
    if not ok:
        raise OSError(_win_last_error(), "GetFileInformationByHandle failed")
    return int(info.dwFileAttributes)


def _win_final_path(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(  # type: ignore[attr-defined]
        handle, buffer, len(buffer), 0
    )
    if length == 0 or length >= len(buffer):
        raise OSError(_win_last_error(), "GetFinalPathNameByHandleW failed")
    return str(buffer.value)


def _win_payload_path_is_contained(root_final_path: str, name: str, file_final_path: str) -> bool:
    expected_final_path = root_final_path.rstrip("\\") + "\\" + name
    return file_final_path.lower() == expected_final_path.lower()


def _windows_open_regular_nofollow(root: Path, name: str) -> int:
    try:
        root_handle = _win_create_file(str(root), is_dir=True)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root") from exc
    try:
        if _win_file_attributes(root_handle) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root")
        root_final_path = _win_final_path(root_handle)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle root") from exc
    finally:
        ctypes.windll.kernel32.CloseHandle(root_handle)  # type: ignore[attr-defined]

    file_path = str(root / name)
    try:
        file_handle = _win_create_file(file_path, is_dir=False)
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name) from exc

    opened = False
    try:
        if _win_file_attributes(file_handle) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name)
        file_final_path = _win_final_path(file_handle)
        if not _win_payload_path_is_contained(root_final_path, name, file_final_path):
            raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name)
        opened = True
    except OSError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, name) from exc
    finally:
        if not opened:
            ctypes.windll.kernel32.CloseHandle(file_handle)  # type: ignore[attr-defined]
    return file_handle


def _open_validated_regular_file(root: Path, name: str) -> BinaryIO:
    if os.name == "nt":
        handle = _windows_open_regular_nofollow(root, name)
        import msvcrt

        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)  # type: ignore[attr-defined]
        return os.fdopen(fd, "rb")
    fd = _posix_open_regular_nofollow(root, name)
    return os.fdopen(fd, "rb")


def read_bounded_regular_file(root: Path, name: str, *, max_bytes: int) -> bytes:
    _require_fixed_name(name)
    handle = _open_validated_regular_file(root, name)
    try:
        size = os.fstat(handle.fileno()).st_size
        if size > max_bytes:
            raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, name)
        data = bytearray()
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, name)
        return bytes(data)
    finally:
        handle.close()


def sha256_regular_file(root: Path, name: str, *, max_bytes: int) -> tuple[str, int]:
    _require_fixed_name(name)
    handle = _open_validated_regular_file(root, name)
    try:
        size = os.fstat(handle.fileno()).st_size
        if size > max_bytes:
            raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, name)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, name)
            digest.update(chunk)
        return digest.hexdigest(), total
    finally:
        handle.close()
