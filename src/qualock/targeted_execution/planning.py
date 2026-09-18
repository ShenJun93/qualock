from dataclasses import dataclass
from pathlib import Path

from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.baseline.models import BaselineLock
from qualock.canary.models import CanarySpec
from qualock.change_targeting.commands import coverage_declarations
from qualock.change_targeting.io import load_change_signal, load_target_context
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeSignalV0,
    CoverageAssessmentV0,
)
from qualock.change_targeting.planner import assess_change
from qualock.config.models import QualockConfig
from qualock.evidence.fingerprint import sha256_canonical
from qualock.project import (
    config_fingerprint,
    load_project,
    project_dir,
    suite_fingerprint,
)

from .models import TargetedExecutionError


class TargetedExecutionNotReady(Exception):
    def __init__(self, assessment: CoverageAssessmentV0) -> None:
        super().__init__(f"targeted execution is not ready: {assessment.status.value}")
        self.assessment = assessment


@dataclass(frozen=True)
class TargetedExecutionPlan:
    signal: ChangeSignalV0
    assessment: CoverageAssessmentV0
    config: QualockConfig
    lock: BaselineLock
    canaries: tuple[CanarySpec, ...]
    selected_canaries: tuple[CanarySpec, ...]
    project_suite_sha256: str
    selected_suite_sha256: str
    config_sha256: str
    baseline_lock_sha256: str


def _validate_identity(
    signal: ChangeSignalV0,
    config: QualockConfig,
    lock: BaselineLock,
) -> None:
    if config.agent.name != signal.agent:
        raise TargetedExecutionError(
            f"signal agent {signal.agent} does not match project config agent "
            f"{config.agent.name}"
        )
    if lock.agent.name != signal.agent:
        raise TargetedExecutionError(
            f"signal agent {signal.agent} does not match baseline lock agent "
            f"{lock.agent.name}"
        )
    if lock.agent.version != signal.baseline_version:
        raise TargetedExecutionError(
            f"signal baseline version {signal.baseline_version} does not match "
            f"baseline lock version {lock.agent.version}"
        )


def prepare_targeted_execution(
    root: Path,
    signal_path: Path,
    context_path: Path,
) -> TargetedExecutionPlan:
    signal = load_change_signal(signal_path)
    context = load_target_context(context_path)
    config, loaded_canaries = load_project(root)
    canaries = tuple(loaded_canaries)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")

    _validate_identity(signal, config, lock)

    project_suite_sha256 = suite_fingerprint(canaries)
    config_sha256 = config_fingerprint(config)
    assert_suite_fresh(lock, project_suite_sha256, config_sha256)

    assessment = assess_change(
        signal,
        context,
        coverage_declarations(canaries),
    )
    if assessment.status is not AssessmentStatus.READY:
        raise TargetedExecutionNotReady(assessment)

    canaries_by_id = {canary.id: canary for canary in canaries}
    try:
        selected_canaries = tuple(
            canaries_by_id[source_id]
            for source_id in assessment.selected_sources
        )
    except KeyError as exc:
        missing_source_id = str(exc.args[0])
        raise TargetedExecutionError(
            f"selected source is missing from project: {missing_source_id}"
        ) from exc

    if not selected_canaries:
        raise TargetedExecutionError(
            "READY assessment must select at least one source"
        )

    selected_suite_sha256 = suite_fingerprint(selected_canaries)
    baseline_lock_sha256 = sha256_canonical(lock.model_dump(mode="json"))

    return TargetedExecutionPlan(
        signal=signal,
        assessment=assessment,
        config=config,
        lock=lock,
        canaries=canaries,
        selected_canaries=selected_canaries,
        project_suite_sha256=project_suite_sha256,
        selected_suite_sha256=selected_suite_sha256,
        config_sha256=config_sha256,
        baseline_lock_sha256=baseline_lock_sha256,
    )
