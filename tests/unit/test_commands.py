import json
from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.commands import (
    BaselineUnstableError,
    execute_baseline,
    execute_check,
    parse_agent_spec,
)
from qualock.config.io import write_default_config
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side


class FakeResolver:
    def resolve(self, version: str) -> AgentBinary:
        exact = "0.151.0" if version == "latest" else version
        return AgentBinary("codex", exact, Path(f"/fake/{exact}/node_modules/.bin/codex"), f"sha-{exact}")


class FakeBackend:
    def prepare(self, canary, qualification_id: str) -> PreparedImage:
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary, prepared, binary, side: Side, repetition: int) -> AttemptResult:
        success = binary.version == "0.150.0"
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=1),
        )


def setup_project(root: Path) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    write_default_config(ub / "config.yaml")
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


def test_parse_agent_spec_requires_codex_and_version() -> None:
    assert parse_agent_spec("codex@0.150.0") == ("codex", "0.150.0")


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
    assert lock.agent.version == "0.150.0"
    assert lock.canaries["sample"].valid_runs == 3
    assert lock.canaries["sample"].successes == 3
    assert (tmp_path / ".qualock/baseline.lock").is_file()


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
