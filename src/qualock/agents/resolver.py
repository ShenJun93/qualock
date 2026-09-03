import hashlib
import json
import platform
import re
from dataclasses import dataclass
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary, AgentSupportBinary


class CodexResolveError(RuntimeError):
    pass


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class _LinuxPlatformPackage:
    package: str
    target: str


_LINUX_PLATFORM_PACKAGES = {
    "x86_64": _LinuxPlatformPackage(
        package="@openai/codex-linux-x64",
        target="x86_64-unknown-linux-musl",
    ),
    "amd64": _LinuxPlatformPackage(
        package="@openai/codex-linux-x64",
        target="x86_64-unknown-linux-musl",
    ),
    "arm64": _LinuxPlatformPackage(
        package="@openai/codex-linux-arm64",
        target="aarch64-unknown-linux-musl",
    ),
    "aarch64": _LinuxPlatformPackage(
        package="@openai/codex-linux-arm64",
        target="aarch64-unknown-linux-musl",
    ),
}


class CodexResolver:
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

    def _platform_package(self) -> _LinuxPlatformPackage:
        package = _LINUX_PLATFORM_PACKAGES.get(self.machine)
        if package is None:
            raise CodexResolveError(
                f"unsupported architecture for Linux Codex binary: {self.machine!r}"
            )
        return package

    def latest_version(self) -> str:
        result = run_process(
            [self.npm_executable, "view", "@openai/codex", "version"],
            timeout_seconds=30,
        )
        if result.timed_out or result.exit_code != 0:
            raise CodexResolveError(result.stderr.strip() or "failed to resolve Codex latest")
        version = result.stdout.strip()
        if not _VERSION_RE.match(version):
            raise CodexResolveError(f"unexpected Codex version from npm: {version!r}")
        return version

    def stable_versions(self) -> tuple[str, ...]:
        result = run_process(
            [self.npm_executable, "view", "@openai/codex", "versions", "--json"],
            timeout_seconds=30,
        )
        if result.timed_out or result.exit_code != 0:
            raise CodexResolveError(result.stderr.strip() or "failed to resolve Codex versions")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CodexResolveError("unexpected Codex versions from npm") from exc
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise CodexResolveError("unexpected Codex versions from npm")

        stable = {item for item in payload if _STABLE_VERSION_RE.fullmatch(item)}
        return tuple(sorted(stable, key=_stable_version_key))

    def resolve(self, requested_version: str) -> AgentBinary:
        version = self.latest_version() if requested_version == "latest" else requested_version
        if not _VERSION_RE.match(version):
            raise CodexResolveError(f"invalid Codex version: {version!r}")

        platform_package = self._platform_package()
        prefix = self.cache_root / "agents" / "codex" / version
        package_dir = platform_package.package.removeprefix("@openai/")
        binary = (
            prefix
            / "node_modules"
            / "@openai"
            / package_dir
            / "vendor"
            / platform_package.target
            / "bin"
            / "codex"
        )
        support_binary = binary.with_name("codex-code-mode-host")
        if not binary.is_file() or not support_binary.is_file():
            prefix.mkdir(parents=True, exist_ok=True)
            result = run_process(
                [
                    self.npm_executable,
                    "install",
                    "--prefix",
                    str(prefix),
                    "--no-save",
                    f"@openai/codex@{version}",
                ],
                timeout_seconds=180,
            )
            if result.timed_out or result.exit_code != 0:
                raise CodexResolveError(result.stderr.strip() or "failed to install Codex")
            if not binary.is_file():
                raise CodexResolveError(f"Codex native binary missing after install: {binary}")
            if not support_binary.is_file():
                raise CodexResolveError(
                    f"Codex code-mode host missing after install: {support_binary}"
                )

        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        support_digest = hashlib.sha256(support_binary.read_bytes()).hexdigest()
        return AgentBinary(
            name="codex",
            version=version,
            path=binary,
            sha256=digest,
            support_binaries=(
                AgentSupportBinary(
                    name="codex-code-mode-host",
                    path=support_binary,
                    sha256=support_digest,
                    container_path="/opt/qualock/codex-code-mode-host",
                ),
            ),
        )


def _stable_version_key(version: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"not a stable version: {version!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)
