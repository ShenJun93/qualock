from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import qualock.commands as commands_module
from qualock.change_targeting.canonical import canonical_assessment_bytes
from qualock.evidence.bundle_models import EvidenceBundleError
from qualock.evidence.export import export_evidence_bundle
from qualock.evidence.fingerprint import sha256_canonical
from qualock.evidence.provenance import (
    EvidenceProvenanceError,
    read_evidence_provenance,
)
from qualock.history.loader import scan_results
from qualock.pricing.sidecar import scan_pricing
from qualock.protocols.paired_change.run_sidecar import read_paired_change_run
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.schedule import Side
from qualock.targeted_execution.commands import execute_targeted_check
from qualock.targeted_execution.models import TargetedExecutionError
from qualock.targeted_execution.storage import sha256_file
from tests.unit.test_commands import FakeBackend, FakeResolver, setup_project

RAW_CONTEXT_SENTINEL = "DO-NOT-PERSIST-RAW-CONTEXT"


def _write_canary(
    root: Path,
    *,
    filename: str,
    canary_id: str,
    contract_id: str,
    critical: bool = False,
) -> None:
    canary_dir = root / ".qualock/canaries"
    (canary_dir / filename).write_text(
        f"""schema_version: 1
id: {canary_id}
name: {canary_id}
repository:
  url: https://example.invalid/{canary_id}.git
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
critical: {str(critical).lower()}
coverage:
  - contract_id: {contract_id}
    context_requirements: {{}}
""",
        encoding="utf-8",
    )


def _setup_target_project(root: Path) -> None:
    setup_project(root)
    (root / ".qualock/canaries/sample.yaml").unlink()
    _write_canary(
        root,
        filename="01-probe-a.yaml",
        canary_id="probe-a",
        contract_id="command.execution",
    )
    _write_canary(
        root,
        filename="02-probe-b.yaml",
        canary_id="probe-b",
        contract_id="tool.inventory",
    )
    _write_canary(
        root,
        filename="03-z-alternative-c.yaml",
        canary_id="z-alternative-c",
        contract_id="command.execution",
    )
    _write_canary(
        root,
        filename="04-critical-d.yaml",
        canary_id="critical-d",
        contract_id="mcp.visibility",
        critical=True,
    )

    commands_module.execute_baseline(
        root,
        "codex@0.150.0",
        resolver=FakeResolver(),
        backend=FakeBackend({"0.150.0"}),
        qualification_id="baseline-targeted",
        created_at="2026-09-18T00:00:00Z",
    )


def _write_signal_context(root: Path) -> tuple[Path, Path]:
    signal = root / "signal.yaml"
    signal.write_text(
        """schema_version: 0
agent: codex
baseline_version: "0.150.0"
candidate_version: "0.151.0"
impacts:
  - contract_id: command.execution
  - contract_id: tool.inventory
""",
        encoding="utf-8",
    )
    context = root / "context.yaml"
    context.write_text(
        f"""schema_version: 0
facts:
  unrelated.secret: "{RAW_CONTEXT_SENTINEL}"
""",
        encoding="utf-8",
    )
    return signal, context


def _ready_run(
    root: Path,
    *,
    backend: FakeBackend | None = None,
    qualification_id: str = "target-check-q1",
    max_attempts: int | None = None,
    max_tokens: int | None = None,
):
    _setup_target_project(root)
    signal, context = _write_signal_context(root)
    return execute_targeted_check(
        root,
        signal,
        context,
        resolver=FakeResolver(),
        backend=backend or FakeBackend({"0.150.0", "0.151.0"}),
        qualification_id=qualification_id,
        max_attempts=max_attempts,
        max_tokens=max_tokens,
    )


def test_targeted_execution_runs_exact_selected_sources_and_binds_evidence(tmp_path: Path) -> None:
    backend = FakeBackend({"0.150.0", "0.151.0"})
    outcome = _ready_run(tmp_path, backend=backend)

    assert outcome.assessment.selected_sources == ("probe-a", "probe-b")
    assert tuple(item.canary_id for item in outcome.result.executions) == (
        "probe-a",
        "probe-b",
    )
    assert backend.prepared == ["probe-a", "probe-b"]
    assert {item[0] for item in backend.calls} == {"probe-a", "probe-b"}
    assert "z-alternative-c" not in backend.prepared
    assert "critical-d" not in backend.prepared

    result_dir = tmp_path / ".qualock/results/targeted/target-check-q1"
    assert outcome.result_dir == result_dir
    assert {path.name for path in result_dir.iterdir()} == {
        "targeted-report.md",
        "targeted-report.json",
        "targeted-qualification.json",
        "targeted-evidence-provenance-v1.json",
        "targeted-paired-change-run-v1.json",
        "targeted-run-v1.json",
        "pricing.json",
    }
    assert not (result_dir / "report.json").exists()
    assert not (result_dir / "qualification.json").exists()
    assert not (result_dir / "evidence-provenance.json").exists()
    assert not (result_dir / "paired-change-run-v1.json").exists()

    provenance = read_evidence_provenance(
        result_dir / "targeted-evidence-provenance-v1.json"
    )
    assert tuple(item.canary_id for item in provenance.canaries) == (
        "probe-a",
        "probe-b",
    )
    paired = read_paired_change_run(result_dir / "targeted-paired-change-run-v1.json")
    assert paired.protocol_design.suite_sha256 == outcome.receipt.selected_suite_sha256
    assert tuple(item.canary_id for item in paired.protocol_design.canaries) == (
        "probe-a",
        "probe-b",
    )

    receipt = outcome.receipt
    config, all_canaries = commands_module.load_project(tmp_path)
    lock = commands_module.read_baseline_lock(tmp_path / ".qualock/baseline.lock")
    selected_canaries = tuple(
        canary
        for canary in all_canaries
        if canary.id in outcome.assessment.selected_sources
    )
    assert receipt.project_suite_sha256 == commands_module.suite_fingerprint(
        all_canaries
    )
    assert receipt.selected_suite_sha256 == commands_module.suite_fingerprint(
        selected_canaries
    )
    assert receipt.config_sha256 == commands_module.config_fingerprint(config)
    assert receipt.baseline_lock_sha256 == sha256_canonical(
        lock.model_dump(mode="json")
    )
    assert receipt.project_suite_sha256 != receipt.selected_suite_sha256
    assert receipt.assessment_sha256 == hashlib.sha256(
        canonical_assessment_bytes(outcome.assessment)
    ).hexdigest()
    assert receipt.run_order_sha256 == sha256_canonical(outcome.result.run_order)
    assert receipt.selected_sources == ("probe-a", "probe-b")
    assert receipt.attempted_sources == ("probe-a", "probe-b")
    assert receipt.qualification_verdict is Verdict.PASS
    assert receipt.artifacts.targeted_report_json == sha256_file(
        result_dir / "targeted-report.json"
    )
    assert receipt.artifacts.targeted_qualification_json == sha256_file(
        result_dir / "targeted-qualification.json"
    )
    assert receipt.artifacts.targeted_evidence_provenance_v1_json == sha256_file(
        result_dir / "targeted-evidence-provenance-v1.json"
    )
    assert receipt.artifacts.targeted_paired_change_run_v1_json == sha256_file(
        result_dir / "targeted-paired-change-run-v1.json"
    )
    assert set(receipt.artifacts.model_dump()) == {
        "targeted_report_json",
        "targeted_qualification_json",
        "targeted_evidence_provenance_v1_json",
        "targeted_paired_change_run_v1_json",
    }

    for artifact in result_dir.iterdir():
        if artifact.is_file():
            assert RAW_CONTEXT_SENTINEL not in artifact.read_text(
                encoding="utf-8", errors="ignore"
            )
    results_root = tmp_path / ".qualock/results"
    assert all(
        artifact.parent == result_dir
        for artifact in results_root.rglob("targeted-*")
        if artifact.is_file()
    )

    summary = scan_results(results_root)
    assert all(item.qualification_id != "target-check-q1" for item in summary.loaded)
    pricing = scan_pricing(summary)
    assert all(item.qualification_id != "target-check-q1" for item in pricing.records)

    with pytest.raises(EvidenceBundleError):
        export_evidence_bundle(
            tmp_path,
            "target-check-q1",
            tmp_path / "export-direct-targeted",
        )
    with pytest.raises(EvidenceBundleError):
        export_evidence_bundle(
            tmp_path,
            "targeted/target-check-q1",
            tmp_path / "export-unsafe-targeted",
        )


class PrepareFailBackend(FakeBackend):
    def prepare(self, canary, qualification_id: str):
        self.prepared.append(canary.id)
        if canary.id == "probe-a":
            raise RuntimeError("prepare failed")
        return super().prepare(canary, qualification_id)


def test_prepare_failure_never_falls_back_to_alternative_source(tmp_path: Path) -> None:
    _setup_target_project(tmp_path)
    signal, context = _write_signal_context(tmp_path)
    backend = PrepareFailBackend({"0.150.0", "0.151.0"})

    with pytest.raises(RuntimeError, match="prepare failed"):
        execute_targeted_check(
            tmp_path,
            signal,
            context,
            resolver=FakeResolver(),
            backend=backend,
            qualification_id="target-check-prepare-fail",
        )

    assert backend.prepared == ["probe-a"]
    assert "z-alternative-c" not in backend.prepared
    assert not (tmp_path / ".qualock/results/targeted").exists()


class InvalidAttemptBackend(FakeBackend):
    def run_attempt(
        self,
        *,
        canary,
        prepared,
        binary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=False,
            valid=False,
            duration_ms=1,
            usage=Usage(input_tokens=1, output_tokens=1, observed=True),
            invalid_reason="synthetic invalid attempt",
        )


def test_invalid_attempts_do_not_trigger_replacement_source(tmp_path: Path) -> None:
    backend = InvalidAttemptBackend({"0.150.0", "0.151.0"})
    outcome = _ready_run(
        tmp_path,
        backend=backend,
        qualification_id="target-check-invalid",
    )

    assert outcome.result.verdict is Verdict.INCOMPLETE
    assert backend.prepared == ["probe-a", "probe-b"]
    assert "z-alternative-c" not in backend.prepared
    assert outcome.receipt.selected_sources == ("probe-a", "probe-b")
    assert outcome.receipt.attempted_sources == ("probe-a", "probe-b")


@pytest.mark.parametrize(
    ("budget_name", "budget_value"),
    [("max_attempts", 6), ("max_tokens", 1)],
)
def test_budget_skip_does_not_widen_selected_set(
    tmp_path: Path,
    budget_name: str,
    budget_value: int,
) -> None:
    backend = FakeBackend({"0.150.0", "0.151.0"})
    kwargs = {budget_name: budget_value}
    outcome = _ready_run(
        tmp_path,
        backend=backend,
        qualification_id=f"target-check-{budget_name}",
        **kwargs,
    )

    assert outcome.result.verdict is Verdict.INCOMPLETE
    assert backend.prepared == ["probe-a"]
    assert "z-alternative-c" not in backend.prepared
    assert outcome.receipt.selected_sources == ("probe-a", "probe-b")
    assert outcome.receipt.attempted_sources == ("probe-a",)


def test_required_evidence_failure_leaves_partial_dir_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_target_project(tmp_path)
    signal, context = _write_signal_context(tmp_path)

    def fail_provenance(*args: object, **kwargs: object) -> None:
        raise EvidenceProvenanceError("synthetic evidence failure")

    import qualock.targeted_execution.commands as targeted_commands

    monkeypatch.setattr(targeted_commands, "write_evidence_provenance", fail_provenance)

    with pytest.raises(TargetedExecutionError, match="targeted evidence"):
        execute_targeted_check(
            tmp_path,
            signal,
            context,
            resolver=FakeResolver(),
            backend=FakeBackend({"0.150.0", "0.151.0"}),
            qualification_id="target-check-evidence-fail",
        )

    result_dir = (
        tmp_path / ".qualock/results/targeted/target-check-evidence-fail"
    )
    assert result_dir.is_dir()
    assert (result_dir / "targeted-report.json").is_file()
    assert not (result_dir / "targeted-run-v1.json").exists()


def test_pricing_failure_is_advisory_and_not_receipt_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_pricing(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic pricing failure")

    monkeypatch.setattr(commands_module, "write_pricing_sidecar", fail_pricing)
    outcome = _ready_run(
        tmp_path,
        qualification_id="target-check-pricing-fail",
    )

    assert outcome.result.verdict is Verdict.PASS
    assert not (outcome.result_dir / "pricing.json").exists()
    assert set(outcome.receipt.artifacts.model_dump()) == {
        "targeted_report_json",
        "targeted_qualification_json",
        "targeted_evidence_provenance_v1_json",
        "targeted_paired_change_run_v1_json",
    }
