"""RED tests for secure, hierarchical, offline first-bad/v1 package I/O.

`qualock.protocols.first_bad.io` does not exist yet. These tests specify the
untrusted-filesystem posture required of `read_first_bad_package` and
`write_first_bad_receipt` before any implementation exists: the chain root,
`edges/`, each numeric edge directory, and each edge's `bundle/`/`protocol/`
subdirectory are pinned once and never reopened by a reconstructed pathname;
no symlink/reparse point is ever followed; no non-regular file is ever read;
every read is bounded from opened-handle metadata; the fixed package
inventory is exactly `chain-evidence.json`, optional `chain-receipt.json`,
and `edges/`, with each edge directory containing exactly `bundle/` and
`protocol/`. Collection failure because `io.py` is missing is acceptable RED
evidence for this phase.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.protocols.first_bad.io import (
    CHAIN_EVIDENCE_FILENAME,
    CHAIN_EVIDENCE_MAX_BYTES,
    CHAIN_RECEIPT_FILENAME,
    EDGES_DIRNAME,
    EdgePackageSnapshot,
    FirstBadPackageSnapshot,
    FirstBadVerificationError,
    FirstBadVerificationReason,
    read_first_bad_package,
    write_first_bad_receipt,
)
from qualock.protocols.first_bad.models import (
    FirstBadChainEvidenceV1,
    FirstBadClaimClass,
    FirstBadEdgeEvidenceV1,
    FirstBadReceiptV1,
)
from qualock.protocols.paired_change.models import AgentDependencyStateV1, ModelDeclarationV1

_FIFO_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="no FIFO concept on Windows"
)
_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="POSIX-only dir_fd/O_NOFOLLOW rename semantics"
)
_NATIVE_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="Windows share-mode/reparse semantics"
)

_PROTOCOL_EVIDENCE_FILENAME = "protocol-evidence.json"
_CLAIM_RECEIPT_FILENAME = "claim-receipt.json"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _agent_state(label: str) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name="claude",
        version="1.0.0",
        binary_sha256=_sha(f"{label}-binary"),
        support_sha256=None,
        model=ModelDeclarationV1(id="claude-sonnet-5", snapshot=None, reasoning_effort="medium"),
    )


def _edge_evidence(index: int, baseline: str, candidate: str) -> FirstBadEdgeEvidenceV1:
    return FirstBadEdgeEvidenceV1(
        index=index,
        baseline_version=baseline,
        candidate_version=candidate,
        baseline_runtime_identity=_agent_state(baseline),
        candidate_runtime_identity=_agent_state(candidate),
        bundle_manifest_sha256=_sha(f"manifest-{index}"),
        protocol_evidence_sha256=_sha(f"protocol-{index}"),
    )


def _chain_evidence(edge_count: int) -> FirstBadChainEvidenceV1:
    versions = tuple(f"1.{i}.0" for i in range(edge_count + 1))
    edges = tuple(_edge_evidence(i, versions[i], versions[i + 1]) for i in range(edge_count))
    return FirstBadChainEvidenceV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        agent_name="claude",
        baseline_version=versions[0],
        baseline_runtime_identity=_agent_state(versions[0]),
        upper_version=versions[-1],
        catalog_versions=versions,
        catalog_sha256=_sha("catalog"),
        suite_sha256=_sha("suite"),
        config_sha256=_sha("config"),
        model_pin=ModelDeclarationV1(id="claude-sonnet-5", snapshot=None, reasoning_effort="medium"),
        protocol_design_sha256=_sha("design"),
        edges=edges,
        chain_sha256=_sha("chain"),
    )


def _chain_receipt() -> FirstBadReceiptV1:
    return FirstBadReceiptV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        chain_sha256=_sha("chain"),
        catalog_sha256=_sha("catalog"),
        conditions=(),
        edges=(),
        claim=FirstBadClaimClass.UNRESOLVED,
        boundary_version=None,
        boundary_edge_index=None,
    )


def _edge_dirname(index: int) -> str:
    return f"{index:06d}"


def _manifest_bytes(index: int, marker: str | None = None) -> bytes:
    payload = f'{{"edge":{index}}}' if marker is None else f'{{"edge":{index},"marker":"{marker}"}}'
    return payload.encode("utf-8")


def _protocol_evidence_bytes(index: int) -> bytes:
    return f'{{"edge":{index}}}'.encode()


def _write_edge_files(
    edge_dir: Path,
    index: int,
    *,
    marker: str | None = None,
    extra_bundle_files: dict[str, bytes] | None = None,
    with_claim_receipt: bool = False,
) -> None:
    bundle_dir = edge_dir / "bundle"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_bytes(_manifest_bytes(index, marker))
    for name, data in (extra_bundle_files or {}).items():
        (bundle_dir / name).write_bytes(data)
    protocol_dir = edge_dir / "protocol"
    protocol_dir.mkdir(parents=True)
    (protocol_dir / _PROTOCOL_EVIDENCE_FILENAME).write_bytes(_protocol_evidence_bytes(index))
    if with_claim_receipt:
        (protocol_dir / _CLAIM_RECEIPT_FILENAME).write_bytes(b'{"stored":true}')


def _build_chain(
    tmp_path: Path,
    *,
    name: str = "first-bad-chain",
    edge_count: int = 1,
    marker: str | None = None,
) -> tuple[Path, FirstBadChainEvidenceV1]:
    chain_path = tmp_path / name
    chain_path.mkdir()
    evidence = _chain_evidence(edge_count)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(
        canonical_json_file_bytes(evidence.model_dump(mode="json"))
    )
    edges_dir = chain_path / EDGES_DIRNAME
    edges_dir.mkdir()
    for edge in evidence.edges:
        edge_dir = edges_dir / _edge_dirname(edge.index)
        edge_dir.mkdir()
        _write_edge_files(edge_dir, edge.index, marker=marker)
    return chain_path, evidence


def _truncate_to(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.truncate(size)


# --- read_first_bad_package: happy paths ----------------------------------------


def test_read_first_bad_package_parses_valid_chain(tmp_path: Path) -> None:
    chain_path, evidence = _build_chain(tmp_path, edge_count=2)

    snapshot = read_first_bad_package(chain_path)

    assert isinstance(snapshot, FirstBadPackageSnapshot)
    assert snapshot.evidence == evidence
    assert snapshot.evidence_bytes == (chain_path / CHAIN_EVIDENCE_FILENAME).read_bytes()
    assert snapshot.stored_receipt is None
    assert len(snapshot.edges) == 2
    for index, edge_snapshot in enumerate(snapshot.edges):
        assert isinstance(edge_snapshot, EdgePackageSnapshot)
        assert edge_snapshot.bundle_files == {"manifest.json": _manifest_bytes(index)}
        assert edge_snapshot.protocol_evidence_bytes == _protocol_evidence_bytes(index)
        assert edge_snapshot.claim_receipt_bytes is None


def test_read_first_bad_package_reads_stored_chain_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    receipt = _chain_receipt()
    (chain_path / CHAIN_RECEIPT_FILENAME).write_bytes(
        canonical_json_file_bytes(receipt.model_dump(mode="json"))
    )

    snapshot = read_first_bad_package(chain_path)

    assert snapshot.stored_receipt == receipt


def test_read_first_bad_package_reads_optional_child_claim_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    protocol_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "protocol"
    (protocol_dir / _CLAIM_RECEIPT_FILENAME).write_bytes(b'{"stored":true}')

    snapshot = read_first_bad_package(chain_path)

    assert snapshot.edges[0].claim_receipt_bytes == b'{"stored":true}'


def test_read_first_bad_package_reads_optional_bundle_file(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    bundle_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "bundle"
    (bundle_dir / "pricing.json").write_bytes(b'{"pricing":true}')

    snapshot = read_first_bad_package(chain_path)

    assert snapshot.edges[0].bundle_files["pricing.json"] == b'{"pricing":true}'


def test_snapshots_are_immutable(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    snapshot = read_first_bad_package(chain_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.evidence = None  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.edges[0].protocol_evidence_bytes = b"x"  # type: ignore[misc]
    assert isinstance(snapshot.edges[0].bundle_files, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.edges[0].bundle_files["manifest.json"] = b"attacker"  # type: ignore[index]


# --- read_first_bad_package: chain-evidence.json acquisition ---------------------


def test_read_first_bad_package_rejects_missing_chain_evidence(tmp_path: Path) -> None:
    chain_path = tmp_path / "empty-chain"
    chain_path.mkdir()
    (chain_path / EDGES_DIRNAME).mkdir()
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(tmp_path / "does-not-exist")
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_root_symlink(tmp_path: Path) -> None:
    real_path, _evidence = _build_chain(tmp_path, name="real-chain")
    link_path = tmp_path / "link-chain"
    link_path.symlink_to(real_path, target_is_directory=True)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(link_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_symlinked_chain_evidence_file(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (chain_path / CHAIN_EVIDENCE_FILENAME).unlink()
    (chain_path / CHAIN_EVIDENCE_FILENAME).symlink_to(outside)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


@_FIFO_UNSUPPORTED
def test_read_first_bad_package_rejects_non_regular_chain_evidence(tmp_path: Path) -> None:
    chain_path = tmp_path / "fifo-chain"
    chain_path.mkdir()
    (chain_path / EDGES_DIRNAME).mkdir()
    os.mkfifo(chain_path / CHAIN_EVIDENCE_FILENAME)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_oversized_chain_evidence(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    _truncate_to(chain_path / CHAIN_EVIDENCE_FILENAME, CHAIN_EVIDENCE_MAX_BYTES + 1)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_malformed_utf8(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_malformed_json(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(b"{not valid json")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_read_first_bad_package_rejects_schema_violation(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(b'{"schema_version":2}\n')
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.UNSUPPORTED_CHAIN_PROTOCOL


def test_read_first_bad_package_rejects_wrong_protocol_id(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(
        b'{"schema_version":1,"protocol_id":"paired-change/v1"}\n'
    )
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.UNSUPPORTED_CHAIN_PROTOCOL


def test_read_first_bad_package_rejects_wrong_shape_valid_schema(tmp_path: Path) -> None:
    # Correct schema/protocol markers but otherwise malformed shape must still
    # fail closed as a malformed-evidence error, not crash the preflight.
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(
        b'{"schema_version":1,"protocol_id":"first-bad/v1"}\n'
    )
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


# --- read_first_bad_package: chain-receipt.json acquisition ----------------------


def test_read_first_bad_package_rejects_symlinked_chain_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (chain_path / CHAIN_RECEIPT_FILENAME).symlink_to(outside)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT


def test_read_first_bad_package_rejects_oversized_chain_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    _truncate_to(chain_path / CHAIN_RECEIPT_FILENAME, CHAIN_EVIDENCE_MAX_BYTES + 1)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT


def test_read_first_bad_package_rejects_malformed_chain_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / CHAIN_RECEIPT_FILENAME).write_bytes(b"{not valid json")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT


# --- read_first_bad_package: fixed root/edge inventory ----------------------------


def test_read_first_bad_package_rejects_unexpected_root_entry(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / "unexpected.txt").write_bytes(b"nope")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_symlinked_edges_directory(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / "outside-edges"
    outside.mkdir()
    edges_path = chain_path / EDGES_DIRNAME
    import shutil

    shutil.rmtree(edges_path)
    edges_path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_missing_internal_edge_directory(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path, edge_count=3)
    import shutil

    shutil.rmtree(chain_path / EDGES_DIRNAME / _edge_dirname(1))
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_extra_edge_directory(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path, edge_count=1)
    extra_dir = chain_path / EDGES_DIRNAME / _edge_dirname(99)
    extra_dir.mkdir()
    _write_edge_files(extra_dir, 99)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_symlinked_edge_directory(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / "outside-edge"
    outside.mkdir()
    edge_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0)
    import shutil

    shutil.rmtree(edge_dir)
    edge_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_unexpected_edge_entry(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    edge_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0)
    (edge_dir / "unexpected").write_bytes(b"nope")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


@pytest.mark.parametrize("component", ["bundle", "protocol"])
def test_read_first_bad_package_rejects_symlinked_edge_component(
    tmp_path: Path, component: str
) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / f"outside-{component}"
    outside.mkdir()
    component_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / component
    import shutil

    shutil.rmtree(component_dir)
    component_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_unexpected_bundle_entry(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    bundle_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "bundle"
    (bundle_dir / "unexpected.json").write_bytes(b"{}")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_unexpected_protocol_entry(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    protocol_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "protocol"
    (protocol_dir / "unexpected.json").write_bytes(b"{}")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_missing_protocol_evidence(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    protocol_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "protocol"
    (protocol_dir / _PROTOCOL_EVIDENCE_FILENAME).unlink()
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


@_FIFO_UNSUPPORTED
def test_read_first_bad_package_rejects_non_regular_bundle_file(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    bundle_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "bundle"
    (bundle_dir / "manifest.json").unlink()
    os.mkfifo(bundle_dir / "manifest.json")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_oversized_bundle_file(tmp_path: Path) -> None:
    from qualock.evidence.bundle_models import MANIFEST_MAX_BYTES

    chain_path, _evidence = _build_chain(tmp_path)
    bundle_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "bundle"
    _truncate_to(bundle_dir / "manifest.json", MANIFEST_MAX_BYTES + 1)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_oversized_protocol_evidence(tmp_path: Path) -> None:
    from qualock.protocols.paired_change.io import PROTOCOL_EVIDENCE_MAX_BYTES

    chain_path, _evidence = _build_chain(tmp_path)
    protocol_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "protocol"
    _truncate_to(protocol_dir / _PROTOCOL_EVIDENCE_FILENAME, PROTOCOL_EVIDENCE_MAX_BYTES + 1)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


def test_read_first_bad_package_rejects_oversized_claim_receipt(tmp_path: Path) -> None:
    from qualock.protocols.paired_change.io import CLAIM_RECEIPT_MAX_BYTES

    chain_path, _evidence = _build_chain(tmp_path)
    protocol_dir = chain_path / EDGES_DIRNAME / _edge_dirname(0) / "protocol"
    _truncate_to(protocol_dir / _CLAIM_RECEIPT_FILENAME, CLAIM_RECEIPT_MAX_BYTES + 1)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(chain_path)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH


# --- pinned-transaction TOCTOU coverage -------------------------------------------


@_POSIX_ONLY
def test_read_first_bad_package_ignores_root_replacement_after_pinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # POSIX rename-while-open semantics only: the kernel permits renaming a
    # directory that has an open fd, and dir_fd-relative lookups continue to
    # resolve against the pinned (now unlinked-by-path) directory. Windows
    # blocks this rename outright while the pinned handle is held; that
    # stronger guarantee is covered separately by the ancestor-rename test.
    from qualock.protocols.first_bad import io as io_module

    chain_path, evidence = _build_chain(tmp_path, edge_count=1)
    real_open_dir = io_module._PinnedTree.open_dir
    state = {"swapped": False}

    def swap_root_then_open(self, parent_fd, name, **kwargs):  # type: ignore[no-untyped-def]
        if name == EDGES_DIRNAME and not state["swapped"]:
            state["swapped"] = True
            moved = tmp_path / "moved-original-root"
            chain_path.rename(moved)
            attacker_path, _attacker_evidence = _build_chain(
                tmp_path, name=chain_path.name, edge_count=1, marker="attacker"
            )
            assert attacker_path == chain_path
        return real_open_dir(self, parent_fd, name, **kwargs)

    monkeypatch.setattr(io_module._PinnedTree, "open_dir", swap_root_then_open)

    snapshot = read_first_bad_package(chain_path)

    assert state["swapped"] is True
    assert snapshot.evidence == evidence
    assert snapshot.edges[0].bundle_files["manifest.json"] == _manifest_bytes(0)


@_POSIX_ONLY
def test_read_first_bad_package_ignores_edges_replacement_after_pinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # POSIX-only for the same reason as the root-replacement test above.
    from qualock.protocols.first_bad import io as io_module

    chain_path, _evidence = _build_chain(tmp_path, edge_count=1)
    real_open_dir = io_module._PinnedTree.open_dir
    state = {"swapped": False}

    def swap_edges_then_return(self, parent_fd, name, **kwargs):  # type: ignore[no-untyped-def]
        fd = real_open_dir(self, parent_fd, name, **kwargs)
        if name == EDGES_DIRNAME and not state["swapped"]:
            state["swapped"] = True
            edges_path = chain_path / EDGES_DIRNAME
            moved = tmp_path / "moved-original-edges"
            edges_path.rename(moved)
            decoy_chain, _decoy_evidence = _build_chain(
                tmp_path, name="decoy-chain", edge_count=1, marker="attacker"
            )
            (decoy_chain / EDGES_DIRNAME).rename(edges_path)
        return fd

    monkeypatch.setattr(io_module._PinnedTree, "open_dir", swap_edges_then_return)

    snapshot = read_first_bad_package(chain_path)

    assert state["swapped"] is True
    assert snapshot.edges[0].bundle_files["manifest.json"] == _manifest_bytes(0)


# --- write_first_bad_receipt ------------------------------------------------------


def test_write_first_bad_receipt_produces_canonical_deterministic_bytes(tmp_path: Path) -> None:
    receipt = _chain_receipt()
    chain_path_a, _ = _build_chain(tmp_path, name="chain-a")
    chain_path_b, _ = _build_chain(tmp_path, name="chain-b")

    path_a = write_first_bad_receipt(chain_path_a, receipt)
    path_b = write_first_bad_receipt(chain_path_b, receipt)

    assert path_a.name == CHAIN_RECEIPT_FILENAME
    assert path_b.name == CHAIN_RECEIPT_FILENAME
    bytes_a = path_a.read_bytes()
    bytes_b = path_b.read_bytes()
    assert bytes_a == bytes_b
    assert bytes_a == canonical_json_file_bytes(receipt.model_dump(mode="json"))


def test_write_first_bad_receipt_round_trips_through_read(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    receipt = _chain_receipt()
    write_first_bad_receipt(chain_path, receipt)
    assert read_first_bad_package(chain_path).stored_receipt == receipt


def test_write_first_bad_receipt_never_overwrites_existing_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    write_first_bad_receipt(chain_path, _chain_receipt())
    original_bytes = (chain_path / CHAIN_RECEIPT_FILENAME).read_bytes()

    other_receipt = _chain_receipt().model_copy(update={"catalog_sha256": _sha("other")})
    with pytest.raises(FirstBadVerificationError) as exc_info:
        write_first_bad_receipt(chain_path, other_receipt)
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT

    assert (chain_path / CHAIN_RECEIPT_FILENAME).read_bytes() == original_bytes


def test_write_first_bad_receipt_rejects_unexpected_root_inventory(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    (chain_path / "unexpected.txt").write_bytes(b"nope")
    with pytest.raises(FirstBadVerificationError) as exc_info:
        write_first_bad_receipt(chain_path, _chain_receipt())
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT
    assert not (chain_path / CHAIN_RECEIPT_FILENAME).exists()


def test_write_first_bad_receipt_rejects_symlinked_existing_receipt(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (chain_path / CHAIN_RECEIPT_FILENAME).symlink_to(outside)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        write_first_bad_receipt(chain_path, _chain_receipt())
    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT
    assert outside.read_bytes() == b"{}"


@_POSIX_ONLY
def test_write_first_bad_receipt_fails_closed_without_o_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(FirstBadVerificationError) as exc_info:
        write_first_bad_receipt(chain_path, _chain_receipt())

    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT
    assert not (chain_path / CHAIN_RECEIPT_FILENAME).exists()


# --- reason inventory --------------------------------------------------------------


def test_verification_reason_inventory_is_exact() -> None:
    assert {item.name for item in FirstBadVerificationReason} == {
        "MALFORMED_CHAIN_EVIDENCE",
        "UNSUPPORTED_CHAIN_PROTOCOL",
        "CATALOG_BINDING_MISMATCH",
        "BASELINE_BINDING_MISMATCH",
        "EDGE_LAYOUT_MISMATCH",
        "EDGE_BINDING_MISMATCH",
        "DIGEST_MISMATCH",
        "MALFORMED_CHAIN_RECEIPT",
        "CHAIN_CLAIM_MISMATCH",
    }


# --- Windows-specific control-flow coverage (runs on every platform) -------------


def test_windows_api_signature_configuration_is_pointer_safe() -> None:
    from types import SimpleNamespace

    from qualock.protocols.first_bad import io as io_module

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
        NtQueryDirectoryFile=FakeFunction(),
        RtlNtStatusToDosError=FakeFunction(),
    )

    io_module._configure_windows_api(kernel32, ntdll)

    assert kernel32.CreateFileW.restype is io_module.wintypes.HANDLE
    assert kernel32.CloseHandle.argtypes == [io_module.wintypes.HANDLE]
    assert ntdll.NtCreateFile.argtypes[0] == io_module.ctypes.POINTER(io_module.wintypes.HANDLE)
    assert ntdll.NtQueryDirectoryFile.argtypes[0] == io_module.wintypes.HANDLE
    assert ntdll.NtQueryDirectoryFile.restype is io_module.wintypes.LONG


def test_windows_root_open_failure_maps_to_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.protocols.first_bad import io as io_module

    monkeypatch.setattr(io_module.os, "name", "nt")

    def fail_open(*args: object, **kwargs: object) -> int:
        raise OSError(2, "missing chain root")

    monkeypatch.setattr(io_module, "_win_create_handle", fail_open)

    with pytest.raises(FirstBadVerificationError) as exc_info:
        read_first_bad_package(tmp_path / "missing")

    assert exc_info.value.reason is FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE


def test_windows_receipt_creation_never_reopens_a_pathname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.protocols.first_bad import io as io_module

    monkeypatch.setattr(io_module.os, "name", "nt")
    tree = io_module._PinnedTree()
    tree._win_handles.append(123)
    tree._win_finals[123] = r"\\?\C:\pinned"
    calls: list[tuple[int, str]] = []

    def fake_create_relative(root_handle: int, name: str):  # type: ignore[no-untyped-def]
        calls.append((root_handle, name))
        import io as stdlib_io

        return stdlib_io.BytesIO()

    monkeypatch.setattr(io_module, "_win_create_relative_file", fake_create_relative)
    monkeypatch.setattr(
        io_module,
        "_win_create_handle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("receipt creation reopened a pathname")
        ),
    )

    tree.create_file(
        123,
        CHAIN_RECEIPT_FILENAME,
        b"receipt",
        reason=FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT,
        field=CHAIN_RECEIPT_FILENAME,
    )

    assert calls == [(123, CHAIN_RECEIPT_FILENAME)]


def test_windows_directory_names_never_use_scandir(monkeypatch: pytest.MonkeyPatch) -> None:
    from qualock.protocols.first_bad import io as io_module

    monkeypatch.setattr(io_module.os, "name", "nt")

    def fail_scandir(*args: object, **kwargs: object) -> object:
        raise AssertionError("Windows chain inventory must never use os.scandir(path)")

    monkeypatch.setattr(io_module.os, "scandir", fail_scandir)
    monkeypatch.setattr(
        io_module,
        "_win_query_directory_names",
        lambda handle: ("chain-evidence.json", "edges"),
    )

    tree = io_module._PinnedTree()
    assert tree.names(999) == ("chain-evidence.json", "edges")


# --- native-Windows-only real semantics --------------------------------------------


@_NATIVE_WINDOWS_ONLY
def test_windows_pinned_root_blocks_ancestor_rename(tmp_path: Path) -> None:
    from qualock.protocols.first_bad import io as io_module

    ancestor = tmp_path / "ancestor"
    chain_path = ancestor / "nested" / "chain"
    chain_path.mkdir(parents=True)
    (chain_path / EDGES_DIRNAME).mkdir()
    (chain_path / CHAIN_EVIDENCE_FILENAME).write_bytes(b"original")

    tree = io_module._PinnedTree()
    with tree:
        root_fd = tree.open_root(
            chain_path,
            reason=FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE,
            field="chain root",
        )
        moved = tmp_path / "moved-ancestor"
        with pytest.raises(PermissionError):
            ancestor.rename(moved)
        assert (
            tree.read_file(
                root_fd,
                CHAIN_EVIDENCE_FILENAME,
                max_bytes=1024,
                reason=FirstBadVerificationReason.MALFORMED_CHAIN_EVIDENCE,
                field=CHAIN_EVIDENCE_FILENAME,
            )
            == b"original"
        )


@_NATIVE_WINDOWS_ONLY
def test_windows_write_first_bad_receipt_creates_new_file(tmp_path: Path) -> None:
    chain_path, _evidence = _build_chain(tmp_path)
    receipt = _chain_receipt()

    path = write_first_bad_receipt(chain_path, receipt)

    assert path.read_bytes() == canonical_json_file_bytes(receipt.model_dump(mode="json"))
    assert read_first_bad_package(chain_path).stored_receipt == receipt
