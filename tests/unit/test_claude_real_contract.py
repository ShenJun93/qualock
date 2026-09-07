import os
import shutil
import sys
from pathlib import Path

import pytest

from qualock.agents.claude import ClaudeAdapter
from qualock.agents.claude_resolver import ClaudeResolver
from qualock.evidence.claude_stream_json import parse_claude_stream_json
from qualock.evidence.models import AgentEvidence
from qualock.qualification.models import Usage
from qualock.run.process import run_process


def golden_evidence() -> AgentEvidence:
    fixture = Path(
        "tests/fixtures/claude/stream_json_bash_failure_success_2_1_260.jsonl"
    )
    return parse_claude_stream_json(fixture.read_text(encoding="utf-8").splitlines())


def test_real_2_1_260_golden_transcript_parses_normalized_usage() -> None:
    evidence = golden_evidence()

    assert evidence.thread_id == "session-sanitized"
    assert [(item.command, item.exit_code) for item in evidence.commands] == [
        ("false", 1)
    ]
    assert evidence.input_tokens == 18202
    assert evidence.cached_input_tokens == 9035
    assert evidence.cache_write_input_tokens == 9163
    assert evidence.output_tokens == 74
    assert evidence.usage_observed is True
    assert evidence.errors == []
    assert [event.get("type") for event in evidence.unknown_events] == ["rate_limit_event"]


def test_real_2_1_260_golden_usage_does_not_double_count_cache_tokens() -> None:
    evidence = golden_evidence()
    usage = Usage(
        input_tokens=evidence.input_tokens,
        cached_input_tokens=evidence.cached_input_tokens,
        cache_write_input_tokens=evidence.cache_write_input_tokens,
        output_tokens=evidence.output_tokens,
        reasoning_output_tokens=evidence.reasoning_output_tokens,
        observed=evidence.usage_observed,
    )

    assert usage.total_tokens == 18202 + 74
    assert usage.total_tokens != 18202 + 9035 + 9163 + 74


def test_claude_usage_normalization_does_not_retain_sensitive_wire_data() -> None:
    raw_line = (
        '{"type":"result","subtype":"success",'
        '"usage":{"input_tokens":2,"cache_read_input_tokens":3,"output_tokens":4},'
        '"transcript":"token-bearing-secret","credentials":"credential-secret"}'
    )

    evidence = parse_claude_stream_json([raw_line])
    retained = repr(vars(evidence))

    assert raw_line not in retained
    assert "token-bearing-secret" not in retained
    assert "credential-secret" not in retained


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
