import json
from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary, AgentRuntimeOverlay
from qualock.agents.gemini import GeminiAdapter, select_gemini_automation_credential
from qualock.evidence.gemini_stream_json import parse_gemini_stream_json
from qualock.evidence.models import AgentEvidence

_ENFORCED_SETTINGS = {
    "mcpServers": {},
    "policyPaths": [],
    "adminPolicyPaths": [],
    "general": {
        "enableAutoUpdate": False,
        "enableAutoUpdateNotification": False,
        "checkpointing": {"enabled": False},
    },
    "privacy": {"usageStatisticsEnabled": False},
    "advanced": {"ignoreLocalEnv": True},
    "context": {
        "fileName": [],
        "includeDirectories": [],
        "loadMemoryFromIncludeDirectories": False,
    },
    "tools": {
        "shell": {"enableInteractiveShell": False},
        "core": [
            "read_file",
            "read_many_files",
            "list_directory",
            "glob",
            "grep_search",
            "write_file",
            "replace",
            "run_shell_command",
        ],
    },
    "security": {
        "auth": {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"},
        "blockedEnvironmentVariables": ["GEMINI_API_KEY"],
    },
    "skills": {"enabled": False},
    "hooksConfig": {"enabled": False},
    "hooks": {},
    "telemetry": {"enabled": False, "logPrompts": False},
    "experimental": {"enableAgents": False},
}

_WRAPPER_SCRIPT = (
    "#!/bin/sh\n"
    "set -eu\n"
    "unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS \\\n"
    "  GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION\n"
    "if ! /usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/true; then\n"
    "  echo QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE >&2\n"
    "  exit 125\n"
    "fi\n"
    "set +e\n"
    '/usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/bash "$@"\n'
    "qualock_status=$?\n"
    "printf '\\nQUALOCK_GEMINI_EXIT_CODE=%s\\n' \"$qualock_status\" >&2\n"
    'exit "$qualock_status"\n'
)

_NODE_OVERLAY = AgentRuntimeOverlay(
    image=(
        "node:22.23.2-bookworm@sha256:"
        "8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d"
    ),
    source_path="/usr/local",
    destination_path="/opt/qualock/node-runtime",
    validation_command=("/opt/qualock/node-runtime/bin/node", "--version"),
)


def binary(tmp_path: Path) -> AgentBinary:
    path = tmp_path / "gemini"
    path.write_text("fake", encoding="utf-8")
    return AgentBinary(name="gemini", version="0.58.0", path=path, sha256="sha")


def test_invocation_builds_isolated_headless_command(tmp_path: Path) -> None:
    adapter = GeminiAdapter()
    with adapter.invocation(
        binary(tmp_path),
        model="gemini-3.5-flash",
        reasoning_effort="provider-default",
        prompt="Fix it",
    ) as invocation:
        assert invocation.container_binary_path == "/opt/qualock/gemini"

        argv = list(invocation.argv)
        assert argv[0] == str(tmp_path / "gemini")
        assert argv[argv.index("--prompt") + 1] == "Fix it"
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert argv[argv.index("--model") + 1] == "gemini-3.5-flash"
        assert "--approval-mode" in argv
        assert argv[argv.index("--approval-mode") + 1] == "yolo"
        assert argv[argv.index("-e") + 1] == "none"
        assert "GEMINI_API_KEY" not in argv
        assert "test-only" not in argv
        assert not any(flag in argv for flag in ("--resume", "--session-id", "--session"))
        assert "--record-session" not in argv
        assert "--include-directories" not in argv
        assert "--policy-file" not in argv
        assert "--extensions" not in argv

        env = dict(invocation.environment)
        assert env["HOME"] == "/opt/qualock/gemini-home"
        assert env["GEMINI_CLI_HOME"] == "/opt/qualock/gemini-home"
        assert env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == "/opt/qualock/gemini-settings.json"
        assert env["GEMINI_SANDBOX"] == "false"
        assert invocation.tmpfs_mounts == ("/opt/qualock/gemini-home",)

        path_entries = env["PATH"].split(":")
        assert path_entries[0] == "/opt/qualock/bin"
        assert path_entries[1] == "/opt/qualock/node-runtime/bin"


def test_invocation_mounts_settings_wrapper_and_empty_project_dir(tmp_path: Path) -> None:
    adapter = GeminiAdapter()
    with adapter.invocation(
        binary(tmp_path),
        model="gemini-3.5-flash",
        reasoning_effort="provider-default",
        prompt="Fix it",
    ) as invocation:
        mounts_by_path = {mount.container_path: mount for mount in invocation.mounts}

        settings_mount = mounts_by_path["/opt/qualock/gemini-settings.json"]
        assert settings_mount.mode == "ro"
        payload = json.loads(settings_mount.host_path.read_text(encoding="utf-8"))
        assert payload == _ENFORCED_SETTINGS

        project_mount = mounts_by_path["/workspace/.gemini"]
        assert project_mount.mode == "ro"
        assert project_mount.host_path.is_dir()
        assert list(project_mount.host_path.iterdir()) == []

        wrapper_mount = mounts_by_path["/opt/qualock/bin/bash"]
        assert wrapper_mount.mode == "ro"
        wrapper_text = wrapper_mount.host_path.read_text(encoding="utf-8")
        assert wrapper_text == _WRAPPER_SCRIPT

        settings_host = settings_mount.host_path
        project_host = project_mount.host_path
        wrapper_host = wrapper_mount.host_path

    assert not settings_host.exists()
    assert not project_host.exists()
    assert not wrapper_host.exists()


def test_wrapper_script_bubblewrap_invocations_are_well_formed() -> None:
    assert _WRAPPER_SCRIPT.count("--unshare-user") == 2
    assert _WRAPPER_SCRIPT.count("--unshare-net") == 2
    assert _WRAPPER_SCRIPT.count("--bind / /") == 2
    assert _WRAPPER_SCRIPT.count("--dev /dev") == 2

    unset_index = _WRAPPER_SCRIPT.index("unset ")
    first_bwrap_index = _WRAPPER_SCRIPT.index("/usr/bin/bwrap")
    assert unset_index < first_bwrap_index

    assert _WRAPPER_SCRIPT.count("/bin/bash") == 1
    assert "-- /bin/bash" in _WRAPPER_SCRIPT

    assert _WRAPPER_SCRIPT.count("QUALOCK_GEMINI_EXIT_CODE") == 1
    second_bwrap_index = _WRAPPER_SCRIPT.index("-- /bin/bash")
    marker_index = _WRAPPER_SCRIPT.index("QUALOCK_GEMINI_EXIT_CODE")
    assert second_bwrap_index < marker_index


def test_non_provider_default_reasoning_effort_raises_before_yielding(tmp_path: Path) -> None:
    adapter = GeminiAdapter()

    with pytest.raises(ValueError), adapter.invocation(
        binary(tmp_path),
        model="gemini-3.5-flash",
        reasoning_effort="high",
        prompt="Fix it",
    ):
        pytest.fail("invocation body must not run for non provider-default effort")


def test_automation_credential_uses_stdin_secret_environment_without_metadata(
    tmp_path: Path,
) -> None:
    adapter = GeminiAdapter(automation_credential=("GEMINI_API_KEY", "test-only"))

    with adapter.invocation(
        binary(tmp_path),
        model="gemini-3.5-flash",
        reasoning_effort="provider-default",
        prompt="Fix it",
    ) as invocation:
        assert invocation.stdin_secret_env == ("GEMINI_API_KEY", "test-only")
        assert "test-only" not in repr(invocation)
        assert invocation.bootstrap_copy is None
        assert "test-only" not in " ".join(invocation.argv)
        assert "test-only" not in dict(invocation.environment).values()
        assert all("credential" not in mount.container_path for mount in invocation.mounts)


def test_gemini_adapter_rejects_non_api_key_automation_credential() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiAdapter(automation_credential=("GOOGLE_API_KEY", "not-allowed"))


def test_gemini_adapter_rejects_empty_api_key_automation_credential() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiAdapter(automation_credential=("GEMINI_API_KEY", ""))


def test_missing_automation_credential_keeps_isolated_config_without_secret_transport(
    tmp_path: Path,
) -> None:
    adapter = GeminiAdapter()

    with adapter.invocation(
        binary(tmp_path),
        model="gemini-3.5-flash",
        reasoning_effort="provider-default",
        prompt="Fix it",
    ) as invocation:
        assert invocation.stdin_secret_env is None
        assert invocation.bootstrap_copy is None


def test_select_gemini_automation_credential_requires_non_empty_api_key() -> None:
    assert select_gemini_automation_credential({"GEMINI_API_KEY": "abc"}) == (
        "GEMINI_API_KEY",
        "abc",
    )
    assert select_gemini_automation_credential({"GEMINI_API_KEY": ""}) is None
    assert select_gemini_automation_credential({}) is None


def test_select_gemini_automation_credential_ignores_other_google_credentials() -> None:
    env = {
        "GOOGLE_API_KEY": "should-not-be-used",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/creds.json",
    }
    assert select_gemini_automation_credential(env) is None


def test_gemini_adapter_declares_no_runtime_dependencies() -> None:
    assert GeminiAdapter().runtime_dependencies == ()


def test_gemini_adapter_declares_exactly_one_node_runtime_overlay() -> None:
    assert GeminiAdapter().runtime_overlays == (_NODE_OVERLAY,)


def test_parse_evidence_ignores_stderr_and_delegates_to_gemini_stream_parser() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "init", "session_id": "s1"}),
            json.dumps(
                {
                    "type": "result",
                    "status": "success",
                    "stats": {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "cached": 0,
                        "input": 0,
                        "duration_ms": 10,
                        "tool_calls": 0,
                    },
                }
            ),
        ]
    )

    expected = parse_gemini_stream_json(stdout.splitlines())
    evidence = GeminiAdapter().parse_evidence(stdout, "not valid json at all {{{")

    assert isinstance(evidence, AgentEvidence)
    assert evidence.thread_id == expected.thread_id
    assert evidence.input_tokens == expected.input_tokens
    assert evidence.output_tokens == expected.output_tokens
