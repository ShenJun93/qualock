from pathlib import Path
from typing import Protocol

from platformdirs import user_cache_dir

from .claude_resolver import ClaudeResolveError, ClaudeResolver
from .gemini_resolver import GeminiResolveError, GeminiResolver
from .resolver import CodexResolveError, CodexResolver


class ReleaseDiscoveryError(RuntimeError):
    pass


class LatestReleaseSource(Protocol):
    def latest_version(self) -> str: ...


def default_agent_cache_root() -> Path:
    return Path(user_cache_dir("qualock"))


class _CodexLatestReleaseSource:
    def __init__(self, resolver: CodexResolver) -> None:
        self.resolver = resolver

    def latest_version(self) -> str:
        try:
            return self.resolver.latest_version()
        except CodexResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class _ClaudeLatestReleaseSource:
    def __init__(self, resolver: ClaudeResolver) -> None:
        self.resolver = resolver

    def latest_version(self) -> str:
        try:
            return self.resolver.latest_version()
        except ClaudeResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class _GeminiLatestReleaseSource:
    def __init__(self, resolver: GeminiResolver) -> None:
        self.resolver = resolver

    def latest_version(self) -> str:
        try:
            return self.resolver.latest_version()
        except GeminiResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class StableReleaseCatalog(Protocol):
    def stable_versions(self) -> tuple[str, ...]: ...


class _CodexStableReleaseCatalog:
    def __init__(self, resolver: CodexResolver) -> None:
        self.resolver = resolver

    def stable_versions(self) -> tuple[str, ...]:
        try:
            return self.resolver.stable_versions()
        except CodexResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class _ClaudeStableReleaseCatalog:
    def __init__(self, resolver: ClaudeResolver) -> None:
        self.resolver = resolver

    def stable_versions(self) -> tuple[str, ...]:
        try:
            return self.resolver.stable_versions()
        except ClaudeResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


class _GeminiStableReleaseCatalog:
    def __init__(self, resolver: GeminiResolver) -> None:
        self.resolver = resolver

    def stable_versions(self) -> tuple[str, ...]:
        try:
            return self.resolver.stable_versions()
        except GeminiResolveError as exc:
            raise ReleaseDiscoveryError(str(exc)) from exc


def default_stable_release_catalog(
    agent_name: str,
    *,
    cache_root: Path | None = None,
) -> StableReleaseCatalog:
    cache = cache_root or default_agent_cache_root()
    if agent_name == "codex":
        return _CodexStableReleaseCatalog(CodexResolver(cache))
    if agent_name == "claude":
        return _ClaudeStableReleaseCatalog(ClaudeResolver(cache))
    if agent_name == "gemini":
        return _GeminiStableReleaseCatalog(GeminiResolver(cache))
    if agent_name == "antigravity":
        raise ReleaseDiscoveryError(
            "release discovery is unavailable for Antigravity"
        )
    raise ReleaseDiscoveryError(
        f"release discovery is unavailable for agent {agent_name!r}"
    )


def default_latest_release_source(
    agent_name: str,
    *,
    cache_root: Path | None = None,
) -> LatestReleaseSource:
    cache = cache_root or default_agent_cache_root()
    if agent_name == "codex":
        return _CodexLatestReleaseSource(CodexResolver(cache))
    if agent_name == "claude":
        return _ClaudeLatestReleaseSource(ClaudeResolver(cache))
    if agent_name == "gemini":
        return _GeminiLatestReleaseSource(GeminiResolver(cache))
    if agent_name == "antigravity":
        raise ReleaseDiscoveryError(
            "release discovery is unavailable for Antigravity"
        )
    raise ReleaseDiscoveryError(
        f"release discovery is unavailable for agent {agent_name!r}"
    )
