from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.agents.codex import CodexAdapter, IncompatibleCodexError


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
