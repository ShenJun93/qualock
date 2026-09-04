import hashlib
import os
import platform
import re
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary


class ClaudeResolveError(RuntimeError):
    pass


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

_MIN_VALIDATED_VERSION = (2, 1, 260)
_HELP_OPTION_RE = re.compile(r"(?<!\S)(--?[A-Za-z][A-Za-z0-9-]*)(?=[,\s]|$)")


def _help_options(help_text: str) -> set[str]:
    options: set[str] = set()
    for line in help_text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("-"):
            continue
        synopsis = re.split(r"\s{2,}", stripped, maxsplit=1)[0]
        options.update(_HELP_OPTION_RE.findall(synopsis))
    return options


_REQUIRED_CLI_FLAGS = (
    "-p",
    "--safe-mode",
    "--restricted",
    "--no-session-persistence",
    "--output-format",
    "--verbose",
    "--permission-mode",
    "--permission-prompts",
    "--model",
    "--effort",
    "--tools",
    "--allowed-tools",
    "--strict-mcp-config",
    "--mcp-config",
    "--settings",
)


def _core_version(version: str) -> tuple[int, int, int]:
    core = version.split("+", 1)[0].split("-", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)

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

    def _validate_binary_contract(self, binary: Path, version: str) -> None:
        if _core_version(version) < _MIN_VALIDATED_VERSION:
            raise ClaudeResolveError(
                "QuaLock requires Claude Code >= 2.1.260 for the validated sandbox contract"
            )

        probe_env = {
            "HOME": "/nonexistent",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        version_result = run_process(
            [str(binary), "--version"], timeout_seconds=10, env=probe_env
        )
        if version_result.timed_out or version_result.exit_code != 0:
            raise ClaudeResolveError(
                version_result.stderr.strip() or "failed to inspect Claude Code version"
            )
        version_output = version_result.stdout.strip()
        if not version_output:
            raise ClaudeResolveError("Claude binary returned empty version output")
        reported = version_output.split(maxsplit=1)[0]
        if reported != version:
            raise ClaudeResolveError(
                f"Claude binary reported version {reported!r}, expected {version!r}"
            )

        help_result = run_process(
            [str(binary), "--help"], timeout_seconds=10, env=probe_env
        )
        if help_result.timed_out or help_result.exit_code != 0:
            raise ClaudeResolveError(
                help_result.stderr.strip() or "failed to inspect Claude Code CLI contract"
            )
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        help_options = _help_options(help_text)
        for flag in _REQUIRED_CLI_FLAGS:
            if flag not in help_options:
                raise ClaudeResolveError(f"Claude binary missing required CLI flag {flag}")

    def resolve(self, requested_version: str) -> AgentBinary:
        version = self.latest_version() if requested_version == "latest" else requested_version
        if not _VERSION_RE.fullmatch(version):
            raise ClaudeResolveError(f"invalid Claude version: {version!r}")
        if _core_version(version) < _MIN_VALIDATED_VERSION:
            raise ClaudeResolveError(
                "QuaLock requires Claude Code >= 2.1.260 for the validated sandbox contract"
            )

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
        self._validate_binary_contract(binary, version)
        if hashlib.sha256(binary.read_bytes()).hexdigest() != digest:
            raise ClaudeResolveError("Claude binary changed during contract validation")
        return AgentBinary(name="claude", version=version, path=binary, sha256=digest)
