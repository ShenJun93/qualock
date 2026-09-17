from collections.abc import Sequence
from pathlib import Path

from qualock.canary.models import CanarySpec
from qualock.project import load_project

from .canonical import normalize_coverage
from .io import load_change_signal, load_target_context
from .models import CoverageAssessmentV0, CoverageDeclarationV0
from .planner import assess_change


def coverage_declarations(canaries: Sequence[CanarySpec]) -> tuple[CoverageDeclarationV0, ...]:
    declarations = [
        CoverageDeclarationV0(
            source_id=canary.id,
            contract_id=coverage.contract_id,
            context_requirements=coverage.context_requirements,
        )
        for canary in canaries
        for coverage in canary.coverage
    ]
    return normalize_coverage(declarations)


def execute_target_change(
    root: Path, signal_path: Path, context_path: Path
) -> CoverageAssessmentV0:
    signal = load_change_signal(signal_path)
    context = load_target_context(context_path)
    _config, canaries = load_project(root)
    declarations = coverage_declarations(canaries)
    return assess_change(signal, context, declarations)
