"""Pure paired-change/v1 canary claim transition."""

from __future__ import annotations

from qualock.evidence.bundle_models import VerifiedEvidenceBundle

from .models import (
    CanaryClaimV1,
    ClaimClass,
    ConditionStatus,
    ConditionType,
    ProtocolConditionV1,
    ProtocolEvidenceV1,
)

_QUALIFICATION_ORDER = (
    ConditionType.EVIDENCE_BOUND,
    ConditionType.DESIGN_FROZEN,
    ConditionType.STATE_BOUND,
)

_CANARY_ORDER = (
    ConditionType.PREPARATION_EQUIVALENT,
    ConditionType.BASELINE_STABLE,
    ConditionType.ATTEMPTS_COMPLETE,
    ConditionType.ATTEMPTS_ISOLATED,
    ConditionType.ORDER_VALID,
    ConditionType.TEMPORAL_PAIR_VALID,
    ConditionType.MATERIAL_DIMENSIONS_CONTROLLED,
)


def _canonical_conditions(
    conditions: tuple[ProtocolConditionV1, ...],
    order: tuple[ConditionType, ...],
) -> tuple[ProtocolConditionV1, ...] | None:
    by_type = {item.type: item for item in conditions}
    if len(by_type) != len(conditions) or set(by_type) != set(order):
        return None
    return tuple(by_type[item] for item in order)


def derive_canary_claim(
    bundle: VerifiedEvidenceBundle,
    evidence: ProtocolEvidenceV1,
    canary_id: str,
    qualification_conditions: tuple[ProtocolConditionV1, ...],
    canary_conditions: tuple[ProtocolConditionV1, ...],
) -> CanaryClaimV1:
    """Derive one deterministic claim from bound outcomes and required gates."""

    canonical_qualification = _canonical_conditions(
        qualification_conditions, _QUALIFICATION_ORDER
    )
    canonical_canary = _canonical_conditions(canary_conditions, _CANARY_ORDER)
    stored_conditions = canary_conditions if canonical_canary is None else canonical_canary
    unresolved = CanaryClaimV1(
        canary_id=canary_id,
        conditions=stored_conditions,
        claim=ClaimClass.UNRESOLVED,
    )
    if canonical_qualification is None or canonical_canary is None:
        return unresolved
    if any(
        item.status is not ConditionStatus.TRUE
        for item in (*canonical_qualification, *canonical_canary)
    ):
        return unresolved

    execution = next(
        (item for item in bundle.report.executions if item.canary_id == canary_id),
        None,
    )
    design = next(
        (
            item
            for item in evidence.protocol_design.canaries
            if item.canary_id == canary_id
        ),
        None,
    )
    if execution is None or design is None:
        return unresolved

    repetitions = evidence.protocol_design.repetitions
    expected = set(range(1, repetitions + 1))
    baseline = tuple(item for item in execution.attempts if item.side == "baseline")
    candidate = tuple(item for item in execution.attempts if item.side == "candidate")
    if (
        len(baseline) != repetitions
        or {item.repetition for item in baseline} != expected
        or any(not item.valid or not item.success for item in baseline)
        or len(candidate) != repetitions
        or {item.repetition for item in candidate} != expected
        or any(not item.valid for item in candidate)
    ):
        return unresolved

    claim = (
        ClaimClass.NO_REGRESSION_OBSERVED
        if all(item.success for item in candidate)
        else ClaimClass.ATTRIBUTABLE_CHANGESET
    )
    return CanaryClaimV1(
        canary_id=canary_id,
        conditions=canonical_canary,
        claim=claim,
    )
