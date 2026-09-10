import hashlib
import os
from pathlib import Path

import pytest

import qualock.agents.support_integrity as support_integrity_module
from qualock.agents.base import AgentBinary, AgentSupportBinary, AgentSupportTree
from qualock.agents.support_integrity import (
    AgentSupportIntegrityError,
    agent_support_fingerprint,
    fingerprint_support_tree,
    verify_agent_supports,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_support_tree_digest_is_path_and_content_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "package"
    (root / "bundle/worker").mkdir(parents=True)
    (root / "bundle/gemini.js").write_text("entry", encoding="utf-8")
    (root / "bundle/worker/worker-entry.js").write_text("worker", encoding="utf-8")

    expected_stream = (
        f"bundle/gemini.js\0{_sha256(b'entry')}\n"
        f"bundle/worker/worker-entry.js\0{_sha256(b'worker')}\n"
    ).encode()
    first = fingerprint_support_tree(root)

    assert first == hashlib.sha256(expected_stream).hexdigest()
    assert first == fingerprint_support_tree(root)
    (root / "bundle/worker/worker-entry.js").write_text("changed", encoding="utf-8")
    assert fingerprint_support_tree(root) != first


def test_support_tree_rejects_nested_symlink(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside.js"
    outside.write_text("outside", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested/link.js").symlink_to(outside)

    with pytest.raises(AgentSupportIntegrityError, match="symlink"):
        fingerprint_support_tree(root)


def test_support_tree_rejects_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-package"
    real_root.mkdir()
    linked_root = tmp_path / "linked-package"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(AgentSupportIntegrityError, match="symlink"):
        fingerprint_support_tree(linked_root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_support_tree_rejects_non_regular_node(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    fifo = root / "runtime.fifo"
    os.mkfifo(fifo)
    with pytest.raises(AgentSupportIntegrityError, match="non-regular"):
        fingerprint_support_tree(root)


def test_agent_support_fingerprint_is_sorted_and_none_only_when_empty(
    tmp_path: Path,
) -> None:
    binary_path = tmp_path / "agent"
    binary_path.write_bytes(b"agent")
    support_path = tmp_path / "host"
    support_path.write_bytes(b"host")
    tree_root = tmp_path / "tree"
    tree_root.mkdir()
    tree_digest = _sha256(b"")
    support_digest = _sha256(b"host")
    binary = AgentBinary(
        name="gemini",
        version="0.58.0",
        path=binary_path,
        sha256=_sha256(b"agent"),
        support_binaries=(
            AgentSupportBinary(
                name="host",
                path=support_path,
                sha256=support_digest,
                container_path="/opt/qualock/z-host",
            ),
        ),
        support_trees=(
            AgentSupportTree(
                root=tree_root,
                sha256=tree_digest,
                container_root="/opt/qualock/a-tree",
            ),
        ),
    )
    expected_stream = (
        f"B\0/opt/qualock/z-host\0{support_digest}\nT\0/opt/qualock/a-tree\0{tree_digest}\n"
    ).encode()

    assert agent_support_fingerprint(binary) == hashlib.sha256(expected_stream).hexdigest()
    assert (
        agent_support_fingerprint(AgentBinary("codex", "1.0.0", binary_path, _sha256(b"agent")))
        is None
    )


def test_verify_agent_supports_accepts_matching_binary_and_tree(tmp_path: Path) -> None:
    binary_path = tmp_path / "agent"
    binary_path.write_bytes(b"agent")
    support_path = tmp_path / "host"
    support_path.write_bytes(b"host")
    tree_root = tmp_path / "tree"
    tree_root.mkdir()
    (tree_root / "nested.js").write_bytes(b"nested")
    binary = AgentBinary(
        name="gemini",
        version="0.58.0",
        path=binary_path,
        sha256=_sha256(b"agent"),
        support_binaries=(
            AgentSupportBinary("host", support_path, _sha256(b"host"), "/opt/qualock/host"),
        ),
        support_trees=(
            AgentSupportTree(
                tree_root,
                fingerprint_support_tree(tree_root),
                "/opt/qualock/tree",
            ),
        ),
    )

    verify_agent_supports(binary)


@pytest.mark.parametrize("kind", ["binary", "tree"])
def test_verify_agent_supports_rejects_tamper(tmp_path: Path, kind: str) -> None:
    binary_path = tmp_path / "agent"
    binary_path.write_bytes(b"agent")
    support_path = tmp_path / "host"
    support_path.write_bytes(b"host")
    tree_root = tmp_path / "tree"
    tree_root.mkdir()
    tree_file = tree_root / "nested.js"
    tree_file.write_bytes(b"nested")
    binary = AgentBinary(
        name="gemini",
        version="0.58.0",
        path=binary_path,
        sha256=_sha256(b"agent"),
        support_binaries=(
            AgentSupportBinary("host", support_path, _sha256(b"host"), "/opt/qualock/host"),
        ),
        support_trees=(
            AgentSupportTree(
                tree_root,
                fingerprint_support_tree(tree_root),
                "/opt/qualock/tree",
            ),
        ),
    )
    (support_path if kind == "binary" else tree_file).write_bytes(b"tampered")

    with pytest.raises(AgentSupportIntegrityError, match="fingerprint"):
        verify_agent_supports(binary)


@pytest.mark.parametrize(
    ("binary_destination", "tree_destination"),
    [
        ("/opt/qualock/runtime", "/opt/qualock/runtime"),
        ("/opt/qualock/runtime/host", "/opt/qualock/runtime"),
        ("/opt/qualock/runtime", "/opt/qualock/runtime/tree"),
    ],
)
def test_verify_agent_supports_rejects_overlapping_container_destinations(
    tmp_path: Path,
    binary_destination: str,
    tree_destination: str,
) -> None:
    support_path = tmp_path / "host"
    support_path.write_bytes(b"host")
    tree_root = tmp_path / "tree"
    tree_root.mkdir()
    binary = AgentBinary(
        "gemini",
        "0.58.0",
        tmp_path / "agent",
        _sha256(b"agent"),
        support_binaries=(
            AgentSupportBinary("host", support_path, _sha256(b"host"), binary_destination),
        ),
        support_trees=(AgentSupportTree(tree_root, _sha256(b""), tree_destination),),
    )

    with pytest.raises(AgentSupportIntegrityError, match="overlapping"):
        verify_agent_supports(binary)


@pytest.mark.skipif(
    not support_integrity_module._HAS_POSIX_DIR_FD,
    reason="requires POSIX dir_fd/O_NOFOLLOW traversal",
)
def test_support_tree_directory_swap_to_symlink_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "package"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.js").write_text("safe", encoding="utf-8")
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "evil.js").write_text("evil", encoding="utf-8")
    parked = root / "nested-original"

    real_open = support_integrity_module.os.open
    swapped = False

    def racing_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "nested" and dir_fd is not None and not swapped:
            nested.rename(parked)
            nested.symlink_to(evil, target_is_directory=True)
            swapped = True
        if dir_fd is None:
            return real_open(path, flags, mode)  # type: ignore[arg-type]
        return real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]

    monkeypatch.setattr(support_integrity_module.os, "open", racing_open)

    with pytest.raises(AgentSupportIntegrityError, match="symlink"):
        fingerprint_support_tree(root)
    assert swapped is True


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX byte-path semantics")
def test_support_tree_handles_undecodable_posix_filename(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    root_bytes = os.fsencode(root)
    raw_path = os.path.join(root_bytes, b"runtime-\xff.js")
    fd = os.open(raw_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"runtime")
    finally:
        os.close(fd)

    try:
        digest = fingerprint_support_tree(root)
    except UnicodeEncodeError as exc:  # pragma: no cover - explicit regression guard
        pytest.fail(f"raw UnicodeEncodeError escaped support fingerprinting: {exc}")

    assert len(digest) == 64


def test_verify_agent_supports_rejects_double_slash_container_destination(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    binary_path = tmp_path / "agent"
    binary_path.write_bytes(b"agent")
    binary = AgentBinary(
        "gemini",
        "0.58.0",
        binary_path,
        _sha256(b"agent"),
        support_trees=(
            AgentSupportTree(
                root,
                fingerprint_support_tree(root),
                "//opt/qualock/tree",
            ),
        ),
    )

    with pytest.raises(AgentSupportIntegrityError, match="canonical"):
        verify_agent_supports(binary)
