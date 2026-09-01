from dataclasses import asdict
from typing import Any

from rich.console import Console
from rich.table import Table

from qualock.qualification.models import QualificationResult, Verdict


def _encode(value: Any) -> Any:
    if isinstance(value, Verdict):
        return value.value
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def render_json(result: QualificationResult) -> dict[str, Any]:
    return _encode(asdict(result))


def render_markdown(result: QualificationResult) -> str:
    lines = [
        "# Qualock qualification",
        "",
        f"**Codex:** `{result.baseline_version}` → `{result.candidate_version}`",
        "",
        "| Canary | Baseline | Candidate | Verdict |",
        "| --- | ---: | ---: | --- |",
    ]
    for execution in result.executions:
        lines.append(
            "| "
            f"{execution.canary_id} | "
            f"{execution.baseline_successes}/{execution.baseline_valid} | "
            f"{execution.candidate_successes}/{execution.candidate_valid} | "
            f"{execution.verdict.value.upper()} |"
        )
    lines.extend(["", f"## Verdict: {result.verdict.value.upper()}"])
    if result.reasons:
        lines.append("")
        lines.extend(f"- {reason}" for reason in result.reasons)
    lines.append("")
    return "\n".join(lines)


def render_terminal(result: QualificationResult) -> str:
    console = Console(record=True, force_terminal=False, color_system=None, width=100)
    console.print(
        f"Qualock qualification: Codex {result.baseline_version} -> {result.candidate_version}"
    )
    table = Table(show_header=True)
    table.add_column("Canary")
    table.add_column("Baseline", justify="right")
    table.add_column("Candidate", justify="right")
    table.add_column("Verdict")
    for execution in result.executions:
        table.add_row(
            execution.canary_id,
            f"{execution.baseline_successes}/{execution.baseline_valid}",
            f"{execution.candidate_successes}/{execution.candidate_valid}",
            execution.verdict.value.upper(),
        )
    console.print(table)
    console.print(f"Quality  {result.verdict.value.upper()}")
    for reason in result.reasons:
        console.print(f"- {reason}")
    return console.export_text()


def render_safety_terminal(summary: "SafetySummary", evidence_path: str) -> str:
    workflow_labels = {
        Verdict.PASS: "OK",
        Verdict.WARN: "REVIEW",
        Verdict.BLOCK: "REGRESSED",
        Verdict.INCOMPLETE: "UNKNOWN",
    }
    lines = [
        "QuaLock Safety Check",
        "",
        summary.headline,
        "",
        summary.explanation,
        "",
        f"Codex {summary.baseline_version} -> {summary.candidate_version}",
        "",
        "Protected workflows",
    ]
    for workflow in summary.workflows:
        lines.append(
            f"- {workflow_labels[workflow.verdict]}: {workflow.name}  "
            f"{workflow.baseline_successes}/{workflow.baseline_valid} -> "
            f"{workflow.candidate_successes}/{workflow.candidate_valid}"
        )
    lines.extend(
        [
            "",
            "Recommendation:",
            summary.recommendation,
            "",
            f"Technical evidence: {evidence_path}",
            "",
        ]
    )
    return "\n".join(lines)
