from pathlib import Path

import pytest

from qualock.agents.resolver import CodexResolveError, CodexResolver
from qualock.run.process import ProcessResult


def make_fake_npm(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[:3] == ['view', '@openai/codex', 'version']:
    print('0.151.0')
    raise SystemExit(0)
if args and args[0] == 'install':
    prefix = pathlib.Path(args[args.index('--prefix') + 1])
    spec = next(x for x in args if x.startswith('@openai/codex@'))
    version = spec.rsplit('@', 1)[1]
    binary = prefix / 'node_modules' / '@openai' / 'codex-linux-x64' / 'vendor' / 'x86_64-unknown-linux-musl' / 'bin' / 'codex'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(('native-codex-' + version).encode())
    binary.chmod(0o755)
    host = binary.with_name('codex-code-mode-host')
    host.write_bytes(('code-mode-host-' + version).encode())
    host.chmod(0o755)
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_resolves_exact_version_to_native_linux_binary(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    binary = resolver.resolve("0.150.0")
    assert binary.version == "0.150.0"
    assert binary.path == (
        tmp_path
        / "cache/agents/codex/0.150.0/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    )
    assert len(binary.sha256) == 64
    assert binary.path.is_file()
    assert len(binary.support_binaries) == 1
    support = binary.support_binaries[0]
    assert support.name == 'codex-code-mode-host'
    assert support.path == binary.path.with_name('codex-code-mode-host')
    assert support.container_path == '/opt/qualock/codex-code-mode-host'
    assert len(support.sha256) == 64


def test_latest_is_resolved_to_exact_version_before_install(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    binary = resolver.resolve("latest")
    assert binary.version == "0.151.0"
    assert "0.151.0" in binary.path.parts


def test_latest_version_queries_metadata_without_install_or_cache(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    cache = tmp_path / "cache"
    resolver = CodexResolver(cache, npm_executable=str(npm), machine="x86_64")

    assert resolver.latest_version() == "0.151.0"
    assert not cache.exists()


def test_latest_version_rejects_malformed_registry_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(0, "not-a-version\n", "", 0.01, False),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="unexpected Codex version"):
        resolver.latest_version()


def test_latest_version_rejects_registry_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(None, "", "registry timeout", 30.0, True),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="registry timeout"):
        resolver.latest_version()


def test_latest_version_rejects_registry_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.agents.resolver.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", "registry failed", 0.02, False),
    )
    resolver = CodexResolver(tmp_path / "cache", machine="x86_64")

    with pytest.raises(CodexResolveError, match="registry failed"):
        resolver.latest_version()


def test_resolve_latest_delegates_to_public_latest_version(tmp_path: Path, monkeypatch) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    calls: list[str] = []
    monkeypatch.setattr(
        resolver,
        "latest_version",
        lambda: calls.append("latest") or "0.151.0",
    )

    binary = resolver.resolve("latest")

    assert calls == ["latest"]
    assert binary.version == "0.151.0"


def test_existing_cached_native_binary_is_reused_without_npm(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    first = resolver.resolve("0.150.0")
    npm.unlink()
    second = resolver.resolve("0.150.0")
    assert second == first


def test_arm64_resolves_linux_arm64_platform_package_path(tmp_path: Path) -> None:
    npm = tmp_path / "npm"
    npm.write_text(
        """#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
prefix = pathlib.Path(args[args.index('--prefix') + 1])
spec = next(x for x in args if x.startswith('@openai/codex@'))
version = spec.rsplit('@', 1)[1]
binary = prefix / 'node_modules' / '@openai' / 'codex-linux-arm64' / 'vendor' / 'aarch64-unknown-linux-musl' / 'bin' / 'codex'
binary.parent.mkdir(parents=True, exist_ok=True)
binary.write_bytes(('native-codex-' + version).encode())
binary.chmod(0o755)
host = binary.with_name('codex-code-mode-host')
host.write_bytes(('code-mode-host-' + version).encode())
host.chmod(0o755)
""",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="arm64")
    binary = resolver.resolve("0.150.0")
    assert "codex-linux-arm64" in str(binary.path)
    assert "aarch64-unknown-linux-musl" in str(binary.path)
