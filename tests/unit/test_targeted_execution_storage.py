import hashlib
import json
from pathlib import Path

import pytest

from qualock.change_targeting.models import AssessmentStatus, CoverageAssessmentV0
from qualock.evidence.fingerprint import canonical_json
from qualock.qualification.models import CanaryExecution, QualificationResult, Verdict
from qualock.targeted_execution.models import (
    TargetedArtifactHashesV1,
    TargetedExecutionError,
    TargetedRunV1,
)
from qualock.targeted_execution.storage import (
    sha256_file,
    targeted_results_dir,
    write_targeted_qualification_artifacts,
    write_targeted_run,
)


def ready_assessment() -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.READY,
        relevant_contracts=("command.execution",),
        selected_sources=("probe-a",),
    )


def sample_result(*, qualification_id: str = "target-check-q1") -> QualificationResult:
    execution = CanaryExecution(
        canary_id="probe-a",
        critical=True,
        prepared_image_digest="sha256:" + "9" * 64,
        attempts=(),
        baseline_successes=3,
        candidate_successes=3,
        baseline_valid=3,
        candidate_valid=3,
        verdict=Verdict.PASS,
        reason="stable",
    )
    return QualificationResult(
        qualification_id=qualification_id,
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        verdict=Verdict.PASS,
        executions=(execution,),
        reasons=(),
        run_order=(("probe-a", "baseline", 1), ("probe-a", "candidate", 1)),
        max_attempts=8,
        max_tokens=50_000,
        attempts_used=6,
        observed_tokens=1200,
    )


def sample_run() -> TargetedRunV1:
    return TargetedRunV1(
        qualification_id="target-check-q1",
        assessment=ready_assessment(),
        assessment_sha256="d" * 64,
        project_suite_sha256="e" * 64,
        selected_suite_sha256="f" * 64,
        config_sha256="1" * 64,
        baseline_lock_sha256="2" * 64,
        selected_sources=("probe-a",),
        attempted_sources=("probe-a",),
        qualification_verdict=Verdict.PASS,
        run_order_sha256="3" * 64,
        artifacts=TargetedArtifactHashesV1(
            targeted_report_json="4" * 64,
            targeted_qualification_json="5" * 64,
            targeted_evidence_provenance_v1_json="6" * 64,
            targeted_paired_change_run_v1_json="7" * 64,
        ),
    )


def test_targeted_results_dir_is_isolated_under_targeted(tmp_path: Path) -> None:
    assert targeted_results_dir(tmp_path) == tmp_path / "targeted"


def test_write_targeted_artifacts_uses_only_targeted_filenames(tmp_path: Path) -> None:
    root = write_targeted_qualification_artifacts(
        tmp_path,
        result=sample_result(),
        assessment=ready_assessment(),
        selected_sources=("probe-a",),
        agent_display_name="Codex",
    )

    assert root == tmp_path / "targeted" / "target-check-q1"
    assert {path.name for path in root.iterdir()} == {
        "targeted-report.md",
        "targeted-report.json",
        "targeted-qualification.json",
    }
    assert not (root / "report.json").exists()
    assert not (root / "qualification.json").exists()


def test_targeted_report_binds_scope_selection_and_assessment(tmp_path: Path) -> None:
    root = write_targeted_qualification_artifacts(
        tmp_path,
        result=sample_result(),
        assessment=ready_assessment(),
        selected_sources=("probe-a",),
        agent_display_name="Codex",
    )

    report = json.loads((root / "targeted-report.json").read_text(encoding="utf-8"))
    qualification = json.loads(
        (root / "targeted-qualification.json").read_text(encoding="utf-8")
    )
    markdown = (root / "targeted-report.md").read_text(encoding="utf-8")

    assert report["schema_version"] == 1
    assert report["scope"] == "targeted"
    assert report["selected_sources"] == ["probe-a"]
    assert report["assessment"]["signal_sha256"] == "a" * 64
    assert report["assessment"]["target_context_sha256"] == "b" * 64
    assert report["assessment"]["coverage_sha256"] == "c" * 64
    assert report["result"]["verdict"] == "pass"
    assert qualification == {
        "schema_version": 1,
        "scope": "targeted",
        "qualification_id": "target-check-q1",
        "baseline_version": "0.150.0",
        "candidate_version": "0.151.0",
        "verdict": "pass",
        "run_order": [["probe-a", "baseline", 1], ["probe-a", "candidate", 1]],
        "max_attempts": 8,
        "max_tokens": 50_000,
        "attempts_used": 6,
        "observed_tokens": 1200,
        "selected_sources": ["probe-a"],
    }
    assert "selected sources only" in markdown.lower()
    assert "SAFE TO UPDATE" not in markdown
    assert "SAFE TO UPDATE" not in json.dumps(report)


def test_sha256_file_hashes_exact_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "bytes.bin"
    payload = b'{"z":1}\r\n'
    path.write_bytes(payload)
    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_existing_targeted_qualification_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "targeted" / "target-check-q1"
    root.mkdir(parents=True)

    with pytest.raises(TargetedExecutionError, match="already exists"):
        write_targeted_qualification_artifacts(
            tmp_path,
            result=sample_result(),
            assessment=ready_assessment(),
            selected_sources=("probe-a",),
            agent_display_name="Codex",
        )


@pytest.mark.parametrize("qualification_id", ["", ".", "..", "a/b", r"a\b"])
def test_unsafe_qualification_id_is_rejected_before_directory_creation(
    tmp_path: Path, qualification_id: str
) -> None:
    with pytest.raises(TargetedExecutionError, match="unsafe targeted qualification_id"):
        write_targeted_qualification_artifacts(
            tmp_path,
            result=sample_result(qualification_id=qualification_id),
            assessment=ready_assessment(),
            selected_sources=("probe-a",),
            agent_display_name="Codex",
        )
    assert not (tmp_path / "targeted").exists()


def test_write_targeted_run_uses_exclusive_canonical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "targeted-run-v1.json"
    value = sample_run()

    assert write_targeted_run(path, value) == path
    assert path.read_bytes() == canonical_json(value.model_dump(mode="json")) + b"\n"

    with pytest.raises(TargetedExecutionError, match="already exists"):
        write_targeted_run(path, value)
