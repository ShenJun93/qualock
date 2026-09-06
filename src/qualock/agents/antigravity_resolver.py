import hashlib
import os
import platform
import shutil
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary


class AntigravityResolveError(RuntimeError):
    pass


def _probe_environment() -> dict[str, str]:
    # Antigravity ships a background self-updater; a resolved binary must not
    # mutate between the version probe and the SHA-256 pin.
    environment = dict(os.environ)
    environment["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    return environment


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

    def locate(self) -> Path:
        """Resolve the Antigravity binary path without executing it.

        Applies binary_path/PATH precedence and rejects a resolved Windows
        executable or a missing/non-file path. Never runs a process, so it is
        safe to call without probing version/help or touching auth state.
        """
        binary_path = self._locate_binary()
        resolved_path = binary_path.resolve()

        if resolved_path.suffix.lower() == ".exe":
            raise AntigravityResolveError(
                f"Antigravity resolver requires a native Linux binary, got {binary_path}"
            )

        if not resolved_path.is_file():
            raise AntigravityResolveError(f"Antigravity binary not found: {binary_path}")

        return resolved_path

    def resolve(self, version: str) -> AgentBinary:
        if platform.system() != "Linux":
            binary_path = self._locate_binary()
            raise AntigravityResolveError(
                f"Antigravity resolver requires a native Linux binary, got {binary_path}"
            )

        resolved_path = self.locate()

        environment = _probe_environment()
        version_result = run_process(
            [str(resolved_path), "--version"], env=environment, timeout_seconds=10
        )
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

        help_result = run_process(
            [str(resolved_path), "--help"], env=environment, timeout_seconds=10
        )
        if help_result.timed_out or help_result.exit_code != 0:
            raise AntigravityResolveError(
                help_result.stderr.strip() or "failed to inspect Antigravity CLI contract"
            )
        # agy 1.1.27 prints its whole "Usage of agy:" flag table to stderr, so the
        # CLI contract must be checked against both streams.
        help_text = help_result.stdout + help_result.stderr
        for flag in _REQUIRED_CLI_FLAGS:
            if flag not in help_text:
                raise AntigravityResolveError(f"Antigravity binary missing required CLI flag {flag}")

        digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        return AgentBinary(name="antigravity", version=version, path=resolved_path, sha256=digest)
