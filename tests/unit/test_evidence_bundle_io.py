import hashlib
import os
import sys
from pathlib import Path

import pytest

from qualock.evidence.bundle_io import (
    canonical_json_file_bytes,
    inspect_bundle_files,
    read_bounded_regular_file,
    sha256_regular_file,
)
from qualock.evidence.bundle_models import FILE_MAX_BYTES, EvidenceBundleError, EvidenceBundleReason

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only filesystem shape")


def _make_file_of_size(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.truncate(size)


def _write_minimal_bundle(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "manifest.json",
        "report.json",
        "qualification.json",
        "baseline.lock",
        "provenance.json",
        "canaries.json",
    ):
        (root / name).write_bytes(b"{}")


# --- canonical_json_file_bytes ---------------------------------------------


def test_canonical_json_file_bytes_is_exact_and_deterministic() -> None:
    assert canonical_json_file_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'


def test_canonical_json_file_bytes_ignores_input_key_order() -> None:
    first = canonical_json_file_bytes({"z": 1, "a": {"y": 2, "x": 3}})
    second = canonical_json_file_bytes({"a": {"x": 3, "y": 2}, "z": 1})
    assert first == second


# --- inspect_bundle_files ----------------------------------------------------


def test_inspect_bundle_files_returns_sorted_present_names(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    assert inspect_bundle_files(tmp_path) == (
        "baseline.lock",
        "canaries.json",
        "manifest.json",
        "provenance.json",
        "qualification.json",
        "report.json",
    )


def test_inspect_bundle_files_allows_optional_pricing(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    (tmp_path / "pricing.json").write_bytes(b"{}")
    assert "pricing.json" in inspect_bundle_files(tmp_path)


def test_inspect_bundle_files_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(tmp_path / "does-not-exist")
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_inspect_bundle_files_rejects_root_as_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_bytes(b"not a directory")
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_inspect_bundle_files_rejects_root_as_symlink(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    _write_minimal_bundle(real_root)
    link_root = tmp_path / "link"
    link_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(link_root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_inspect_bundle_files_rejects_payload_symlink(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write_minimal_bundle(root)
    target = tmp_path / "outside.json"
    target.write_bytes(b"{}")
    (root / "report.json").unlink()
    (root / "report.json").symlink_to(target)
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_inspect_bundle_files_rejects_fifo(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    fifo_path = tmp_path / "canaries.json"
    fifo_path.unlink()
    os.mkfifo(fifo_path)
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(tmp_path)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_inspect_bundle_files_rejects_nested_directory(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    (tmp_path / "nested").mkdir()
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(tmp_path)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_inspect_bundle_files_rejects_extra_file(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    (tmp_path / "unexpected.json").write_bytes(b"{}")
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(tmp_path)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_inspect_bundle_files_rejects_backslash_filename(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    (tmp_path / "re\\port.json").write_bytes(b"{}")
    with pytest.raises(EvidenceBundleError) as exc_info:
        inspect_bundle_files(tmp_path)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


# --- read_bounded_regular_file -------------------------------------------------


def test_read_bounded_regular_file_returns_exact_content(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_bytes(b'{"a":1}')
    assert read_bounded_regular_file(tmp_path, "report.json", max_bytes=1024) == b'{"a":1}'


def test_read_bounded_regular_file_rejects_missing_file(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_read_bounded_regular_file_rejects_name_outside_fixed_set(tmp_path: Path) -> None:
    (tmp_path / "unexpected.json").write_bytes(b"{}")
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, "unexpected.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_read_bounded_regular_file_rejects_symlink_payload(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_bytes(b"{}")
    (tmp_path / "report.json").symlink_to(target)
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@_POSIX_ONLY
def test_read_bounded_regular_file_rejects_replacement_to_link_race(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_bytes(b"{}")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (tmp_path / "report.json").unlink()
    (tmp_path / "report.json").symlink_to(outside)
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


@pytest.mark.parametrize("name", sorted(FILE_MAX_BYTES))
def test_read_bounded_regular_file_accepts_exact_cap_boundary(tmp_path: Path, name: str) -> None:
    cap = FILE_MAX_BYTES[name]
    _make_file_of_size(tmp_path / name, cap)
    data = read_bounded_regular_file(tmp_path, name, max_bytes=cap)
    assert len(data) == cap


@pytest.mark.parametrize("name", sorted(FILE_MAX_BYTES))
def test_read_bounded_regular_file_rejects_one_byte_over_cap(tmp_path: Path, name: str) -> None:
    cap = FILE_MAX_BYTES[name]
    _make_file_of_size(tmp_path / name, cap + 1)
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, name, max_bytes=cap)
    assert exc_info.value.reason is EvidenceBundleReason.MALFORMED_PAYLOAD


# --- sha256_regular_file --------------------------------------------------------


def test_sha256_regular_file_matches_hashlib_and_returns_size(tmp_path: Path) -> None:
    content = b"evidence-bundle-content"
    (tmp_path / "report.json").write_bytes(content)
    digest, size = sha256_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert digest == hashlib.sha256(content).hexdigest()
    assert size == len(content)


def test_sha256_regular_file_rejects_over_cap(tmp_path: Path) -> None:
    _make_file_of_size(tmp_path / "report.json", 2048)
    with pytest.raises(EvidenceBundleError) as exc_info:
        sha256_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.MALFORMED_PAYLOAD


@_POSIX_ONLY
def test_sha256_regular_file_rejects_symlink_payload(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_bytes(b"{}")
    (tmp_path / "report.json").symlink_to(target)
    with pytest.raises(EvidenceBundleError) as exc_info:
        sha256_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


# --- no fallback to link-following open when no-follow primitive missing -------


@_POSIX_ONLY
def test_read_bounded_regular_file_fails_closed_when_nofollow_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "report.json").write_bytes(b"{}")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(EvidenceBundleError) as exc_info:
        read_bounded_regular_file(tmp_path, "report.json", max_bytes=1024)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH
