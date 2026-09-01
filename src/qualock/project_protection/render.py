from collections.abc import Sequence

from .models import ProjectProtectResult, ProjectVerifyResult, ProtectionRun, ProtectionStatus


def _run_label(status: ProtectionStatus) -> str:
    if status is ProtectionStatus.PASS:
        return "OK"
    if status is ProtectionStatus.FAIL:
        return "REGRESSED"
    return "UNKNOWN"


def _runs(lines: list[str], runs: Sequence[ProtectionRun]) -> None:
    lines.append("Protected workflows")
    for run in runs:
        lines.append(f"- {_run_label(run.status)}: {run.name}")


def render_protect_terminal(result: ProjectProtectResult, evidence_path: str) -> str:
    if result.status is ProtectionStatus.PASS:
        headline = "PROTECTED"
        explanation = "All configured workflows pass in this known-good state."
        recommendation = "You can let your AI make changes, then run qualock verify."
    elif result.status is ProtectionStatus.FAIL:
        headline = "NOT PROTECTED"
        explanation = "At least one workflow is already failing, so QuaLock did not record a known-good state."
        recommendation = "Fix the failing workflow, then run qualock protect again."
    else:
        headline = "CHECK COULD NOT FINISH"
        explanation = "QuaLock could not collect enough evidence to record a known-good state."
        recommendation = "Fix the incomplete workflow, then run qualock protect again."
    lines = ["QuaLock Project Protection", "", headline, "", explanation, ""]
    _runs(lines, result.runs)
    lines.extend(["", "Recommendation:", recommendation, "", f"Technical evidence: {evidence_path}", ""])
    return "\n".join(lines)


def render_verify_terminal(result: ProjectVerifyResult, evidence_path: str) -> str:
    if result.status is ProtectionStatus.PASS:
        headline = "SAFE TO KEEP"
        explanation = "All protected workflows still work after the project changes."
        recommendation = "The protected behavior is still intact."
    elif result.status is ProtectionStatus.FAIL:
        headline = "DON'T KEEP THIS CHANGE"
        explanation = "At least one previously working workflow now fails."
        recommendation = "Ask your AI to fix the regressed workflow before keeping this change."
    else:
        headline = "CHECK COULD NOT FINISH"
        explanation = "QuaLock could not collect enough evidence to judge this change."
        recommendation = "Fix the incomplete workflow and run qualock verify again."
    lines = ["QuaLock Project Check", "", headline, "", explanation, ""]
    _runs(lines, result.runs)
    lines.extend(["", "Recommendation:", recommendation, "", f"Technical evidence: {evidence_path}", ""])
    return "\n".join(lines)
