import subprocess
from pathlib import Path

from qualock.source.git import GitSourceManager


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def test_materializes_exact_detached_sha_and_reuses_cache(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", cwd=origin)
    git("config", "user.email", "test@example.com", cwd=origin)
    git("config", "user.name", "Test", cwd=origin)
    (origin / "value.txt").write_text("one\n", encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "-m", "one", cwd=origin)
    first_sha = git("rev-parse", "HEAD", cwd=origin)
    (origin / "value.txt").write_text("two\n", encoding="utf-8")
    git("commit", "-am", "two", cwd=origin)

    manager = GitSourceManager(tmp_path / "cache")
    first = manager.materialize(str(origin), first_sha, tmp_path / "work-1")
    assert (first / "value.txt").read_text(encoding="utf-8") == "one\n"
    assert git("rev-parse", "HEAD", cwd=first) == first_sha
    assert git("branch", "--show-current", cwd=first) == ""

    origin.rename(tmp_path / "origin-moved")
    second = manager.materialize(str(origin), first_sha, tmp_path / "work-2")
    assert (second / "value.txt").read_text(encoding="utf-8") == "one\n"


def test_rejects_existing_destination(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", cwd=origin)
    git("config", "user.email", "test@example.com", cwd=origin)
    git("config", "user.name", "Test", cwd=origin)
    (origin / "x").write_text("x", encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "-m", "x", cwd=origin)
    sha = git("rev-parse", "HEAD", cwd=origin)
    destination = tmp_path / "work"
    destination.mkdir()
    manager = GitSourceManager(tmp_path / "cache")
    try:
        manager.materialize(str(origin), sha, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")


def test_materialized_checkout_excludes_future_git_objects(tmp_path: Path) -> None:
    origin = tmp_path / "origin-history"
    origin.mkdir()
    git("init", cwd=origin)
    git("config", "user.email", "test@example.com", cwd=origin)
    git("config", "user.name", "Test", cwd=origin)
    (origin / "value.txt").write_text("historical\n", encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "-m", "historical", cwd=origin)
    historical_sha = git("rev-parse", "HEAD", cwd=origin)
    (origin / "value.txt").write_text("future fix\n", encoding="utf-8")
    git("commit", "-am", "future fix", cwd=origin)
    future_sha = git("rev-parse", "HEAD", cwd=origin)

    manager = GitSourceManager(tmp_path / "cache")
    checkout = manager.materialize(str(origin), historical_sha, tmp_path / "historical")

    assert git("rev-parse", "HEAD", cwd=checkout) == historical_sha
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{future_sha}^{{commit}}"], cwd=checkout
    )
    assert probe.returncode != 0
    assert git("remote", cwd=checkout) == ""
