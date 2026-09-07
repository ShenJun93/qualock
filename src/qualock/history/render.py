from qualock.history.models import HistoryAnalysis

_RUNTIME_NOTE = (
    "Note: this excludes setup, preparation, and CLI overhead "
    "(model-attempt time only)."
)


def _format_runtime(milliseconds: float) -> str:
    seconds = round(milliseconds / 1000)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60}s"


def _format_tokens(tokens: float) -> str:
    return f"{round(tokens):,}"


def _format_rate(rate: float) -> str:
    return f"{round(rate * 100)}%"


def render_history_text(analysis: HistoryAnalysis) -> str:
    lines: list[str] = []

    lines.append(f"Loaded {analysis.loaded_reports} qualification report(s).")
    lines.append(f"Ignored {len(analysis.ignored_reports)} report(s).")
    if analysis.loaded_reports == 0:
        lines.append(
            "No qualification history found yet. "
            "Run `qualock check` to start building history."
        )
    lines.append("")

    lines.append("Estimated model-attempt runtime")
    suite = analysis.suite_estimate
    if suite.runtime_ms is not None:
        lines.append(f"  Suite: {_format_runtime(suite.runtime_ms)}")
    else:
        lines.append("  Suite estimate unavailable.")
        if suite.missing_runtime_canaries:
            missing = ", ".join(suite.missing_runtime_canaries)
            lines.append(f"  Missing runtime sample for: {missing}")
    for estimate in analysis.per_canary_estimates:
        if estimate.runtime_median_ms is not None:
            lines.append(
                f"  {estimate.canary_id}: {_format_runtime(estimate.runtime_median_ms)}"
            )
    lines.append(f"  {_RUNTIME_NOTE}")
    lines.append("")

    lines.append("Estimated model tokens")
    if suite.tokens is not None:
        lines.append(f"  Suite: {_format_tokens(suite.tokens)}")
    else:
        lines.append("  Suite estimate unavailable.")
        if suite.missing_token_canaries:
            missing = ", ".join(suite.missing_token_canaries)
            lines.append(f"  Missing token sample for: {missing}")
    for estimate in analysis.per_canary_estimates:
        if estimate.token_median is not None:
            lines.append(f"  {estimate.canary_id}: {_format_tokens(estimate.token_median)}")
    lines.append("")

    lines.append("Ranked current canaries")
    if not analysis.ranked:
        lines.append("  (none)")
    for effectiveness in analysis.ranked:
        assert effectiveness.detection_rate is not None
        lines.append(
            f"  {effectiveness.canary_id}: "
            f"{_format_rate(effectiveness.detection_rate)} "
            f"({effectiveness.detections}/{effectiveness.eligible_samples})"
        )
    lines.append("")

    lines.append("Not-enough-history current canaries")
    if not analysis.not_enough_history:
        lines.append("  (none)")
    for effectiveness in analysis.not_enough_history:
        lines.append(
            f"  {effectiveness.canary_id}: not enough history "
            f"({effectiveness.eligible_samples} sample(s))"
        )
    lines.append("")

    if analysis.ignored_reports:
        lines.append("Ignored qualification directories")
        for failure in analysis.ignored_reports:
            lines.append(f"  {failure.qualification_dir.name}: {failure.reason}")

    return "\n".join(lines)
