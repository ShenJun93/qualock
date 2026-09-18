from __future__ import annotations

from qualock.change_targeting.models import CoverageAssessmentV0
from qualock.change_targeting.render import render_coverage_assessment
from qualock.report.render import render_terminal

from .commands import TargetedExecutionOutcome


def render_targeted_execution_not_started(
    assessment: CoverageAssessmentV0 | None,
    diagnostic: str | None = None,
) -> str:
    parts: list[str] = []
    if assessment is not None:
        parts.append(render_coverage_assessment(assessment).rstrip())
    if diagnostic:
        parts.append(diagnostic.rstrip())
    parts.append("Targeted execution: NOT STARTED")
    return "\n".join(parts) + "\n"


def render_targeted_qualification(
    outcome: TargetedExecutionOutcome,
    *,
    agent_display_name: str,
) -> str:
    technical = render_terminal(
        outcome.result,
        agent_display_name=agent_display_name,
    )
    selected = ", ".join(outcome.assessment.selected_sources)
    return (
        "QuaLock Targeted Qualification\n\n"
        "Scope: selected sources only; not a full-suite update-safety verdict.\n"
        f"Selected sources: {selected}\n"
        f"Result directory: {outcome.result_dir}\n\n"
        + technical
    )
