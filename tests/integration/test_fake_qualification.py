from pathlib import Path

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.executor import QualificationExecutor
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedImage:
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary: CanarySpec, prepared: PreparedImage, binary: AgentBinary, side: Side, repetition: int) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition, prepared.digest))
        success = side is Side.BASELINE
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=2),
        )


def make_canary(tmp_path: Path) -> CanarySpec:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate({
        "schema_version": 1,
        "id": "critical-bug",
        "name": "Critical bug",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix it",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(grader), "command": ["pytest -q"]},
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    })


def test_executor_interleaves_both_versions_and_blocks_zero_of_three_candidate(tmp_path: Path) -> None:
    backend = FakeBackend()
    executor = QualificationExecutor(backend=backend, repetitions=3)
    baseline = AgentBinary("codex", "0.150.0", Path("/a"), "a")
    candidate = AgentBinary("codex", "0.151.0", Path("/b"), "b")
    result = executor.run(baseline, candidate, [make_canary(tmp_path)], qualification_id="q-fixed")

    assert result.verdict is Verdict.BLOCK
    assert result.executions[0].baseline_successes == 3
    assert result.executions[0].candidate_successes == 0
    assert len(backend.calls) == 6
    assert {call[3] for call in backend.calls} == {"sha256:critical-bug"}
    for index in range(0, len(backend.calls), 2):
        assert {backend.calls[index][1], backend.calls[index + 1][1]} == {"baseline", "candidate"}
