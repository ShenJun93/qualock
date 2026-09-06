from pathlib import Path
from typing import Protocol

from platformdirs import user_cache_dir

from .claude_resolver import ClaudeResolveError, ClaudeResolver
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
    if agent_name == "antigravity":
        raise ReleaseDiscoveryError(
            "release discovery is unavailable for Antigravity"
        )
    raise ReleaseDiscoveryError(
        f"release discovery is unavailable for agent {agent_name!r}"
    )
