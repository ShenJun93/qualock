from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping

from qualock.qualification.models import QualificationResult, Verdict


class SafetyStatus(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DONT_UPDATE = "dont_update"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class WorkflowSafety:
    canary_id: str
    name: str
    verdict: Verdict
    baseline_successes: int
    baseline_valid: int
    candidate_successes: int
    candidate_valid: int


@dataclass(frozen=True)
class SafetySummary:
    status: SafetyStatus
    headline: str
    explanation: str
    recommendation: str
    baseline_version: str
    candidate_version: str
    workflows: tuple[WorkflowSafety, ...]


_SUITE_COPY = {
    Verdict.PASS: (
        SafetyStatus.SAFE,
        "SAFE TO UPDATE",
        "All protected workflows matched the stable baseline in this check.",
    ),
    Verdict.WARN: (
        SafetyStatus.CAUTION,
        "REVIEW BEFORE UPDATING",
        "QuaLock found a result that needs review before you update.",
    ),
    Verdict.BLOCK: (
        SafetyStatus.DONT_UPDATE,
        "DON'T UPDATE YET",
        "At least one critical protected workflow regressed.",
    ),
    Verdict.INCOMPLETE: (
        SafetyStatus.INCOMPLETE,
        "CHECK COULD NOT FINISH",
        "QuaLock could not collect enough valid evidence to make a safe recommendation.",
    ),
}


def _recommendation(result: QualificationResult) -> str:
    if result.verdict is Verdict.PASS:
        return (
            f"Codex {result.candidate_version} looks safe for the workflows you protect. "
            "You can update with the same caution you use for any tool upgrade."
        )
    if result.verdict is Verdict.WARN:
        return (
            "Review the workflows marked REVIEW before updating. "
            f"Keep Codex {result.baseline_version} available until you are comfortable with the differences."
        )
    if result.verdict is Verdict.BLOCK:
        return (
            f"Keep using Codex {result.baseline_version} for now. "
            f"Do not update to Codex {result.candidate_version} until the regression is understood."
        )
    return (
        "Run the check again after fixing the invalid or missing evidence. "
        "Do not make an update decision from this incomplete run."
    )


def build_safety_summary(
    result: QualificationResult,
    display_names: Mapping[str, str],
) -> SafetySummary:
    status, headline, explanation = _SUITE_COPY[result.verdict]
    workflows = tuple(
        WorkflowSafety(
            canary_id=execution.canary_id,
            name=display_names.get(execution.canary_id, execution.canary_id),
            verdict=execution.verdict,
            baseline_successes=execution.baseline_successes,
            baseline_valid=execution.baseline_valid,
            candidate_successes=execution.candidate_successes,
            candidate_valid=execution.candidate_valid,
        )
        for execution in result.executions
    )
    return SafetySummary(
        status=status,
        headline=headline,
        explanation=explanation,
        recommendation=_recommendation(result),
        baseline_version=result.baseline_version,
        candidate_version=result.candidate_version,
        workflows=workflows,
    )
