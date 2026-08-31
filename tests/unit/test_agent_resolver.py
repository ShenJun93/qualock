from pathlib import Path

from qualock.agents.resolver import CodexResolver


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
    binary = prefix / 'node_modules' / '@openai' / 'codex' / 'node_modules' / '@openai' / 'codex-linux-x64' / 'vendor' / 'x86_64-unknown-linux-musl' / 'bin' / 'codex'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(('native-codex-' + version).encode())
    binary.chmod(0o755)
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
        / "cache/agents/codex/0.150.0/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    )
    assert len(binary.sha256) == 64
    assert binary.path.is_file()


def test_latest_is_resolved_to_exact_version_before_install(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm")
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")
    binary = resolver.resolve("latest")
    assert binary.version == "0.151.0"
    assert "0.151.0" in binary.path.parts


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
binary = prefix / 'node_modules' / '@openai' / 'codex' / 'node_modules' / '@openai' / 'codex-linux-arm64' / 'vendor' / 'aarch64-unknown-linux-musl' / 'bin' / 'codex'
binary.parent.mkdir(parents=True, exist_ok=True)
binary.write_bytes(('native-codex-' + version).encode())
binary.chmod(0o755)
""",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="arm64")
    binary = resolver.resolve("0.150.0")
    assert "codex-linux-arm64" in str(binary.path)
    assert "aarch64-unknown-linux-musl" in str(binary.path)
