from .models import CoverageAssessmentV0


def _csv(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def render_coverage_assessment(assessment: CoverageAssessmentV0) -> str:
    lines = [
        "QuaLock Change Targeting",
        "",
        f"Status: {assessment.status.value}",
        f"Signal SHA256: {assessment.signal_sha256}",
        f"Target context SHA256: {assessment.target_context_sha256}",
        f"Coverage SHA256: {assessment.coverage_sha256}",
        f"Relevant contracts: {_csv(assessment.relevant_contracts)}",
        f"Selected sources: {_csv(assessment.selected_sources)}",
    ]

    if assessment.uncovered:
        lines.append("Coverage gaps:")
        lines.extend(
            f"- {item.contract_id}: {item.reason.value}" for item in assessment.uncovered
        )
    else:
        lines.append("Coverage gaps: none")

    if assessment.unresolved:
        lines.append("Unresolved:")
        for item in assessment.unresolved:
            missing = ",".join(item.missing_context_keys)
            sources = ",".join(item.source_ids) if item.source_ids else "none"
            lines.append(
                f"- {item.contract_id}: {item.reason.value}; missing={missing}; sources={sources}"
            )
    else:
        lines.append("Unresolved: none")

    return "\n".join(lines) + "\n"
