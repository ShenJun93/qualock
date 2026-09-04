import os
import shutil
import sys
from pathlib import Path

import pytest

from qualock.agents.claude import ClaudeAdapter
from qualock.agents.claude_resolver import ClaudeResolver
from qualock.run.process import run_process


def test_claude_2_1_260_real_settings_contract(tmp_path: Path) -> None:
    if os.environ.get("QUALOCK_RUN_CLAUDE_REAL_CONTRACT") != "1":
        pytest.skip("set QUALOCK_RUN_CLAUDE_REAL_CONTRACT=1 to run real Claude contract smoke")
    if sys.platform != "linux":
        pytest.skip("Claude native contract smoke currently targets Linux")
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is required for the real Claude contract smoke")

    cache_env = os.environ.get("QUALOCK_CLAUDE_CONTRACT_CACHE")
    cache_root = Path(cache_env) if cache_env else tmp_path / "cache"
    resolved = ClaudeResolver(cache_root, npm_executable=npm).resolve("2.1.260")
    assert resolved.version == "2.1.260"
    assert len(resolved.sha256) == 64

    probe_home = tmp_path / "home"
    probe_home.mkdir()
    probe_config = tmp_path / "claude-config"
    probe_config.mkdir()
    probe_env = {
        "HOME": str(probe_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLAUDE_CONFIG_DIR": str(probe_config),
        "DISABLE_AUTOUPDATER": "1",
    }

    adapter = ClaudeAdapter()
    with adapter.invocation(
        resolved, model="sonnet", reasoning_effort="high", prompt="noop"
    ) as invocation:
        settings = next(
            mount.host_path
            for mount in invocation.mounts
            if mount.container_path == "/opt/qualock/claude-settings.json"
        )
        positive = run_process(
            [str(resolved.path), "--settings", str(settings), "doctor"],
            env=probe_env,
            timeout_seconds=30,
        )
        positive_text = positive.stdout + positive.stderr
        assert positive.exit_code == 0
        assert "Invalid settings" not in positive_text

    malformed = tmp_path / "invalid-settings.json"
    malformed.write_text(
        '{"sandbox":{"enabled":"not-a-bool"}}\n', encoding="utf-8"
    )
    negative = run_process(
        [str(resolved.path), "--settings", str(malformed), "doctor"],
        env=probe_env,
        timeout_seconds=30,
    )
    negative_text = negative.stdout + negative.stderr
    assert "Invalid settings" in negative_text
