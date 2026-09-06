from pathlib import Path

import pytest

from qualock.agents import releases
from qualock.agents.claude_resolver import ClaudeResolveError
from qualock.agents.resolver import CodexResolveError


def test_default_source_maps_codex_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeCodex:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "0.152.0"

    monkeypatch.setattr(releases, "CodexResolver", FakeCodex)
    source = releases.default_latest_release_source("codex", cache_root=tmp_path)

    assert source.latest_version() == "0.152.0"
    assert observed == [tmp_path]


def test_default_source_maps_claude_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeClaude:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "2.1.260"

    monkeypatch.setattr(releases, "ClaudeResolver", FakeClaude)
    source = releases.default_latest_release_source("claude", cache_root=tmp_path)

    assert source.latest_version() == "2.1.260"
    assert observed == [tmp_path]


def test_default_source_uses_shared_cache_root_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []
    monkeypatch.setattr(releases, "default_agent_cache_root", lambda: tmp_path)

    class FakeCodex:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def latest_version(self) -> str:
            return "0.152.0"

    monkeypatch.setattr(releases, "CodexResolver", FakeCodex)
    releases.default_latest_release_source("codex")

    assert observed == [tmp_path]


def test_antigravity_release_discovery_fails_closed() -> None:
    assert not hasattr(releases, "AntigravityResolver")
    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="release discovery is unavailable for Antigravity",
    ):
        releases.default_latest_release_source("antigravity")


@pytest.mark.parametrize(
    ("agent_name", "resolver_attr", "error_type"),
    [
        ("codex", "CodexResolver", CodexResolveError),
        ("claude", "ClaudeResolver", ClaudeResolveError),
    ],
)
def test_resolver_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str,
    resolver_attr: str,
    error_type: type[Exception],
) -> None:
    class BrokenResolver:
        def __init__(self, cache_root: Path) -> None:
            del cache_root

        def latest_version(self) -> str:
            raise error_type("npm unavailable")

    monkeypatch.setattr(releases, resolver_attr, BrokenResolver)
    source = releases.default_latest_release_source(agent_name, cache_root=tmp_path)

    with pytest.raises(releases.ReleaseDiscoveryError, match="npm unavailable"):
        source.latest_version()


def test_unknown_agent_release_discovery_is_rejected() -> None:
    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="release discovery is unavailable for agent 'other'",
    ):
        releases.default_latest_release_source("other")


def test_default_stable_catalog_maps_codex_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeCodex:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def stable_versions(self) -> tuple[str, ...]:
            return ("0.150.0", "0.151.0")

    monkeypatch.setattr(releases, "CodexResolver", FakeCodex)
    catalog = releases.default_stable_release_catalog("codex", cache_root=tmp_path)

    assert catalog.stable_versions() == ("0.150.0", "0.151.0")
    assert observed == [tmp_path]


def test_default_stable_catalog_maps_claude_and_preserves_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    class FakeClaude:
        def __init__(self, cache_root: Path) -> None:
            observed.append(cache_root)

        def stable_versions(self) -> tuple[str, ...]:
            return ("2.1.260", "2.1.261")

    monkeypatch.setattr(releases, "ClaudeResolver", FakeClaude)
    catalog = releases.default_stable_release_catalog("claude", cache_root=tmp_path)

    assert catalog.stable_versions() == ("2.1.260", "2.1.261")
    assert observed == [tmp_path]


@pytest.mark.parametrize(
    ("agent_name", "resolver_attr", "error_type"),
    [
        ("codex", "CodexResolver", CodexResolveError),
        ("claude", "ClaudeResolver", ClaudeResolveError),
    ],
)
def test_stable_catalog_resolver_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str,
    resolver_attr: str,
    error_type: type[Exception],
) -> None:
    class BrokenResolver:
        def __init__(self, cache_root: Path) -> None:
            del cache_root

        def stable_versions(self) -> tuple[str, ...]:
            raise error_type("npm unavailable")

    monkeypatch.setattr(releases, resolver_attr, BrokenResolver)
    catalog = releases.default_stable_release_catalog(agent_name, cache_root=tmp_path)

    with pytest.raises(releases.ReleaseDiscoveryError, match="npm unavailable"):
        catalog.stable_versions()


def test_antigravity_stable_release_discovery_fails_closed() -> None:
    assert not hasattr(releases, "AntigravityResolver")
    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="unavailable for Antigravity",
    ):
        releases.default_stable_release_catalog("antigravity")


def test_unknown_agent_stable_release_discovery_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_calls: list[Path] = []
    claude_calls: list[Path] = []

    class TrackingCodex:
        def __init__(self, cache_root: Path) -> None:
            codex_calls.append(cache_root)

        def stable_versions(self) -> tuple[str, ...]:
            return ()

    class TrackingClaude:
        def __init__(self, cache_root: Path) -> None:
            claude_calls.append(cache_root)

        def stable_versions(self) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(releases, "CodexResolver", TrackingCodex)
    monkeypatch.setattr(releases, "ClaudeResolver", TrackingClaude)

    with pytest.raises(
        releases.ReleaseDiscoveryError,
        match="release discovery is unavailable for agent 'other'",
    ):
        releases.default_stable_release_catalog("other")

    assert codex_calls == []
    assert claude_calls == []
