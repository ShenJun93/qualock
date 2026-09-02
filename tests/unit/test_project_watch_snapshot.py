from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from qualock.project_watch.models import FileStamp
from qualock.project_watch.snapshot import ProjectWatchSnapshotError, capture_project_snapshot
from qualock.run.process import ProcessResult


def _result(*, stdout: str = "", exit_code: int | None = 0, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        elapsed_seconds=0.01,
        timed_out=timed_out,
    )


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)


def test_capture_snapshot_discovers_tracked_and_untracked_but_not_ignored(tmp_path: Path) -> None:
    _init_git(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore", "tracked.txt"], check=True)
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    snapshot = capture_project_snapshot(tmp_path)

    assert tuple(item.path for item in snapshot.files) == (".gitignore", "new.txt", "tracked.txt")
    assert all(item.present for item in snapshot.files)


def test_capture_snapshot_keeps_tracked_deleted_path_as_missing(tmp_path: Path) -> None:
    _init_git(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    tracked.unlink()

    snapshot = capture_project_snapshot(tmp_path)

    assert snapshot.files == (
        FileStamp(path="tracked.txt", present=False, mode=None, size=None, mtime_ns=None),
    )


def test_capture_snapshot_excludes_qualock_and_git_paths_even_if_discovery_returns_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "source.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".qualock").mkdir()
    (tmp_path / ".qualock" / "results.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "qualock.project_watch.snapshot.run_process",
        lambda *args, **kwargs: _result(stdout="source.py\0.qualock/results.json\0.git/config\0"),
    )

    snapshot = capture_project_snapshot(tmp_path)

    assert tuple(item.path for item in snapshot.files) == ("source.py",)


def test_snapshot_identity_changes_when_file_metadata_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "qualock.project_watch.snapshot.run_process",
        lambda *args, **kwargs: _result(stdout="source.py\0"),
    )
    first = capture_project_snapshot(tmp_path)

    source.write_text("x = 1000\n", encoding="utf-8")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = capture_project_snapshot(tmp_path)

    assert first != second


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_result(exit_code=1), "git file discovery failed"),
        (_result(exit_code=None, timed_out=True), "git file discovery timed out"),
    ],
)
def test_capture_snapshot_rejects_failed_git_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: ProcessResult,
    message: str,
) -> None:
    monkeypatch.setattr("qualock.project_watch.snapshot.run_process", lambda *args, **kwargs: result)

    with pytest.raises(ProjectWatchSnapshotError, match=message):
        capture_project_snapshot(tmp_path)


def test_capture_snapshot_wraps_git_execution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr("qualock.project_watch.snapshot.run_process", fail)

    with pytest.raises(ProjectWatchSnapshotError, match="unable to discover project files"):
        capture_project_snapshot(tmp_path)


def test_capture_snapshot_rejects_unsafe_discovered_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qualock.project_watch.snapshot.run_process",
        lambda *args, **kwargs: _result(stdout="../outside.txt\0"),
    )

    with pytest.raises(ProjectWatchSnapshotError, match="unsafe project path"):
        capture_project_snapshot(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits backslash in filenames")
def test_capture_snapshot_preserves_backslash_in_posix_filename(tmp_path: Path) -> None:
    _init_git(tmp_path)
    name = "folder\\name.txt"
    source = tmp_path / name
    source.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", name], check=True)

    snapshot = capture_project_snapshot(tmp_path)

    assert tuple(item.path for item in snapshot.files) == (name,)
    assert snapshot.files[0].present is True
