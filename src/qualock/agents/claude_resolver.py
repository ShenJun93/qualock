import hashlib
import platform
import re
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary


class ClaudeResolveError(RuntimeError):
    pass


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PLATFORM_PACKAGES = {
    "x86_64": "@anthropic-ai/claude-code-linux-x64",
    "amd64": "@anthropic-ai/claude-code-linux-x64",
    "arm64": "@anthropic-ai/claude-code-linux-arm64",
    "aarch64": "@anthropic-ai/claude-code-linux-arm64",
}


class ClaudeResolver:
    def __init__(
        self,
        cache_root: Path,
        *,
        npm_executable: str = "npm",
        machine: str | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.npm_executable = npm_executable
        self.machine = (machine or platform.machine()).lower()

    def _platform_package(self) -> str:
        package = _PLATFORM_PACKAGES.get(self.machine)
        if package is None:
            raise ClaudeResolveError(
                f"unsupported architecture for Linux Claude Code binary: {self.machine!r}"
            )
        return package

    def latest_version(self) -> str:
        result = run_process(
            [self.npm_executable, "view", "@anthropic-ai/claude-code", "version"],
            timeout_seconds=30,
        )
        if result.timed_out or result.exit_code != 0:
            raise ClaudeResolveError(
                result.stderr.strip() or "failed to resolve Claude latest"
            )
        version = result.stdout.strip()
        if not _VERSION_RE.fullmatch(version):
            raise ClaudeResolveError(f"unexpected Claude version from npm: {version!r}")
        return version

    def resolve(self, requested_version: str) -> AgentBinary:
        version = self.latest_version() if requested_version == "latest" else requested_version
        if not _VERSION_RE.fullmatch(version):
            raise ClaudeResolveError(f"invalid Claude version: {version!r}")

        package = self._platform_package()
        package_name = package.removeprefix("@anthropic-ai/")
        prefix = self.cache_root / "agents" / "claude" / version
        binary = prefix / "node_modules" / "@anthropic-ai" / package_name / "claude"

        if not binary.is_file():
            prefix.mkdir(parents=True, exist_ok=True)
            result = run_process(
                [
                    self.npm_executable,
                    "install",
                    "--prefix",
                    str(prefix),
                    "--no-save",
                    f"{package}@{version}",
                ],
                timeout_seconds=180,
            )
            if result.timed_out or result.exit_code != 0:
                raise ClaudeResolveError(
                    result.stderr.strip() or "failed to install Claude Code"
                )
            if not binary.is_file():
                raise ClaudeResolveError(
                    f"Claude native binary missing after install: {binary}"
                )

        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        return AgentBinary(name="claude", version=version, path=binary, sha256=digest)
