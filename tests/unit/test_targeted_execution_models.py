import pytest
from pydantic import ValidationError

from qualock.change_targeting.models import AssessmentStatus, CoverageAssessmentV0
from qualock.qualification.models import Verdict
from qualock.targeted_execution.models import TargetedArtifactHashesV1, TargetedRunV1


def ready_assessment(
    *,
    selected_sources: tuple[str, ...] = ("probe-a",),
) -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.READY,
        relevant_contracts=("command.execution",),
        selected_sources=selected_sources,
    )


def artifact_hashes() -> TargetedArtifactHashesV1:
    return TargetedArtifactHashesV1(
        targeted_report_json="4" * 64,
        targeted_qualification_json="5" * 64,
        targeted_evidence_provenance_v1_json="6" * 64,
        targeted_paired_change_run_v1_json="7" * 64,
    )


def build_run(**overrides: object) -> TargetedRunV1:
    values: dict[str, object] = {
        "schema_version": 1,
        "protocol_id": "selected-source-execution/v1",
        "scope": "targeted",
        "qualification_id": "target-check-q1",
        "assessment": ready_assessment(),
        "assessment_sha256": "d" * 64,
        "project_suite_sha256": "e" * 64,
        "selected_suite_sha256": "f" * 64,
        "config_sha256": "1" * 64,
        "baseline_lock_sha256": "2" * 64,
        "selected_sources": ("probe-a",),
        "attempted_sources": ("probe-a",),
        "qualification_verdict": Verdict.PASS,
        "run_order_sha256": "3" * 64,
        "artifacts": artifact_hashes(),
    }
    values.update(overrides)
    return TargetedRunV1(**values)


def test_targeted_run_binds_protocol_scope_and_ready_selection() -> None:
    run = build_run()
    assert run.protocol_id == "selected-source-execution/v1"
    assert run.scope == "targeted"
    assert run.selected_sources == run.assessment.selected_sources


@pytest.mark.parametrize("field", ["protocol_id", "scope"])
def test_targeted_run_rejects_wrong_protocol_or_scope(field: str) -> None:
    replacement = "wrong"
    with pytest.raises(ValidationError):
        build_run(**{field: replacement})


@pytest.mark.parametrize(
    ("selected_sources", "attempted_sources"),
    [
        ((), ()),
        (("probe-b", "probe-a"), ("probe-a",)),
        (("probe-a", "probe-a"), ("probe-a",)),
        (("probe-a", "probe-b"), ("probe-b", "probe-a")),
        (("probe-a",), ("probe-b",)),
        (("probe-a",), ("probe-a", "probe-a")),
    ],
)
def test_targeted_run_rejects_invalid_source_bindings(
    selected_sources: tuple[str, ...],
    attempted_sources: tuple[str, ...],
) -> None:
    assessment_sources = selected_sources or ("probe-a",)
    with pytest.raises(ValidationError):
        build_run(
            assessment=ready_assessment(selected_sources=assessment_sources),
            selected_sources=selected_sources,
            attempted_sources=attempted_sources,
        )


def test_targeted_run_rejects_selected_source_mismatch() -> None:
    with pytest.raises(ValidationError):
        build_run(
            assessment=ready_assessment(selected_sources=("probe-a", "probe-b")),
            selected_sources=("probe-a",),
        )


def test_targeted_run_rejects_non_ready_assessment() -> None:
    assessment = CoverageAssessmentV0(
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.NOT_APPLICABLE,
    )
    with pytest.raises(ValidationError):
        build_run(assessment=assessment)


@pytest.mark.parametrize(
    "field",
    [
        "assessment_sha256",
        "project_suite_sha256",
        "selected_suite_sha256",
        "config_sha256",
        "baseline_lock_sha256",
        "run_order_sha256",
    ],
)
def test_targeted_run_rejects_noncanonical_sha_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        build_run(**{field: "A" * 64})


def test_targeted_artifact_hashes_are_explicit_and_strict() -> None:
    with pytest.raises(ValidationError):
        TargetedArtifactHashesV1(
            targeted_report_json="4" * 64,
            targeted_qualification_json="5" * 64,
            targeted_evidence_provenance_v1_json="6" * 64,
            targeted_paired_change_run_v1_json="7" * 64,
            unexpected_json="8" * 64,
        )


def test_targeted_run_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        build_run(unexpected="nope")
