import contextlib
import errno
import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .base import AgentBinary

_HAS_POSIX_DIR_FD = os.name == "posix" and os.open in os.supports_dir_fd


class AgentSupportIntegrityError(RuntimeError):
    pass


MountTuple = tuple[Path, str, str]


@dataclass(frozen=True)
class VerifiedAgentMaterialization:
    agent_binary: Path
    mounts: tuple[MountTuple, ...]


def _canonical_record_bytes(relative_path: str, file_digest: str) -> bytes:
    return f"{relative_path}\0{file_digest}\n".encode("utf-8", errors="surrogateescape")


def _digest_records(records: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for relative_path, file_digest in sorted(records):
        digest.update(_canonical_record_bytes(relative_path, file_digest))
    return digest.hexdigest()


def _domain_open_error(path: object, exc: OSError) -> AgentSupportIntegrityError:
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        return AgentSupportIntegrityError(
            f"agent support path is a symlink or non-directory component: {path}"
        )
    return AgentSupportIntegrityError(f"agent support path is unavailable: {path}")


def _secure_open_posix(path: Path, *, directory: bool) -> int:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise AgentSupportIntegrityError(f"agent support path must be absolute: {path}")

    root_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_DIRECTORY
    current_fd = os.open("/", root_flags)
    try:
        parts = absolute.parts[1:]
        if not parts:
            if directory:
                return os.dup(current_fd)
            raise AgentSupportIntegrityError("agent support file cannot be filesystem root")
        for index, component in enumerate(parts):
            if component in {"", ".", ".."}:
                raise AgentSupportIntegrityError(f"agent support path is non-canonical: {path}")
            is_last = index == len(parts) - 1
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            if not is_last or directory:
                flags |= os.O_DIRECTORY
            try:
                child_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                raise _domain_open_error(path, exc) from exc
            os.close(current_fd)
            current_fd = child_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _portable_lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise AgentSupportIntegrityError(f"agent support path is unavailable: {path}") from exc


def _open_file(path: Path) -> int:
    if _HAS_POSIX_DIR_FD:
        fd = _secure_open_posix(path, directory=False)
    else:  # Windows CI compatibility; runtime Docker proof is POSIX.
        metadata = _portable_lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            raise AgentSupportIntegrityError(f"agent support path is a symlink: {path}")
        if not stat.S_ISREG(metadata.st_mode):
            raise AgentSupportIntegrityError(f"agent support path is non-regular: {path}")
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as exc:
            raise AgentSupportIntegrityError(f"cannot open agent support file: {path}") from exc

    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(fd)
        raise AgentSupportIntegrityError(f"agent support path is non-regular: {path}")
    return fd


def _read_and_hash_fd(fd: int, destination: Path | None = None) -> str:
    digest = hashlib.sha256()
    destination_fd: int | None = None
    if destination is not None:
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if destination_fd is not None:
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
    except OSError as exc:
        raise AgentSupportIntegrityError("cannot read agent support file") from exc
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
    return digest.hexdigest()


def _fingerprint_regular_file(path: Path) -> str:
    fd = _open_file(path)
    try:
        return _read_and_hash_fd(fd)
    finally:
        os.close(fd)


def _copy_regular_fd(fd: int, destination: Path, metadata: os.stat_result) -> str:
    digest = _read_and_hash_fd(fd, destination)
    mode = stat.S_IMODE(metadata.st_mode) & ~0o222
    os.chmod(destination, mode)
    return digest


def _walk_tree_fd(
    directory_fd: int,
    *,
    destination: Path | None,
    relative_prefix: str,
    records: list[tuple[str, str]],
) -> None:
    try:
        entries = list(os.scandir(directory_fd))
    except OSError as exc:
        raise AgentSupportIntegrityError("cannot scan agent support tree directory") from exc

    for entry in entries:
        relative_path = f"{relative_prefix}{entry.name}"
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise AgentSupportIntegrityError(
                    f"agent support tree contains a symlink: {relative_path}"
                ) from exc
            raise AgentSupportIntegrityError(
                f"cannot open agent support tree node: {relative_path}"
            ) from exc

        try:
            metadata = os.fstat(child_fd)
            destination_path = destination / entry.name if destination is not None else None
            if stat.S_ISDIR(metadata.st_mode):
                if destination_path is not None:
                    destination_path.mkdir(mode=0o700)
                _walk_tree_fd(
                    child_fd,
                    destination=destination_path,
                    relative_prefix=f"{relative_path}/",
                    records=records,
                )
                if destination_path is not None:
                    os.chmod(
                        destination_path,
                        stat.S_IMODE(metadata.st_mode) & ~0o222,
                    )
            elif stat.S_ISREG(metadata.st_mode):
                if destination_path is None:
                    file_digest = _read_and_hash_fd(child_fd)
                else:
                    file_digest = _copy_regular_fd(child_fd, destination_path, metadata)
                records.append((relative_path, file_digest))
            else:
                raise AgentSupportIntegrityError(
                    f"agent support tree contains a non-regular node: {relative_path}"
                )
        finally:
            os.close(child_fd)


def _portable_copy_and_fingerprint_tree(root: Path, destination: Path | None) -> str:
    metadata = _portable_lstat(root)
    if stat.S_ISLNK(metadata.st_mode):
        raise AgentSupportIntegrityError(f"agent support tree root is a symlink: {root}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise AgentSupportIntegrityError(f"agent support tree root is not a real directory: {root}")
    if destination is not None:
        destination.mkdir(mode=0o700)

    records: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        node_metadata = _portable_lstat(path)
        relative_path = path.relative_to(root).as_posix()
        if stat.S_ISLNK(node_metadata.st_mode):
            raise AgentSupportIntegrityError(
                f"agent support tree contains a symlink: {relative_path}"
            )
        if stat.S_ISDIR(node_metadata.st_mode):
            if destination is not None:
                target = destination / path.relative_to(root)
                target.mkdir(mode=0o700, exist_ok=True)
            continue
        if not stat.S_ISREG(node_metadata.st_mode):
            raise AgentSupportIntegrityError(
                f"agent support tree contains a non-regular node: {relative_path}"
            )
        if destination is None:
            file_digest = _fingerprint_regular_file(path)
        else:
            target = destination / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = _open_file(path)
            try:
                file_digest = _copy_regular_fd(fd, target, node_metadata)
            finally:
                os.close(fd)
        records.append((relative_path, file_digest))
    if destination is not None:
        os.chmod(destination, stat.S_IMODE(metadata.st_mode) & ~0o222)
    return _digest_records(records)


def _copy_and_fingerprint_tree(root: Path, destination: Path | None = None) -> str:
    if not _HAS_POSIX_DIR_FD:
        return _portable_copy_and_fingerprint_tree(root, destination)

    root_fd = _secure_open_posix(root, directory=True)
    try:
        root_metadata = os.fstat(root_fd)
        if destination is not None:
            destination.mkdir(mode=0o700)
        records: list[tuple[str, str]] = []
        _walk_tree_fd(
            root_fd,
            destination=destination,
            relative_prefix="",
            records=records,
        )
        if destination is not None:
            os.chmod(destination, stat.S_IMODE(root_metadata.st_mode) & ~0o222)
        return _digest_records(records)
    finally:
        os.close(root_fd)


def fingerprint_support_tree(root: Path) -> str:
    return _copy_and_fingerprint_tree(root)


def agent_support_fingerprint(binary: AgentBinary) -> str | None:
    records = [
        f"B\0{support.container_path}\0{support.sha256}\n" for support in binary.support_binaries
    ]
    records.extend(f"T\0{tree.container_root}\0{tree.sha256}\n" for tree in binary.support_trees)
    if not records:
        return None
    digest = hashlib.sha256()
    for record in sorted(records):
        digest.update(record.encode())
    return digest.hexdigest()


def _container_destination(value: str) -> PurePosixPath:
    destination = PurePosixPath(value)
    if (
        not destination.is_absolute()
        or value.startswith("//")
        or str(destination) != value
        or ".." in destination.parts
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise AgentSupportIntegrityError(
            f"agent support container destination must be absolute and canonical: {value}"
        )
    return destination


def _verify_container_destinations(binary: AgentBinary) -> None:
    destinations = [
        _container_destination(support.container_path) for support in binary.support_binaries
    ]
    destinations.extend(
        _container_destination(tree.container_root) for tree in binary.support_trees
    )
    for index, destination in enumerate(destinations):
        for other in destinations[index + 1 :]:
            if destination == other or destination in other.parents or other in destination.parents:
                raise AgentSupportIntegrityError(
                    "agent support container destinations are overlapping: "
                    f"{destination} and {other}"
                )


def verify_agent_supports(binary: AgentBinary) -> None:
    _verify_container_destinations(binary)
    for support in binary.support_binaries:
        actual = _fingerprint_regular_file(support.path)
        if actual != support.sha256:
            raise AgentSupportIntegrityError(
                f"agent support binary fingerprint mismatch: {support.path}"
            )
    for tree in binary.support_trees:
        actual = fingerprint_support_tree(tree.root)
        if actual != tree.sha256:
            raise AgentSupportIntegrityError(
                f"agent support tree fingerprint mismatch: {tree.root}"
            )


def _relative_binary_in_tree(binary: AgentBinary, tree_root: Path) -> Path | None:
    try:
        return binary.path.absolute().relative_to(tree_root.absolute())
    except ValueError:
        return None


@contextlib.contextmanager
def materialize_verified_supports(
    binary: AgentBinary,
) -> Iterator[VerifiedAgentMaterialization]:
    _verify_container_destinations(binary)
    with tempfile.TemporaryDirectory(prefix="qualock-snapshot-") as temp:
        temp_root = Path(temp)
        mounts: list[MountTuple] = []
        materialized_binary = binary.path
        binary_tree_matches = 0

        for index, support in enumerate(binary.support_binaries):
            source_fd = _open_file(support.path)
            try:
                metadata = os.fstat(source_fd)
                destination_dir = temp_root / f"binary-{index}"
                destination_dir.mkdir(mode=0o700)
                destination = destination_dir / "support"
                actual = _copy_regular_fd(source_fd, destination, metadata)
            finally:
                os.close(source_fd)
            if actual != support.sha256:
                raise AgentSupportIntegrityError(
                    f"agent support binary fingerprint mismatch: {support.path}"
                )
            mounts.append((destination, support.container_path, "ro"))

        for index, tree in enumerate(binary.support_trees):
            destination = temp_root / f"tree-{index}"
            actual = _copy_and_fingerprint_tree(tree.root, destination)
            if actual != tree.sha256:
                raise AgentSupportIntegrityError(
                    f"agent support tree fingerprint mismatch: {tree.root}"
                )
            mounts.append((destination, tree.container_root, "ro"))

            relative_binary = _relative_binary_in_tree(binary, tree.root)
            if relative_binary is not None:
                binary_tree_matches += 1
                candidate = destination / relative_binary
                candidate_digest = _fingerprint_regular_file(candidate)
                if candidate_digest != binary.sha256:
                    raise AgentSupportIntegrityError(
                        "agent entrypoint fingerprint does not match verified support tree"
                    )
                materialized_binary = candidate

        if binary_tree_matches > 1:
            raise AgentSupportIntegrityError("agent binary is contained by multiple support trees")
        yield VerifiedAgentMaterialization(
            agent_binary=materialized_binary,
            mounts=tuple(mounts),
        )
