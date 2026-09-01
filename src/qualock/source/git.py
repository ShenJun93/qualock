import hashlib
import shutil
import subprocess
from pathlib import Path


class GitSourceError(RuntimeError):
    pass


class GitSourceManager:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self.mirrors_root = cache_root / "git"

    def _mirror_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.mirrors_root / key

    def _run(self, args: list[str], *, cwd: Path | None = None) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise GitSourceError(exc.stderr.strip() or "git command failed") from exc
        return result.stdout.strip()

    def _ensure_commit(self, mirror: Path, sha: str) -> None:
        probe = subprocess.run(
            ["git", "--git-dir", str(mirror), "cat-file", "-e", f"{sha}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            return
        self._run(["git", "--git-dir", str(mirror), "fetch", "--prune", "origin"])
        self._run(["git", "--git-dir", str(mirror), "cat-file", "-e", f"{sha}^{{commit}}"])

    def materialize(self, url: str, sha: str, destination: Path) -> Path:
        if destination.exists():
            raise FileExistsError(destination)
        mirror = self._mirror_path(url)
        if not mirror.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            self._run(["git", "clone", "--mirror", url, str(mirror)])
        self._ensure_commit(mirror, sha)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run(["git", "init", str(destination)])
            self._run(
                ["git", "fetch", "--depth=1", "--no-tags", str(mirror), sha],
                cwd=destination,
            )
            self._run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return destination
