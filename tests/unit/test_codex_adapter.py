from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary, AgentCapabilities
from qualock.agents.codex import CodexAdapter, IncompatibleCodexError
from qualock.evidence.models import AgentEvidence

FAKE = Path("tests/fixtures/fake-codex").resolve()


def test_detects_required_exec_capabilities() -> None:
    adapter = CodexAdapter()
    capabilities = adapter.detect_capabilities(FAKE)
    assert capabilities.exec is True
    assert capabilities.json is True
    assert capabilities.ephemeral is True
    assert capabilities.ignore_user_config is True
    assert capabilities.ignore_rules is True
    assert capabilities.workspace_write is True
    assert capabilities.model is True


def test_builds_isolated_explicit_model_exec_command() -> None:
    adapter = CodexAdapter()
    binary = AgentBinary(name="codex", version="0.150.0", path=FAKE, sha256="abc")
    capabilities = adapter.detect_capabilities(FAKE)
    argv = adapter.build_exec_argv(
        binary,
        capabilities,
        model="gpt-5.3-codex",
        reasoning_effort="high",
        prompt="Fix it",
    )
    assert argv[0] == str(FAKE)
    assert argv[1] == "exec"
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--model") + 1] == "gpt-5.3-codex"
    disabled = [argv[index + 1] for index, value in enumerate(argv) if value == "--disable"]
    assert disabled == ["apps", "plugins", "remote_plugin"]
    overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
    assert "model_reasoning_effort=high" in overrides
    assert "sandbox_workspace_write.network_access=false" in overrides
    assert "web_search=disabled" in overrides
    assert "--json" in argv
    assert argv[-1] == "Fix it"


def test_rejects_missing_common_capability(tmp_path: Path) -> None:
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho --json\n", encoding="utf-8")
    fake.chmod(0o755)
    adapter = CodexAdapter()
    capabilities = adapter.detect_capabilities(fake)
    binary = AgentBinary(name="codex", version="old", path=fake, sha256="x")
    with pytest.raises(IncompatibleCodexError):
        adapter.build_exec_argv(
            binary, capabilities, model="m", reasoning_effort="high", prompt="p"
        )


def test_invocation_preserves_codex_container_path_and_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = CodexAdapter()
    monkeypatch.setattr(
        adapter,
        "detect_capabilities",
        lambda path: AgentCapabilities(True, True, True, True, True, True, True),
    )
    binary = AgentBinary("codex", "0.150.0", tmp_path / "codex", "sha")

    with adapter.invocation(
        binary,
        model="gpt-5.3-codex",
        reasoning_effort="high",
        prompt="Fix it",
    ) as invocation:
        assert invocation.argv[0] == str(binary.path)
        assert invocation.container_binary_path == "/opt/qualock/codex"
        assert invocation.environment == ()
        assert invocation.tmpfs_mounts == ()


def test_invocation_owns_temporary_codex_auth_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text('{"token":"test-only"}', encoding="utf-8")
    adapter = CodexAdapter(auth_home=auth_home)
    monkeypatch.setattr(
        adapter,
        "detect_capabilities",
        lambda path: AgentCapabilities(True, True, True, True, True, True, True),
    )
    binary = AgentBinary("codex", "0.150.0", tmp_path / "codex", "sha")

    with adapter.invocation(
        binary,
        model="gpt-5.3-codex",
        reasoning_effort="high",
        prompt="Fix it",
    ) as invocation:
        seed = next(
            mount.host_path
            for mount in invocation.mounts
            if mount.container_path == "/opt/qualock/auth-seed.json"
        )
        assert seed.read_text(encoding="utf-8") == '{"token":"test-only"}'
        assert invocation.environment == (("CODEX_HOME", "/opt/qualock/auth"),)
        assert invocation.tmpfs_mounts == ("/opt/qualock/auth",)
        assert invocation.bootstrap_copy == (
            "/opt/qualock/auth-seed.json",
            "/opt/qualock/auth/auth.json",
        )
    assert not seed.exists()


def test_invocation_preserves_tmpfs_auth_home_without_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    adapter = CodexAdapter(auth_home=auth_home)
    monkeypatch.setattr(
        adapter,
        "detect_capabilities",
        lambda path: AgentCapabilities(True, True, True, True, True, True, True),
    )
    binary = AgentBinary("codex", "0.150.0", tmp_path / "codex", "sha")

    with adapter.invocation(
        binary,
        model="m",
        reasoning_effort="high",
        prompt="p",
    ) as invocation:
        assert invocation.environment == (("CODEX_HOME", "/opt/qualock/auth"),)
        assert invocation.tmpfs_mounts == ("/opt/qualock/auth",)
        assert invocation.mounts == ()
        assert invocation.bootstrap_copy is None


def test_parse_evidence_returns_normalized_agent_evidence() -> None:
    evidence = CodexAdapter().parse_evidence(
        '{"type":"turn.completed","usage":{"input_tokens":4,"output_tokens":1}}\n',
        "ignored stderr",
    )
    assert isinstance(evidence, AgentEvidence)
    assert evidence.input_tokens == 4
    assert evidence.output_tokens == 1
