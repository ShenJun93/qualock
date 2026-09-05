import hashlib
import platform
import shutil
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary


class AntigravityResolveError(RuntimeError):
    pass


_REQUIRED_CLI_FLAGS = (
    "--sandbox",
    "--output-format",
    "--model",
    "--effort",
    "--new-project",
    "--disable-slash-commands",
)


class AntigravityResolver:
    def __init__(self, binary_path: Path | None = None) -> None:
        self.binary_path = binary_path

    def _locate_binary(self) -> Path:
        if self.binary_path is not None:
            return self.binary_path
        found = shutil.which("agy")
        if found is None:
            raise AntigravityResolveError(
                "no Antigravity binary found: pass binary_path or install agy on PATH"
            )
        return Path(found)

    def resolve(self, version: str) -> AgentBinary:
        binary_path = self._locate_binary()

        if platform.system() != "Linux" or binary_path.suffix == ".exe":
            raise AntigravityResolveError(
                f"Antigravity resolver requires a native Linux binary, got {binary_path}"
            )

        if not binary_path.is_file():
            raise AntigravityResolveError(f"Antigravity binary not found: {binary_path}")

        version_result = run_process([str(binary_path), "--version"], timeout_seconds=10)
        if version_result.timed_out or version_result.exit_code != 0:
            raise AntigravityResolveError(
                version_result.stderr.strip() or "failed to inspect Antigravity version"
            )
        version_output = version_result.stdout.strip()
        if not version_output:
            raise AntigravityResolveError("Antigravity binary returned empty version output")
        reported = version_output.split()[0]
        if reported != version:
            raise AntigravityResolveError(
                f"Antigravity binary reports version {reported!r}, requested {version}"
            )

        help_result = run_process([str(binary_path), "--help"], timeout_seconds=10)
        if help_result.timed_out or help_result.exit_code != 0:
            raise AntigravityResolveError(
                help_result.stderr.strip() or "failed to inspect Antigravity CLI contract"
            )
        help_text = help_result.stdout
        for flag in _REQUIRED_CLI_FLAGS:
            if flag not in help_text:
                raise AntigravityResolveError(f"Antigravity binary missing required CLI flag {flag}")

        resolved_path = binary_path.resolve()
        digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        return AgentBinary(name="antigravity", version=version, path=resolved_path, sha256=digest)
