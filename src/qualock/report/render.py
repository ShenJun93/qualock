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
