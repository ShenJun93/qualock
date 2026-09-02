from __future__ import annotations

from pathlib import Path, PurePosixPath

from qualock.run.process import run_process

from .models import FileStamp, ProjectSnapshot


class ProjectWatchSnapshotError(RuntimeError):
    pass


def _normalize_path(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ProjectWatchSnapshotError(f"unsafe project path from Git: {raw!r}")
    return path.as_posix()


def _excluded(path: str) -> bool:
    return path in {".git", ".qualock"} or path.startswith((".git/", ".qualock/"))


def _discover_paths(root: Path) -> tuple[str, ...]:
    try:
        result = run_process(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            timeout_seconds=5,
        )
    except (OSError, UnicodeError) as exc:
        raise ProjectWatchSnapshotError(f"unable to discover project files: {exc}") from exc
    if result.timed_out:
        raise ProjectWatchSnapshotError("git file discovery timed out")
    if result.exit_code != 0:
        raise ProjectWatchSnapshotError("git file discovery failed")

    paths: set[str] = set()
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        normalized = _normalize_path(raw)
        if not _excluded(normalized):
            paths.add(normalized)
    return tuple(sorted(paths))


def _stamp(root: Path, relative: str) -> FileStamp:
    path = root / Path(*PurePosixPath(relative).parts)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return FileStamp(
            path=relative,
            present=False,
            mode=None,
            size=None,
            mtime_ns=None,
        )
    except OSError as exc:
        raise ProjectWatchSnapshotError(
            f"unable to inspect project path {relative!r}: {exc}"
        ) from exc
    return FileStamp(
        path=relative,
        present=True,
        mode=info.st_mode,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
    )


def capture_project_snapshot(root: Path) -> ProjectSnapshot:
    files = tuple(_stamp(root, relative) for relative in _discover_paths(root))
    return ProjectSnapshot(files=files)
