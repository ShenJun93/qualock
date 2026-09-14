import json
from pathlib import Path

import pytest

from qualock.protocols.paired_change.design import derive_changeset
from qualock.protocols.paired_change.fingerprint import digest_model
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    CanaryProtocolDesignV1,
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


def design(*, material_dimensions: tuple[str, ...] | None = ("AGENT_BINARY",)) -> ProtocolDesignV1:
    return ProtocolDesignV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest="2" * 64,
        suite_sha256="3" * 64,
        config_sha256="4" * 64,
        repetitions=1,
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
    events_sha256: str = "b" * 64,
) -> AttemptRunTrace:
    return AttemptRunTrace(
        canary_id=canary_id,
        side=side,
        repetition=repetition,
        trace_design_sha256=trace_design_sha256,
        started_offset_ms=10,
        finished_offset_ms=20,
        events_sha256=events_sha256,
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


def test_build_paired_change_run_binds_qualification_and_design_identity() -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest, events_sha256="c" * 64),
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
    assert events_by_side == {"baseline": "b" * 64, "candidate": "c" * 64}
    for attempt in pair.attempts:
        assert attempt.protocol_design_sha256 == frozen_digest

    assert "evidence_manifest_sha256" not in run.model_dump(mode="json")


def test_build_paired_change_run_keeps_legacy_material_declaration_unavailable() -> None:
    frozen_design = design(material_dimensions=None)
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest, events_sha256="c" * 64),
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


def test_write_paired_change_run_is_canonical_and_create_new(tmp_path: Path) -> None:
    frozen_design = design()
    frozen_digest = digest_model(frozen_design)
    trace = [
        trace_entry(side="baseline", trace_design_sha256=frozen_digest),
        trace_entry(side="candidate", trace_design_sha256=frozen_digest, events_sha256="c" * 64),
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
        trace_entry(side="candidate", trace_design_sha256=frozen_digest, events_sha256="c" * 64),
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
