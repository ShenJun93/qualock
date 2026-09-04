import json
from pathlib import Path

from qualock.agents.base import AgentBinary, AgentRuntimeDependency
from qualock.agents.claude import ClaudeAdapter
from qualock.evidence.models import AgentEvidence


def binary(tmp_path: Path) -> AgentBinary:
    path = tmp_path / "claude"
    path.write_text("fake", encoding="utf-8")
    return AgentBinary(name="claude", version="2.1.260", path=path, sha256="sha")


def test_invocation_builds_isolated_headless_command(tmp_path: Path) -> None:
    adapter = ClaudeAdapter()
    with adapter.invocation(
        binary(tmp_path),
        model="sonnet",
        reasoning_effort="high",
        prompt="Fix it",
    ) as invocation:
        argv = list(invocation.argv)
        assert invocation.container_binary_path == "/opt/qualock/claude"
        assert invocation.environment == (
            ("CLAUDE_CONFIG_DIR", "/opt/qualock/claude-home"),
            ("DISABLE_AUTOUPDATER", "1"),
        )
        assert invocation.tmpfs_mounts == ("/opt/qualock/claude-home",)
        assert argv[0] == str(tmp_path / "claude")
        assert "-p" in argv
        assert "--safe-mode" in argv
        assert "--no-session-persistence" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--permission-prompts") + 1] == "none"
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        assert argv[argv.index("--tools") + 1] == "Bash,Read,Edit,Write,Glob,Grep"
        assert argv[argv.index("--allowed-tools") + 1] == "Bash,Read,Edit,Write,Glob,Grep"
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert argv[argv.index("--settings") + 1] == "/opt/qualock/claude-settings.json"
        assert argv[-1] == "Fix it"

        settings_mount = next(
            mount for mount in invocation.mounts
            if mount.container_path == "/opt/qualock/claude-settings.json"
        )
        assert settings_mount.mode == "ro"
        payload = json.loads(settings_mount.host_path.read_text(encoding="utf-8"))
        assert payload == {
            "permissions": {
                "deny": [
                    "Read(//opt/qualock/claude-credentials-seed.json)",
                    "Read(//opt/qualock/claude-home/.credentials.json)",
                ]
            },
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "enableWeakerNestedSandbox": True,
                "network": {"deniedDomains": ["*"], "strictAllowlist": True},
                "credentials": {
                    "files": [
                        {
                            "path": "/opt/qualock/claude-credentials-seed.json",
                            "mode": "deny",
                        },
                        {
                            "path": "/opt/qualock/claude-home/.credentials.json",
                            "mode": "deny",
                        },
                    ]
                },
            },
        }
        settings_host = settings_mount.host_path

    assert not settings_host.exists()


def test_credentials_are_copied_to_temporary_read_only_seed(tmp_path: Path) -> None:
    auth_home = tmp_path / "claude-home"
    auth_home.mkdir()
    source = auth_home / ".credentials.json"
    source.write_text('{"token":"test-only"}', encoding="utf-8")
    adapter = ClaudeAdapter(auth_home=auth_home)

    with adapter.invocation(
        binary(tmp_path), model="sonnet", reasoning_effort="high", prompt="Fix it"
    ) as invocation:
        seed = next(
            mount for mount in invocation.mounts
            if mount.container_path == "/opt/qualock/claude-credentials-seed.json"
        )
        assert seed.host_path != source
        assert seed.host_path.read_text(encoding="utf-8") == '{"token":"test-only"}'
        assert seed.mode == "ro"
        assert invocation.bootstrap_copy == (
            "/opt/qualock/claude-credentials-seed.json",
            "/opt/qualock/claude-home/.credentials.json",
        )
        assert not any(str(source) == str(mount.host_path) for mount in invocation.mounts)
        seed_host = seed.host_path

    assert not seed_host.exists()


def test_missing_credentials_keeps_isolated_config_without_secret_mount(tmp_path: Path) -> None:
    auth_home = tmp_path / "claude-home"
    auth_home.mkdir()
    adapter = ClaudeAdapter(auth_home=auth_home)

    with adapter.invocation(
        binary(tmp_path), model="sonnet", reasoning_effort="medium", prompt="Fix it"
    ) as invocation:
        assert invocation.environment == (
            ("CLAUDE_CONFIG_DIR", "/opt/qualock/claude-home"),
            ("DISABLE_AUTOUPDATER", "1"),
        )
        assert invocation.tmpfs_mounts == ("/opt/qualock/claude-home",)
        assert invocation.bootstrap_copy is None
        assert all(
            mount.container_path != "/opt/qualock/claude-credentials-seed.json"
            for mount in invocation.mounts
        )
        assert any(
            mount.container_path == "/opt/qualock/claude-settings.json"
            for mount in invocation.mounts
        )


def test_parse_evidence_delegates_to_claude_stream_parser() -> None:
    evidence = ClaudeAdapter().parse_evidence(
        '{"type":"result","subtype":"success","usage":{"input_tokens":4,"output_tokens":1}}\n',
        "ignored stderr",
    )

    assert isinstance(evidence, AgentEvidence)
    assert evidence.input_tokens == 4
    assert evidence.output_tokens == 1


def test_invocation_disables_claude_autoupdater(tmp_path: Path) -> None:
    adapter = ClaudeAdapter()
    with adapter.invocation(
        binary(tmp_path), model="sonnet", reasoning_effort="high", prompt="Fix it"
    ) as invocation:
        environment = dict(invocation.environment)
        assert environment["DISABLE_AUTOUPDATER"] == "1"


def test_claude_adapter_requires_socat_runtime_dependency() -> None:
    assert ClaudeAdapter().runtime_dependencies == (
        AgentRuntimeDependency(command="socat", apt_package="socat"),
    )
