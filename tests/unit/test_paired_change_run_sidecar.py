import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Self

import pytest

import qualock.protocols.paired_change.run_sidecar as run_sidecar_module
from qualock.protocols.paired_change.design import derive_changeset
from qualock.protocols.paired_change.fingerprint import digest_model
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    CanaryProtocolDesignV1,
    PairedChangeRunV1,
    ProtocolDesignV1,
)
from qualock.protocols.paired_change.run_sidecar import (
    PairedChangeRunError,
    build_paired_change_run,
    read_paired_change_run,
    write_paired_change_run,
)
from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Usage,
    Verdict,
)
from qualock.run.models import AttemptControlContext, AttemptControlProfiles, AttemptRunTrace

SHA = "a" * 64


def state(*, binary: str = SHA) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name="codex",
        version="0.150.0",
        binary_sha256=binary,
        support_sha256=None,
        model={"id": "gpt-5.6-sol", "snapshot": None, "reasoning_effort": "high"},
    )


def design(
    *,
    material_dimensions: tuple[str, ...] | None = ("AGENT_BINARY",),
    repetitions: int = 1,
) -> ProtocolDesignV1:
    return ProtocolDesignV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest="2" * 64,
        suite_sha256="3" * 64,
        config_sha256="4" * 64,
        repetitions=repetitions,
        order_policy="alternating-v1",
        lifecycle="FRESH",
        canaries=(
            CanaryProtocolDesignV1(
                canary_id="sample",
                canary_fingerprint_sha256="5" * 64,
                material_dimensions=material_dimensions,
                max_pair_gap_ms=5000 if material_dimensions is not None else None,
                preparation_sha256=None,
                isolation_sha256=None,
                resource_sha256=None,
                runtime_sha256=None,
            ),
        ),
    )


def trace_entry(
    *,
    side: str,
    repetition: int = 1,
    canary_id: str = "sample",
    trace_design_sha256: str,
    events_sha256: str | None = None,
) -> AttemptRunTrace:
    return AttemptRunTrace(
        canary_id=canary_id,
        side=side,
        repetition=repetition,
        trace_design_sha256=trace_design_sha256,
        started_offset_ms=10,
        finished_offset_ms=20,
        events_sha256=events_sha256 or hashlib.sha256(f"{side}-events".encode()).hexdigest(),
        context=AttemptControlContext(
            profiles=AttemptControlProfiles(
                preparation_sha256=None,
                isolation_sha256=None,
                resource_sha256=None,
                runtime_sha256=None,
            ),
            isolation_instance_sha256=None,
        ),
    )


def result(*, canary_id: str = "sample") -> QualificationResult:
    execution = CanaryExecution(
        canary_id=canary_id,
        critical=True,
        prepared_image_digest="sha256:" + "9" * 64,
        attempts=(
            AttemptResult(
                side="baseline",
                repetition=1,
                success=True,
                valid=True,
                duration_ms=100,
                usage=Usage(observed=True),
                events_jsonl="baseline-events",
            ),
            AttemptResult(
                side="candidate",
                repetition=1,
                success=True,
                valid=True,
                duration_ms=100,
                usage=Usage(observed=True),
                events_jsonl="candidate-events",
            ),
        ),
        baseline_successes=1,
        candidate_successes=1,
        baseline_valid=1,
        candidate_valid=1,
        verdict=Verdict.PASS,
        reason="ok",
    )
    return QualificationResult(
        qualification_id="q1",
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        verdict=Verdict.PASS,
        executions=(execution,),
        reasons=(),
        run_order=(),
    )


def paired_change_run() -> PairedChangeRunV1:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    return build_paired_change_run(
        protocol_design=frozen_design,
        protocol_design_sha256=frozen_digest,
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        result=result(),
        trace=[
            trace_entry(side="baseline", trace_design_sha256=frozen_digest),
            trace_entry(side="candidate", trace_design_sha256=frozen_digest),
        ],
    )


def test_build_paired_change_run_binds_qualification_and_design_identity() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest),
    ]

    run = build_paired_change_run(
        protocol_design=frozen_design,
        protocol_design_sha256=frozen_digest,
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        result=result(),
        trace=trace,
    )

    assert run.schema_version == 1
    assert run.protocol_id == "paired-change/v1"
    assert run.qualification_id == "q1"
    assert run.protocol_design_sha256 == frozen_digest
    assert run.protocol_design == frozen_design

    changeset = derive_changeset(state(), state(binary="6" * 64))
    assert run.changeset_sha256 == digest_model(changeset)

    assert len(run.canaries) == 1
    canary_evidence = run.canaries[0]
    assert canary_evidence.canary_id == "sample"
    assert canary_evidence.canary_fingerprint_sha256 == "5" * 64
    assert canary_evidence.prepared_target_sha256 == "9" * 64
    assert len(canary_evidence.pairs) == 1

    pair = canary_evidence.pairs[0]
    events_by_side = {item.side: item.events_sha256 for item in pair.attempts}
    assert events_by_side == {
        "baseline": hashlib.sha256(b"baseline-events").hexdigest(),
        "candidate": hashlib.sha256(b"candidate-events").hexdigest(),
    }
    for attempt in pair.attempts:
        assert attempt.protocol_design_sha256 == frozen_digest

    assert "evidence_manifest_sha256" not in run.model_dump(mode="json")


def test_build_paired_change_run_keeps_legacy_material_declaration_unavailable() -> None:
    frozen_design = design(material_dimensions=None)
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest),
    ]

    run = build_paired_change_run(
        protocol_design=frozen_design,
        protocol_design_sha256=frozen_digest,
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        result=result(),
        trace=trace,
    )

    assert run.protocol_design.canaries[0].material_dimensions is None
    assert run.protocol_design.canaries[0].max_pair_gap_ms is None


def test_build_paired_change_run_rejects_duplicate_sides() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="baseline", trace_design_sha256=frozen_digest, events_sha256="c" * 64),
    ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


def test_build_paired_change_run_rejects_missing_side() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [trace_entry(side="baseline", trace_design_sha256=frozen_digest)]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


@pytest.mark.parametrize(
    "trace_repetitions",
    [(), (1,), (1, 3)],
    ids=["empty", "missing-repetition", "out-of-range-repetition"],
)
def test_build_paired_change_run_requires_exact_frozen_repetitions(
    trace_repetitions: tuple[int, ...],
) -> None:
    frozen_design = design(repetitions=2)
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(
            side=side,
            repetition=repetition,
            trace_design_sha256=frozen_digest,
            events_sha256=("b" if side == "baseline" else "c") * 64,
        )
        for repetition in trace_repetitions
        for side in ("baseline", "candidate")
    ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


def test_build_paired_change_run_rejects_attempt_not_bound_to_frozen_design() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256="f" * 64, events_sha256="c" * 64),
    ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


def test_build_paired_change_run_rejects_mismatched_protocol_design_digest() -> None:
    frozen_design = design()
    mismatched_digest = "f" * 64
    trace = [
        trace_entry(side="baseline", trace_design_sha256=mismatched_digest),
        trace_entry(
            side="candidate", trace_design_sha256=mismatched_digest, events_sha256="c" * 64
        ),
    ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=mismatched_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


def test_build_paired_change_run_rejects_qualification_id_mismatch() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest),
    ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="different-qid",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=result(),
            trace=trace,
        )


@pytest.mark.parametrize("mutated_field", ["events_sha256", "side", "repetition"])
def test_build_paired_change_run_reconciles_trace_with_public_attempts(
    mutated_field: str,
) -> None:
    frozen_design = design(repetitions=2)
    frozen_digest = digest_model(frozen_design)
    attempts = tuple(
        AttemptResult(
            side=side,
            repetition=repetition,
            success=True,
            valid=True,
            duration_ms=100,
            usage=Usage(observed=True),
            events_jsonl=f"{side}-{repetition}-events",
        )
        for repetition in (1, 2)
        for side in ("baseline", "candidate")
    )
    public_result = result()
    public_result = replace(
        public_result,
        executions=(replace(public_result.executions[0], attempts=attempts),),
    )
    trace = [
        trace_entry(
            side=attempt.side,
            repetition=attempt.repetition,
            trace_design_sha256=frozen_digest,
            events_sha256=hashlib.sha256(attempt.events_jsonl.encode()).hexdigest(),
        )
        for attempt in attempts
    ]
    if mutated_field == "events_sha256":
        trace[0] = replace(trace[0], events_sha256="f" * 64)
    elif mutated_field == "side":
        trace[0] = replace(trace[0], side="candidate")
        trace[1] = replace(trace[1], side="baseline")
    else:
        trace = [
            replace(item, repetition=2 if item.repetition == 1 else 1) for item in trace
        ]

    with pytest.raises(PairedChangeRunError):
        build_paired_change_run(
            protocol_design=frozen_design,
            protocol_design_sha256=frozen_digest,
            qualification_id="q1",
            baseline_state=state(),
            candidate_state=state(binary="6" * 64),
            result=public_result,
            trace=trace,
        )


def test_write_paired_change_run_is_canonical_and_create_new(tmp_path: Path) -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest),
    ]
    run = build_paired_change_run(
        protocol_design=frozen_design,
        protocol_design_sha256=frozen_digest,
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        result=result(),
        trace=trace,
    )

    path = tmp_path / "nested" / "paired-change-run-v1.json"
    written = write_paired_change_run(path, run)
    assert written == path

    raw = path.read_bytes()
    assert raw == json.dumps(
        run.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"

    with pytest.raises(PairedChangeRunError):
        write_paired_change_run(path, run)


def test_read_paired_change_run_round_trips_and_is_bounded(tmp_path: Path) -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest),
    ]
    run = build_paired_change_run(
        protocol_design=frozen_design,
        protocol_design_sha256=frozen_digest,
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        result=result(),
        trace=trace,
    )
    path = tmp_path / "paired-change-run-v1.json"
    write_paired_change_run(path, run)

    round_tripped = read_paired_change_run(path)
    assert round_tripped == run

    with pytest.raises(PairedChangeRunError):
        read_paired_change_run(path, max_bytes=1)


def test_read_paired_change_run_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "paired-change-run-v1.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(PairedChangeRunError):
        read_paired_change_run(path)


def test_read_paired_change_run_enforces_bound_on_opened_file_during_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "paired-change-run-v1.json"
    replacement = tmp_path / "replacement.json"
    path.write_bytes(b"{}")
    replacement.write_bytes(b"x" * 128)
    original_open = run_sidecar_module.os.open

    def racing_open(value: object, flags: int, *args: object, **kwargs: object) -> int:
        if value == path:
            replacement.replace(path)
        return original_open(value, flags, *args, **kwargs)

    monkeypatch.setattr(run_sidecar_module.os, "open", racing_open)

    with pytest.raises(PairedChangeRunError, match="exceeds maximum size"):
        read_paired_change_run(path, max_bytes=16)


def test_read_paired_change_run_normalizes_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "paired-change-run-v1.json"
    path.write_bytes(b"\xff")

    with pytest.raises(PairedChangeRunError, match="invalid"):
        read_paired_change_run(path)


@pytest.mark.parametrize("failure_stage", ["directory", "open", "write"])
def test_write_paired_change_run_normalizes_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    path = tmp_path / "nested" / "paired-change-run-v1.json"

    if failure_stage == "directory":
        def fail_mkdir(*args: object, **kwargs: object) -> None:
            raise OSError("mkdir failed")

        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    elif failure_stage == "open":
        path.parent.mkdir()

        def fail_open(*args: object, **kwargs: object) -> object:
            raise OSError("open failed")

        monkeypatch.setattr(Path, "open", fail_open)
    else:
        path.parent.mkdir()

        class FailingWriter:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def write(self, payload: bytes) -> None:
                raise OSError("write failed")

        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: FailingWriter())

    with pytest.raises(PairedChangeRunError, match="could not be written"):
        write_paired_change_run(path, paired_change_run())
