from pathlib import Path

import pytest

from qualock.agents.claude_resolver import ClaudeResolveError, ClaudeResolver
from qualock.run.process import ProcessResult


def make_fake_npm(path: Path, *, create_binary: bool = True) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[:3] == ['view', '@anthropic-ai/claude-code', 'version']:
    print('2.1.260')
    raise SystemExit(0)
if args and args[0] == 'install':
    prefix = pathlib.Path(args[args.index('--prefix') + 1])
    spec = next(x for x in args if x.startswith('@anthropic-ai/claude-code-linux-'))
    package, version = spec.rsplit('@', 1)
    package_name = package.removeprefix('@anthropic-ai/')
    if CREATE_BINARY:
        binary = prefix / 'node_modules' / '@anthropic-ai' / package_name / 'claude'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(('native-claude-' + version).encode())
        binary.chmod(0o755)
    raise SystemExit(0)
raise SystemExit(2)
""".replace("CREATE_BINARY", "True" if create_binary else "False"),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_resolves_exact_x86_64_native_binary(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    binary = resolver.resolve("2.1.260")

    assert binary.name == "claude"
    assert binary.version == "2.1.260"
    assert binary.path == (
        tmp_path
        / "cache/agents/claude/2.1.260/node_modules/@anthropic-ai/claude-code-linux-x64/claude"
    )
    assert len(binary.sha256) == 64
    assert binary.support_binaries == ()


def test_arm64_uses_native_arm64_package(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="aarch64")

    binary = resolver.resolve("2.1.260")

    assert "claude-code-linux-arm64" in str(binary.path)


def test_latest_is_resolved_before_install(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    binary = resolver.resolve("latest")

    assert binary.version == "2.1.260"
    assert "2.1.260" in binary.path.parts


def test_latest_version_queries_metadata_without_cache(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    cache = tmp_path / "cache"
    resolver = ClaudeResolver(cache, npm_executable=str(npm), machine="x86_64")

    assert resolver.latest_version() == "2.1.260"
    assert not cache.exists()


def test_cached_binary_is_reused_without_npm(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    first = resolver.resolve("2.1.260")
    npm.unlink()

    second = resolver.resolve("2.1.260")

    assert second == first


def test_unsupported_architecture_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ClaudeResolveError, match="unsupported architecture"):
        ClaudeResolver(tmp_path / "cache", machine="riscv64").resolve("2.1.260")


def test_invalid_version_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ClaudeResolveError, match="invalid Claude version"):
        ClaudeResolver(tmp_path / "cache", machine="x86_64").resolve("not/version")


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(None, "", "registry timeout", 30.0, True), "registry timeout"),
        (ProcessResult(1, "", "registry failed", 0.02, False), "registry failed"),
        (ProcessResult(0, "not-a-version\n", "", 0.01, False), "unexpected Claude version"),
    ],
)
def test_latest_version_rejects_bad_registry_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: ProcessResult, message: str
) -> None:
    monkeypatch.setattr("qualock.agents.claude_resolver.run_process", lambda *args, **kwargs: result)

    with pytest.raises(ClaudeResolveError, match=message):
        ClaudeResolver(tmp_path / "cache", machine="x86_64").latest_version()


def test_missing_binary_after_install_is_rejected(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm", create_binary=False)
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    with pytest.raises(ClaudeResolveError, match="native binary missing"):
        resolver.resolve("2.1.260")
