import json
from pathlib import Path

import pytest
import yaml

from qualock.agents.base import AgentBinary
from qualock.baseline.io import read_baseline_lock, write_baseline_lock
from qualock.commands import (
    BaselineUnstableError,
    CommandError,
    execute_baseline,
    execute_check,
    parse_agent_spec,
)
from qualock.config.io import write_default_config
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side


class FakeResolver:
    def __init__(self, agent_name: str = "codex") -> None:
        self.agent_name = agent_name
        self.calls: list[str] = []

    def resolve(self, version: str) -> AgentBinary:
        self.calls.append(version)
        exact = "0.151.0" if version == "latest" else version
        return AgentBinary(
            self.agent_name,
            exact,
            Path(f"/fake/{self.agent_name}/{exact}/agent"),
            f"sha-{exact}",
        )


class FakeBackend:
    def __init__(self, success_versions: set[str] | None = None) -> None:
        self.success_versions = success_versions or {"0.150.0"}

    def prepare(self, canary, qualification_id: str) -> PreparedImage:
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary, prepared, binary, side: Side, repetition: int) -> AttemptResult:
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=binary.version in self.success_versions,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=1),
        )


def setup_project(root: Path, *, agent_name: str = "codex", model_id: str | None = None) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    config_path = ub / "config.yaml"
    write_default_config(config_path)
    if agent_name != "codex" or model_id is not None:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        payload["agent"]["name"] = agent_name
        if model_id is not None:
            payload["model"]["id"] = model_id
            payload["model"]["snapshot"] = None
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    grader = ub / "canaries/grader.patch"
    grader.write_text("patch", encoding="utf-8")
    (ub / "canaries/sample.yaml").write_text(
        f"""schema_version: 1
id: sample
name: Sample
repository:
  url: https://example.invalid/repo.git
  base_sha: {'a' * 40}
runtime:
  image: python:3.12-slim
task: Fix it.
setup: []
agent:
  timeout_seconds: 60
grader:
  patch: grader.patch
  command:
    - pytest -q
constraints:
  protected_paths:
    - tests/**
critical: true
""",
        encoding="utf-8",
    )


def test_parse_agent_spec_accepts_codex_and_claude() -> None:
    assert parse_agent_spec("codex@0.150.0") == ("codex", "0.150.0")
    assert parse_agent_spec("claude@2.1.260") == ("claude", "2.1.260")


def test_baseline_writes_known_good_behavior_lock(tmp_path: Path) -> None:
    setup_project(tmp_path)
    lock = execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=FakeResolver(),
        backend=FakeBackend(),
        qualification_id="baseline-q",
        created_at="2026-08-31T00:00:00Z",
    )
    assert lock.agent.name == "codex"
    assert lock.agent.version == "0.150.0"
    assert lock.canaries["sample"].valid_runs == 3
    assert lock.canaries["sample"].successes == 3
    assert (tmp_path / ".qualock/baseline.lock").is_file()


def test_claude_baseline_pins_claude_agent(tmp_path: Path) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    lock = execute_baseline(
        tmp_path,
        "claude@2.1.260",
        resolver=FakeResolver("claude"),
        backend=FakeBackend({"2.1.260"}),
        qualification_id="claude-baseline",
        created_at="2026-09-04T00:00:00Z",
    )

    assert lock.agent.name == "claude"
    assert lock.agent.version == "2.1.260"
    assert lock.model.id == "sonnet"


def test_baseline_agent_must_match_config_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="config agent codex does not match requested agent claude"):
        execute_baseline(
            tmp_path,
            "claude@2.1.260",
            resolver=resolver,
            backend=FakeBackend({"2.1.260"}),
        )

    assert resolver.calls == []


def test_unstable_baseline_persists_attempt_evidence(tmp_path: Path) -> None:
    setup_project(tmp_path)
    with pytest.raises(BaselineUnstableError):
        execute_baseline(
            tmp_path,
            "codex@0.151.0",
            resolver=FakeResolver(),
            backend=FakeBackend(),
            qualification_id="baseline-fail",
            created_at="2026-08-31T00:00:00Z",
        )

    evidence_path = tmp_path / ".qualock/results/baseline-fail/baseline.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["qualification_id"] == "baseline-fail"
    assert payload["baseline_version"] == "0.151.0"
    assert len(payload["canaries"]["sample"]) == 3
    assert payload["canaries"]["sample"][0]["success"] is False
    assert payload["canaries"]["sample"][0]["valid"] is True


def test_check_reruns_pinned_baseline_and_candidate_and_writes_report(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-q",
        created_at="2026-08-31T00:00:00Z",
    )
    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-q",
    )
    assert result.verdict is Verdict.BLOCK
    assert result.baseline_version == "0.150.0"
    assert result.candidate_version == "0.151.0"
    assert (tmp_path / ".qualock/results/check-q/report.json").is_file()


def test_check_candidate_agent_must_match_config_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="config agent codex does not match requested agent claude"):
        execute_check(tmp_path, "claude@2.1.260", resolver=resolver, backend=FakeBackend())

    assert resolver.calls == []


def test_check_baseline_agent_must_match_candidate_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    execute_baseline(
        tmp_path,
        "claude@2.1.260",
        resolver=FakeResolver("claude"),
        backend=FakeBackend({"2.1.260"}),
        qualification_id="claude-baseline",
        created_at="2026-09-04T00:00:00Z",
    )
    lock_path = tmp_path / ".qualock/baseline.lock"
    lock = read_baseline_lock(lock_path)
    write_baseline_lock(
        lock_path,
        lock.model_copy(update={"agent": lock.agent.model_copy(update={"name": "codex"})}),
    )
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="baseline agent codex does not match candidate agent claude"):
        execute_check(
            tmp_path,
            "claude@2.1.261",
            resolver=resolver,
            backend=FakeBackend({"2.1.260"}),
        )

    assert resolver.calls == []
