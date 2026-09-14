import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.executor import QualificationExecutor
from qualock.run.models import (
    AttemptControlContext,
    AttemptControlProfiles,
    AttemptExecution,
    AttemptRunTrace,
    PreparedTarget,
)
from qualock.run.schedule import Side


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedTarget:
        return PreparedTarget(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary: CanarySpec, prepared: PreparedTarget, binary: AgentBinary, side: Side, repetition: int) -> AttemptResult:
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


class ProtocolAwareFakeBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.legacy_attempt_calls = 0
        self.context_attempt_calls = 0

    def control_profiles(self, canary: CanarySpec) -> AttemptControlProfiles:
        return AttemptControlProfiles(
            preparation_sha256="1" * 64,
            isolation_sha256="2" * 64,
            resource_sha256="3" * 64,
            runtime_sha256="4" * 64,
        )

    def run_attempt(self, **kwargs) -> AttemptResult:
        self.legacy_attempt_calls += 1
        return super().run_attempt(**kwargs)

    def run_attempt_with_context(self, **kwargs) -> AttemptExecution:
        self.context_attempt_calls += 1
        result = replace(super().run_attempt(**kwargs), events_jsonl="trace-events")
        return AttemptExecution(
            result=result,
            context=AttemptControlContext(
                profiles=self.control_profiles(kwargs["canary"]),
                isolation_instance_sha256="5" * 64,
            ),
        )


def test_executor_records_protocol_aware_attempt_traces(tmp_path: Path) -> None:
    backend = ProtocolAwareFakeBackend()
    executor = QualificationExecutor(backend=backend, repetitions=2)
    baseline = AgentBinary("codex", "0.150.0", Path("/a"), "a")
    candidate = AgentBinary("codex", "0.151.0", Path("/b"), "b")
    traces: list[AttemptRunTrace] = []

    executor.run(
        baseline,
        candidate,
        [make_canary(tmp_path)],
        qualification_id="q-trace",
        trace_sink=traces,
        trace_design_sha256="d" * 64,
    )

    assert backend.context_attempt_calls == 4
    assert backend.legacy_attempt_calls == 0
    assert len(traces) == 4
    assert [trace.side for trace in traces] == [call[1] for call in backend.calls]
    assert [trace.repetition for trace in traces] == [call[2] for call in backend.calls]
    assert all(trace.canary_id == "critical-bug" for trace in traces)
    assert all(trace.trace_design_sha256 == "d" * 64 for trace in traces)
    assert all(trace.started_offset_ms >= 0 for trace in traces)
    assert all(trace.finished_offset_ms >= trace.started_offset_ms for trace in traces)
    assert all(
        trace.events_sha256 == hashlib.sha256(b"trace-events").hexdigest()
        for trace in traces
    )
    assert all(trace.context.profiles.preparation_sha256 == "1" * 64 for trace in traces)
    assert all(trace.context.profiles.isolation_sha256 == "2" * 64 for trace in traces)
    assert all(trace.context.profiles.resource_sha256 == "3" * 64 for trace in traces)
    assert all(trace.context.profiles.runtime_sha256 == "4" * 64 for trace in traces)
    assert all(trace.context.isolation_instance_sha256 == "5" * 64 for trace in traces)


def test_executor_without_trace_sink_uses_only_legacy_attempt_path(tmp_path: Path) -> None:
    backend = ProtocolAwareFakeBackend()
    executor = QualificationExecutor(backend=backend, repetitions=1)
    baseline = AgentBinary("codex", "0.150.0", Path("/a"), "a")
    candidate = AgentBinary("codex", "0.151.0", Path("/b"), "b")

    executor.run(baseline, candidate, [make_canary(tmp_path)], qualification_id="q-legacy")

    assert backend.legacy_attempt_calls == 2
    assert backend.context_attempt_calls == 0


def test_trace_clock_invariant_uses_runtime_error_not_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = ProtocolAwareFakeBackend()
    executor = QualificationExecutor(backend=backend, repetitions=1)
    baseline = AgentBinary("codex", "0.150.0", Path("/a"), "a")
    candidate = AgentBinary("codex", "0.151.0", Path("/b"), "b")
    traces: list[AttemptRunTrace] = []
    monkeypatch.setattr("qualock.run.executor.time.monotonic_ns", lambda: None)

    with pytest.raises(RuntimeError, match="trace clock invariant"):
        executor.run(
            baseline,
            candidate,
            [make_canary(tmp_path)],
            qualification_id="q-trace-invariant",
            trace_sink=traces,
            trace_design_sha256="d" * 64,
        )


def test_trace_sink_requires_design_digest_before_attempts(tmp_path: Path) -> None:
    backend = ProtocolAwareFakeBackend()
    executor = QualificationExecutor(backend=backend, repetitions=1)
    baseline = AgentBinary("codex", "0.150.0", Path("/a"), "a")
    candidate = AgentBinary("codex", "0.151.0", Path("/b"), "b")
    traces: list[AttemptRunTrace] = []

    with pytest.raises(ValueError, match="trace_design_sha256"):
        executor.run(
            baseline,
            candidate,
            [make_canary(tmp_path)],
            qualification_id="q-missing-design",
            trace_sink=traces,
        )

    assert backend.calls == []
    assert traces == []
