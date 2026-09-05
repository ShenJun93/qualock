from pathlib import Path

import pytest

from qualock.agents.resolver import CodexResolveError, CodexResolver
from qualock.run.process import ProcessResult

from ._platform_helpers import write_python_launcher


def make_fake_npm(
    path: Path,
    *,
    package: str = "codex-linux-x64",
    target: str = "x86_64-unknown-linux-musl",
) -> Path:
    source = f"""import pathlib
import sys

args = sys.argv[1:]
if args[:3] == ['view', '@openai/codex', 'version']:
    print('0.151.0')
    raise SystemExit(0)
if args and args[0] == 'install':
    prefix = pathlib.Path(args[args.index('--prefix') + 1])
    spec = next(x for x in args if x.startswith('@openai/codex@'))
    version = spec.rsplit('@', 1)[1]
    binary = prefix / 'node_modules' / '@openai' / '{package}' / 'vendor' / '{target}' / 'bin' / 'codex'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(('native-codex-' + version).encode())
    host = binary.with_name('codex-code-mode-host')
    host.write_bytes(('code-mode-host-' + version).encode())
    raise SystemExit(0)
raise SystemExit(2)
"""
    return write_python_launcher(path, source)


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


def test_stable_versions_filters_dedupes_and_sorts_numerically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, timeout_seconds: int) -> ProcessResult:
        calls.append(args)
        return ProcessResult(
            0,
            '["0.10.0","0.9.0","0.10.0","0.11.0-beta.1","1.0.0"]',
            "",
            0.01,
            False,
        )

    monkeypatch.setattr("qualock.agents.resolver.run_process", fake_run)
    cache = tmp_path / "cache"
    resolver = CodexResolver(cache, npm_executable="npm", machine="x86_64")
    assert resolver.stable_versions() == ("0.9.0", "0.10.0", "1.0.0")
    assert calls == [["npm", "view", "@openai/codex", "versions", "--json"]]
    assert not cache.exists()


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(None, "", "registry timeout", 30.0, True), "registry timeout"),
        (ProcessResult(1, "", "registry failed", 0.02, False), "registry failed"),
        (ProcessResult(0, "not-json", "", 0.01, False), "unexpected Codex versions"),
        (ProcessResult(0, '{"0":"0.150.0"}', "", 0.01, False), "unexpected Codex versions"),
        (ProcessResult(0, '["0.150.0",151]', "", 0.01, False), "unexpected Codex versions"),
    ],
)
def test_stable_versions_rejects_bad_registry_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: ProcessResult, message: str
) -> None:
    monkeypatch.setattr("qualock.agents.resolver.run_process", lambda *args, **kwargs: result)
    with pytest.raises(CodexResolveError, match=message):
        CodexResolver(tmp_path / "cache", machine="x86_64").stable_versions()


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
    npm = make_fake_npm(
        tmp_path / "npm",
        package="codex-linux-arm64",
        target="aarch64-unknown-linux-musl",
    )
    resolver = CodexResolver(tmp_path / "cache", npm_executable=str(npm), machine="arm64")
    binary = resolver.resolve("0.150.0")
    assert "codex-linux-arm64" in str(binary.path)
    assert "aarch64-unknown-linux-musl" in str(binary.path)
