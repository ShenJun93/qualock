from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from qualock.change_targeting.canonical import canonical_assessment_bytes
from qualock.change_targeting.models import CoverageAssessmentV0
from qualock.commands import (
    Resolver,
    _execute_qualification_core,
    _qualification_id,
    _write_pricing_sidecar_best_effort,
    agent_display_name,
)
from qualock.evidence.fingerprint import sha256_canonical
from qualock.evidence.provenance import (
    EvidenceProvenanceError,
    build_evidence_provenance,
    write_evidence_provenance,
)
from qualock.project import project_dir
from qualock.protocols.paired_change.design import build_agent_dependency_state
from qualock.protocols.paired_change.run_sidecar import (
    PairedChangeRunError,
    build_paired_change_run,
    write_paired_change_run,
)
from qualock.qualification.models import QualificationResult
from qualock.run.executor import QualificationBackend

from .models import (
    TargetedArtifactHashesV1,
    TargetedExecutionError,
    TargetedRunV1,
)
from .planning import prepare_targeted_execution
from .storage import (
    sha256_file,
    write_targeted_qualification_artifacts,
    write_targeted_run,
)


@dataclass(frozen=True)
class TargetedExecutionOutcome:
    agent_name: str
    assessment: CoverageAssessmentV0
    result: QualificationResult
    result_dir: Path
    receipt: TargetedRunV1


def execute_targeted_check(
    root: Path,
    signal_path: Path,
    context_path: Path,
    *,
    resolver: Resolver | None = None,
    backend: QualificationBackend | None = None,
    qualification_id: str | None = None,
    max_attempts: int | None = None,
    max_tokens: int | None = None,
) -> TargetedExecutionOutcome:
    plan = prepare_targeted_execution(root, signal_path, context_path)
    qid = qualification_id or _qualification_id("target-check")

    core = _execute_qualification_core(
        root=root,
        config=plan.config,
        lock=plan.lock,
        agent_name=plan.signal.agent,
        candidate_version=plan.signal.candidate_version,
        execution_canaries=plan.selected_canaries,
        qualification_id=qid,
        resolver=resolver,
        backend=backend,
        max_attempts=max_attempts,
        max_tokens=max_tokens,
    )

    result_dir = write_targeted_qualification_artifacts(
        project_dir(root) / "results",
        result=core.result,
        assessment=plan.assessment,
        selected_sources=plan.assessment.selected_sources,
        agent_display_name=agent_display_name(plan.signal.agent),
    )

    try:
        provenance = build_evidence_provenance(
            lock=plan.lock,
            baseline_binary=core.baseline_binary,
            candidate_binary=core.candidate_binary,
            config=plan.config,
            canaries=plan.selected_canaries,
            result=core.result,
        )
        write_evidence_provenance(
            result_dir / "targeted-evidence-provenance-v1.json",
            provenance,
        )
    except (EvidenceProvenanceError, ValidationError, OSError) as exc:
        raise TargetedExecutionError(
            "targeted evidence provenance could not be written"
        ) from exc

    try:
        paired = build_paired_change_run(
            protocol_design=core.protocol_design,
            protocol_design_sha256=core.protocol_design_sha256,
            qualification_id=core.result.qualification_id,
            baseline_state=build_agent_dependency_state(
                core.baseline_binary,
                plan.lock.model,
            ),
            candidate_state=build_agent_dependency_state(
                core.candidate_binary,
                plan.lock.model,
            ),
            result=core.result,
            trace=core.trace,
        )
        write_paired_change_run(
            result_dir / "targeted-paired-change-run-v1.json",
            paired,
        )
    except (PairedChangeRunError, ValidationError, OSError) as exc:
        raise TargetedExecutionError(
            "targeted paired-change evidence could not be written"
        ) from exc

    _write_pricing_sidecar_best_effort(
        result_dir,
        plan.config,
        core.result,
        core.run_started_at,
        core.run_finished_at,
    )

    attempted_sources = tuple(
        sorted(
            {
                execution.canary_id
                for execution in core.result.executions
                if execution.attempts
            }
        )
    )
    receipt = TargetedRunV1(
        schema_version=1,
        protocol_id="selected-source-execution/v1",
        qualification_id=core.result.qualification_id,
        assessment=plan.assessment,
        assessment_sha256=hashlib.sha256(
            canonical_assessment_bytes(plan.assessment)
        ).hexdigest(),
        project_suite_sha256=plan.project_suite_sha256,
        selected_suite_sha256=plan.selected_suite_sha256,
        config_sha256=plan.config_sha256,
        baseline_lock_sha256=plan.baseline_lock_sha256,
        selected_sources=plan.assessment.selected_sources,
        attempted_sources=attempted_sources,
        qualification_verdict=core.result.verdict,
        run_order_sha256=sha256_canonical(core.result.run_order),
        artifacts=TargetedArtifactHashesV1(
            targeted_report_json=sha256_file(result_dir / "targeted-report.json"),
            targeted_qualification_json=sha256_file(
                result_dir / "targeted-qualification.json"
            ),
            targeted_evidence_provenance_v1_json=sha256_file(
                result_dir / "targeted-evidence-provenance-v1.json"
            ),
            targeted_paired_change_run_v1_json=sha256_file(
                result_dir / "targeted-paired-change-run-v1.json"
            ),
        ),
    )
    write_targeted_run(result_dir / "targeted-run-v1.json", receipt)

    return TargetedExecutionOutcome(
        agent_name=plan.signal.agent,
        assessment=plan.assessment,
        result=core.result,
        result_dir=result_dir,
        receipt=receipt,
    )
