import json
from pathlib import Path

from qualock.agents.base import AgentBinary, AgentRuntimeDependency
from qualock.agents.claude import ClaudeAdapter, select_claude_automation_credential
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
        assert "--restricted" in argv
        assert "--no-session-persistence" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in argv
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--permission-prompts") + 1] == "none"
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        assert argv[argv.index("--tools") + 1] == "Bash,Read,Edit,Write"
        assert argv[argv.index("--allowed-tools") + 1] == "Bash,Read,Edit,Write"
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
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "enableWeakerNestedSandbox": True,
                "network": {"deniedDomains": ["*"], "strictAllowlist": True},
                "credentials": {
                    "envVars": [
                        {"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"},
                        {"name": "ANTHROPIC_API_KEY", "mode": "deny"},
                        {"name": "CLAUDE_CODE_OAUTH_TOKEN", "mode": "deny"},
                    ]
                },
            },
        }
        settings_host = settings_mount.host_path

    assert not settings_host.exists()


def test_automation_credential_uses_stdin_secret_environment_without_metadata(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(automation_credential=("CLAUDE_CODE_OAUTH_TOKEN", "test-only"))

    with adapter.invocation(
        binary(tmp_path), model="sonnet", reasoning_effort="high", prompt="Fix it"
    ) as invocation:
        assert invocation.stdin_secret_env == ("CLAUDE_CODE_OAUTH_TOKEN", "test-only")
        assert "test-only" not in repr(invocation)
        assert invocation.bootstrap_copy is None
        assert "test-only" not in " ".join(invocation.argv)
        assert "test-only" not in dict(invocation.environment).values()
        assert all("credential" not in mount.container_path for mount in invocation.mounts)


def test_missing_automation_credential_keeps_isolated_config_without_secret_transport(tmp_path: Path) -> None:
    adapter = ClaudeAdapter()

    with adapter.invocation(
        binary(tmp_path), model="sonnet", reasoning_effort="medium", prompt="Fix it"
    ) as invocation:
        assert invocation.environment == (
            ("CLAUDE_CONFIG_DIR", "/opt/qualock/claude-home"),
            ("DISABLE_AUTOUPDATER", "1"),
        )
        assert invocation.tmpfs_mounts == ("/opt/qualock/claude-home",)
        assert invocation.bootstrap_copy is None
        assert invocation.stdin_secret_env is None
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
        '{"type":"result","subtype":"success","usage":{"input_tokens":4,"cache_read_input_tokens":0,"output_tokens":1}}\n',
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


def test_select_claude_automation_credential_follows_documented_precedence() -> None:
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "ANTHROPIC_API_KEY": "api",
        "ANTHROPIC_AUTH_TOKEN": "bearer",
    }
    assert select_claude_automation_credential(env) == ("ANTHROPIC_AUTH_TOKEN", "bearer")
    del env["ANTHROPIC_AUTH_TOKEN"]
    assert select_claude_automation_credential(env) == ("ANTHROPIC_API_KEY", "api")
    del env["ANTHROPIC_API_KEY"]
    assert select_claude_automation_credential(env) == ("CLAUDE_CODE_OAUTH_TOKEN", "oauth")


def test_select_claude_automation_credential_ignores_empty_values() -> None:
    assert select_claude_automation_credential({"CLAUDE_CODE_OAUTH_TOKEN": ""}) is None
