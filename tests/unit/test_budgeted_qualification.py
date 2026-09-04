from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.executor import QualificationExecutor
from qualock.run.models import PreparedImage
from qualock.run.schedule import Side, paired_schedule


class RecordingBackend:
    def __init__(self, *, failing_candidates: set[str] | None = None) -> None:
        self.failing_candidates = failing_candidates or set()
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedImage:
        self.prepared.append(canary.id)
        return PreparedImage(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedImage,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        success = side is Side.BASELINE or canary.id not in self.failing_candidates
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=True,
            duration_ms=10,
            usage=Usage(input_tokens=1, output_tokens=1),
        )


def make_canary(tmp_path: Path, canary_id: str, *, critical: bool) -> CanarySpec:
    patch = tmp_path / f"{canary_id}.patch"
    patch.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": canary_id,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(patch), "command": ["pytest -q"]},
            "constraints": {"protected_paths": []},
            "critical": critical,
        }
    )


def binaries() -> tuple[AgentBinary, AgentBinary]:
    return (
        AgentBinary("codex", "0.150.0", Path("/baseline"), "sha-baseline"),
        AgentBinary("codex", "0.151.0", Path("/candidate"), "sha-candidate"),
    )


@pytest.mark.parametrize("max_attempts", [None, 18, 19])
def test_unconstrained_budget_preserves_existing_order(
    tmp_path: Path, max_attempts: int | None
) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-fixed",
        max_attempts=max_attempts,
    )

    expected_calls = [
        (canary.id, slot.side.value, slot.repetition)
        for canary in suite
        for slot in paired_schedule(canary.id, 3, "q-fixed")
    ]
    assert backend.calls == expected_calls
    assert [item.canary_id for item in result.executions] == [item.id for item in suite]
    assert result.run_order == tuple(expected_calls)


def test_constrained_budget_runs_critical_first_but_returns_original_order(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-budget",
        max_attempts=6,
    )

    expected_critical_calls = [
        ("critical", slot.side.value, slot.repetition)
        for slot in paired_schedule("critical", 3, "q-budget")
    ]
    assert backend.prepared == ["critical"]
    assert backend.calls == expected_critical_calls
    assert result.run_order == tuple(expected_critical_calls)
    assert [item.canary_id for item in result.executions] == [
        "normal-a",
        "critical",
        "normal-b",
    ]
    assert result.verdict is Verdict.INCOMPLETE

    first, critical, last = result.executions
    for skipped in (first, last):
        assert skipped.attempts == ()
        assert skipped.prepared_image_digest == ""
        assert skipped.baseline_successes == 0
        assert skipped.baseline_valid == 0
        assert skipped.candidate_successes == 0
        assert skipped.candidate_valid == 0
        assert skipped.verdict is Verdict.INCOMPLETE
        assert skipped.reason == (
            "INCOMPLETE: skipped by attempt budget "
            "(max_attempts=6, complete_canary_attempts=6)"
        )
    assert critical.verdict is Verdict.PASS


def test_budget_never_starts_a_partial_canary(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-seven",
        max_attempts=7,
    )

    assert backend.prepared == ["critical"]
    assert len(backend.calls) == 6
    assert result.executions[1].attempts == ()
    assert result.verdict is Verdict.INCOMPLETE


def test_budget_smaller_than_one_canary_runs_nothing(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-small",
        max_attempts=5,
    )

    assert backend.prepared == []
    assert backend.calls == []
    assert result.run_order == ()
    assert all(item.verdict is Verdict.INCOMPLETE for item in result.executions)
    assert result.verdict is Verdict.INCOMPLETE


def test_observed_critical_block_plus_skipped_canary_is_still_incomplete(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "critical", critical=True),
        make_canary(tmp_path, "normal", critical=False),
    ]
    backend = RecordingBackend(failing_candidates={"critical"})
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-block",
        max_attempts=6,
    )

    assert result.executions[0].verdict is Verdict.BLOCK
    assert result.executions[1].verdict is Verdict.INCOMPLETE
    assert result.verdict is Verdict.INCOMPLETE


def test_constrained_priority_is_stable_with_multiple_critical_canaries(tmp_path: Path) -> None:
    suite = [
        make_canary(tmp_path, "normal-a", critical=False),
        make_canary(tmp_path, "critical-a", critical=True),
        make_canary(tmp_path, "normal-b", critical=False),
        make_canary(tmp_path, "critical-b", critical=True),
    ]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    result = QualificationExecutor(backend=backend, repetitions=3).run(
        baseline,
        candidate,
        suite,
        qualification_id="q-stable-priority",
        max_attempts=12,
    )

    assert backend.prepared == ["critical-a", "critical-b"]
    assert [item.canary_id for item in result.executions] == [
        "normal-a",
        "critical-a",
        "normal-b",
        "critical-b",
    ]
    assert result.executions[0].verdict is Verdict.INCOMPLETE
    assert result.executions[2].verdict is Verdict.INCOMPLETE
    assert result.verdict is Verdict.INCOMPLETE


@pytest.mark.parametrize("bad_max_attempts", [0, -1])
def test_run_rejects_nonpositive_max_attempts_for_direct_callers(
    tmp_path: Path, bad_max_attempts: int
) -> None:
    suite = [make_canary(tmp_path, "critical", critical=True)]
    backend = RecordingBackend()
    baseline, candidate = binaries()

    with pytest.raises(ValueError, match="max_attempts must be greater than zero"):
        QualificationExecutor(backend=backend, repetitions=3).run(
            baseline,
            candidate,
            suite,
            qualification_id="q-invalid-budget",
            max_attempts=bad_max_attempts,
        )

    assert backend.prepared == []
    assert backend.calls == []
