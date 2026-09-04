from pathlib import Path

import pytest

from qualock.agents.claude_resolver import ClaudeResolveError, ClaudeResolver
from qualock.run.process import ProcessResult


def make_fake_npm(
    path: Path,
    *,
    create_binary: bool = True,
    missing_flag: str | None = None,
    reported_version: str | None = None,
) -> Path:
    flags = [
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
    ]
    if missing_flag is not None:
        flags.remove(missing_flag)
    help_text = " ".join(flags)
    binary_template = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--version' in args:\n"
        "    print(REPORTED + ' (Claude Code)')\n"
        "    raise SystemExit(0)\n"
        "if '--help' in args:\n"
        "    print(HELP_TEXT)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(0)\n"
    )
    binary_template = binary_template.replace("REPORTED", repr(reported_version or "__VERSION__"))
    binary_template = binary_template.replace("HELP_TEXT", repr(help_text))
    npm_script = (
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args[:3] == ['view', '@anthropic-ai/claude-code', 'version']:\n"
        "    print('2.1.260')\n"
        "    raise SystemExit(0)\n"
        "if args and args[0] == 'install':\n"
        "    prefix = pathlib.Path(args[args.index('--prefix') + 1])\n"
        "    spec = next(x for x in args if x.startswith('@anthropic-ai/claude-code-linux-'))\n"
        "    package, version = spec.rsplit('@', 1)\n"
        "    package_name = package.removeprefix('@anthropic-ai/')\n"
        f"    if {create_binary!r}:\n"
        "        binary = prefix / 'node_modules' / '@anthropic-ai' / package_name / 'claude'\n"
        "        binary.parent.mkdir(parents=True, exist_ok=True)\n"
        f"        template = {binary_template!r}\n"
        "        template = template.replace('__VERSION__', version)\n"
        "        binary.write_text(template)\n"
        "        binary.chmod(0o755)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    path.write_text(npm_script, encoding="utf-8")
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


def test_versions_before_validated_contract_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ClaudeResolveError, match="requires Claude Code >= 2.1.260"):
        ClaudeResolver(tmp_path / "cache", machine="x86_64").resolve("2.1.259")


def test_resolved_binary_must_report_requested_version(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm", reported_version="2.1.261")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    with pytest.raises(ClaudeResolveError, match="reported version"):
        resolver.resolve("2.1.260")


def test_resolved_binary_requires_verbose_for_stream_json_contract(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm", missing_flag="--verbose")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    with pytest.raises(ClaudeResolveError, match="missing required CLI flag --verbose"):
        resolver.resolve("2.1.260")


def test_resolved_binary_must_expose_required_cli_contract(tmp_path: Path) -> None:
    npm = make_fake_npm(tmp_path / "npm", missing_flag="--restricted")
    resolver = ClaudeResolver(tmp_path / "cache", npm_executable=str(npm), machine="x86_64")

    with pytest.raises(ClaudeResolveError, match="missing required CLI flag --restricted"):
        resolver.resolve("2.1.260")


def test_binary_mutation_during_contract_validation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    binary_path = (
        cache
        / "agents/claude/2.1.260/node_modules/@anthropic-ai/claude-code-linux-x64/claude"
    )
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"original-binary")
    resolver = ClaudeResolver(cache, machine="x86_64")

    def mutate_after_fingerprint(binary: Path, version: str) -> None:
        assert version == "2.1.260"
        binary.write_bytes(b"mutated-after-contract-start")

    monkeypatch.setattr(resolver, "_validate_binary_contract", mutate_after_fingerprint)
    with pytest.raises(ClaudeResolveError, match="changed during contract validation"):
        resolver.resolve("2.1.260")


def test_binary_contract_probes_use_scrubbed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "claude"
    binary.write_text("fake", encoding="utf-8")
    calls: list[dict[str, str] | None] = []
    flags = (
        "--safe-mode --restricted --no-session-persistence --output-format --verbose "
        "--permission-mode --permission-prompts --model --effort --tools "
        "--allowed-tools --strict-mcp-config --mcp-config --settings"
    )

    def fake_run(argv, *, timeout_seconds, env=None):
        del timeout_seconds
        calls.append(env)
        if "--version" in argv:
            return ProcessResult(0, "2.1.260 (Claude Code)\n", "", 0.01, False)
        return ProcessResult(0, flags, "", 0.01, False)

    monkeypatch.setattr("qualock.agents.claude_resolver.run_process", fake_run)
    ClaudeResolver(tmp_path / "cache", machine="x86_64")._validate_binary_contract(
        binary, "2.1.260"
    )

    assert len(calls) == 2
    for env in calls:
        assert env is not None
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


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
