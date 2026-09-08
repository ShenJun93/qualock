import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qualock.agents.antigravity import (
    AntigravityAdapter,
    AntigravityInvocationError,
)
from qualock.agents.base import AgentBinary
from qualock.evidence.models import AgentEvidence


def _binary(tmp_path: Path) -> AgentBinary:
    return AgentBinary("antigravity", "1.1.27", tmp_path / "agy", "a" * 64)


def _invoke_hook(
    hook: Path,
    workspace: Path,
    payload: object,
    *,
    raw_input: str | None = None,
) -> tuple[int, object]:
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=raw_input if raw_input is not None else json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "QUALOCK_WORKSPACE": str(workspace)},
    )
    return result.returncode, json.loads(result.stdout)


def _tool_payload(tool_name: str, parameters: object) -> dict[str, object]:
    return {"toolCall": {"name": tool_name, "args": parameters}}


def test_antigravity_adapter_declares_no_runtime_overlays(tmp_path: Path) -> None:
    assert AntigravityAdapter(auth_app_data=tmp_path / "app-data").runtime_overlays == ()


def test_invocation_builds_fresh_private_profile(tmp_path: Path) -> None:
    token = tmp_path / "real-app" / "antigravity-oauth-token"
    token.parent.mkdir()
    token.write_text("DO_NOT_COPY", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = AntigravityAdapter(auth_app_data=token.parent)
    binary = _binary(tmp_path)

    with adapter.invocation(
        binary,
        model="gemini-3.8-flash-medium",
        reasoning_effort="medium",
        prompt="edit target",
        workspace=workspace,
        timeout_seconds=60,
    ) as invocation:
        settings = json.loads((invocation.app_data_root / "settings.json").read_text())
        assert settings["enableTerminalSandbox"] is True
        assert settings["toolPermission"] == "proceed-in-sandbox"
        assert "unsandboxed" not in json.dumps(settings).lower()
        assert "DO_NOT_COPY" not in json.dumps(settings)
        assert invocation.oauth_token_path == token
        assert invocation.workspace_mount == "/tmp/qualock-workspace"

        assert invocation.argv == (
            str(binary.path),
            "--new-project",
            "-p",
            "edit target",
            "--model",
            "gemini-3.8-flash-medium",
            "--effort",
            "medium",
            "--sandbox",
            "--disable-slash-commands",
            "--output-format",
            "stream-json",
            "--print-timeout",
            "60s",
        )
        assert invocation.environment == (
            ("AGY_CLI_DISABLE_AUTO_UPDATE", "true"),
            ("QUALOCK_WORKSPACE", "/tmp/qualock-workspace"),
        )
        assert invocation.config_root.name == "config"
        assert invocation.app_data_root.name == "appdata"
        assert (invocation.app_data_root / "antigravity-oauth-token").read_bytes() == b""

        manifest = json.loads(
            (invocation.config_root / "import_manifest.json").read_text(encoding="utf-8")
        )
        plugin_root = invocation.config_root / "plugins" / "qualock-security"
        plugin = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((plugin_root / "hooks.json").read_text(encoding="utf-8"))
        assert manifest == {"imports": None}
        assert plugin == {"name": "qualock-security"}
        pre_tool_use = hooks["qualock-security"]["PreToolUse"]
        assert pre_tool_use[0]["matcher"] == "*"
        assert "qualock_gate.py" in pre_tool_use[0]["hooks"][0]["command"]


def test_generated_profile_contains_no_credential_bytes(tmp_path: Path) -> None:
    sentinel = "UNIQUE_REAL_TOKEN_SENTINEL_4f53a9"
    token = tmp_path / "real-app" / "antigravity-oauth-token"
    token.parent.mkdir()
    token.write_text(sentinel, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AntigravityAdapter(auth_app_data=token.parent).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="low",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=1,
    ) as invocation:
        generated = b"".join(
            path.read_bytes()
            for root in (invocation.config_root, invocation.app_data_root)
            for path in root.rglob("*")
            if path.is_file()
        )
        assert sentinel.encode() not in generated


def test_invocation_uses_fresh_profile_and_cleans_it_up(tmp_path: Path) -> None:
    auth_app_data = tmp_path / "real-app"
    auth_app_data.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = AntigravityAdapter(auth_app_data=auth_app_data)

    with adapter.invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="high",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as first:
        first_root = first.config_root.parent
        assert first_root.exists()

    assert not first_root.exists()
    with adapter.invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="high",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as second:
        assert second.config_root.parent != first_root


@pytest.mark.parametrize("effort", ["xhigh", "invalid"])
def test_invocation_rejects_unsupported_reasoning_effort(tmp_path: Path, effort: str) -> None:
    with (
        pytest.raises(AntigravityInvocationError, match="reasoning effort"),
        AntigravityAdapter(auth_app_data=tmp_path).invocation(
            _binary(tmp_path),
            model="model",
            reasoning_effort=effort,
            prompt="prompt",
            workspace=tmp_path,
            timeout_seconds=5,
        ),
    ):
        pass


@pytest.mark.parametrize(
    ("tool_name", "parameters"),
    [
        ("view_file", {"AbsolutePath": "inside.txt"}),
        ("write_to_file", {"TargetFile": "nested/new.txt"}),
    ],
)
def test_hook_allows_file_tools_inside_workspace(
    tmp_path: Path, tool_name: str, parameters: dict[str, str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "nested").mkdir()

    with AntigravityAdapter(auth_app_data=tmp_path).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="medium",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as invocation:
        hook = invocation.config_root / "plugins" / "qualock-security" / "qualock_gate.py"
        returncode, response = _invoke_hook(hook, workspace, _tool_payload(tool_name, parameters))

    assert returncode == 0
    assert response == {"decision": "allow"}


@pytest.mark.parametrize("tool_name", ["view_file", "write_to_file"])
def test_hook_denies_file_tools_outside_workspace(tmp_path: Path, tool_name: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    key = "AbsolutePath" if tool_name == "view_file" else "TargetFile"
    parameters = {key: str(tmp_path / "outside.txt")}

    with AntigravityAdapter(auth_app_data=tmp_path).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="medium",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as invocation:
        hook = invocation.config_root / "plugins" / "qualock-security" / "qualock_gate.py"
        _, response = _invoke_hook(hook, workspace, _tool_payload(tool_name, parameters))

    assert response == {"decision": "deny"}


@pytest.mark.parametrize(
    "tool_name",
    [
        "search_web",
        "browser_subagent",
        "fetch_url",
        "mcp",
        "mcp_example",
        "invoke_subagent",
        "request_permission",
        "unknown_tool",
    ],
)
def test_hook_denies_escape_and_unknown_tools(tmp_path: Path, tool_name: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AntigravityAdapter(auth_app_data=tmp_path).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="medium",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as invocation:
        hook = invocation.config_root / "plugins" / "qualock-security" / "qualock_gate.py"
        _, response = _invoke_hook(hook, workspace, _tool_payload(tool_name, {}))

    assert response == {"decision": "deny"}


def test_hook_denies_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to(tmp_path)

    with AntigravityAdapter(auth_app_data=tmp_path).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="medium",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as invocation:
        hook = invocation.config_root / "plugins" / "qualock-security" / "qualock_gate.py"
        _, response = _invoke_hook(
            hook,
            workspace,
            _tool_payload("view_file", {"AbsolutePath": "escape/outside.txt"}),
        )

    assert response == {"decision": "deny"}


@pytest.mark.parametrize("raw_input", ["not json", "[]", "{}", ""])
def test_hook_fails_closed_on_malformed_input(tmp_path: Path, raw_input: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AntigravityAdapter(auth_app_data=tmp_path).invocation(
        _binary(tmp_path),
        model="model",
        reasoning_effort="medium",
        prompt="prompt",
        workspace=workspace,
        timeout_seconds=5,
    ) as invocation:
        hook = invocation.config_root / "plugins" / "qualock-security" / "qualock_gate.py"
        _, response = _invoke_hook(hook, workspace, {}, raw_input=raw_input)

    assert response == {"decision": "deny"}


def test_parse_evidence_delegates_to_antigravity_parser(tmp_path: Path) -> None:
    init_event = (
        '{"event":"init","conversation_id":"c1","init":{"permission_mode":"proceed-in-sandbox"}}'
    )
    result_event = (
        '{"event":"result","result":{"status":"SUCCESS","denied_actions":[],'
        '"usage":{"input_tokens":4,"output_tokens":1,"thinking_tokens":2,'
        '"cache_read_tokens":0}}}'
    )
    stdout = f"{init_event}\n{result_event}"

    evidence = AntigravityAdapter(auth_app_data=tmp_path).parse_evidence(stdout, "ignored")

    assert isinstance(evidence, AgentEvidence)
    assert evidence.input_tokens == 4
    assert evidence.output_tokens == 1
