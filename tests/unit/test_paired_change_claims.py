from pathlib import Path

import pytest

from qualock.protocols.paired_change.claims import derive_canary_claim
from qualock.protocols.paired_change.conditions import (
    evaluate_canary_conditions,
    evaluate_qualification_conditions,
)
from qualock.protocols.paired_change.models import (
    ClaimClass,
    ConditionStatus,
    ConditionType,
    ProtocolConditionV1,
)
from tests.unit.test_paired_change_conditions import (
    _baseline_incomplete,
    _baseline_violation,
    _replace_public_attempt,
    _valid_inputs,
)

VALID_REASONS = {
    ConditionType.EVIDENCE_BOUND: {
        ConditionStatus.TRUE: "EvidenceVerified",
        ConditionStatus.FALSE: "EvidenceMismatch",
        ConditionStatus.UNKNOWN: "EvidenceUnavailable",
    },
    ConditionType.DESIGN_FROZEN: {
        ConditionStatus.TRUE: "DesignVerified",
        ConditionStatus.FALSE: "DesignChanged",
        ConditionStatus.UNKNOWN: "DesignFreezeUnavailable",
    },
    ConditionType.STATE_BOUND: {
        ConditionStatus.TRUE: "StateVerified",
        ConditionStatus.FALSE: "StateMismatch",
        ConditionStatus.UNKNOWN: "StateIdentityIncomplete",
    },
    ConditionType.PREPARATION_EQUIVALENT: {
        ConditionStatus.TRUE: "PreparationVerified",
        ConditionStatus.FALSE: "PreparationMismatch",
        ConditionStatus.UNKNOWN: "PreparationUnverified",
    },
    ConditionType.BASELINE_STABLE: {
        ConditionStatus.TRUE: "BaselineStable",
        ConditionStatus.FALSE: "BaselineViolation",
        ConditionStatus.UNKNOWN: "BaselineEvidenceIncomplete",
    },
    ConditionType.ATTEMPTS_COMPLETE: {
        ConditionStatus.TRUE: "AttemptsComplete",
        ConditionStatus.FALSE: "AttemptMissing",
        ConditionStatus.UNKNOWN: "AttemptLayoutUnknown",
    },
    ConditionType.ATTEMPTS_ISOLATED: {
        ConditionStatus.TRUE: "IsolationVerified",
        ConditionStatus.FALSE: "IsolationReuseDetected",
        ConditionStatus.UNKNOWN: "IsolationUnverified",
    },
    ConditionType.ORDER_VALID: {
        ConditionStatus.TRUE: "OrderVerified",
        ConditionStatus.FALSE: "PairLayoutInvalid",
        ConditionStatus.UNKNOWN: "OrderUnknown",
    },
    ConditionType.TEMPORAL_PAIR_VALID: {
        ConditionStatus.TRUE: "TemporalPairVerified",
        ConditionStatus.FALSE: "TemporalGapExceeded",
        ConditionStatus.UNKNOWN: "TemporalEvidenceMissing",
    },
    ConditionType.MATERIAL_DIMENSIONS_CONTROLLED: {
        ConditionStatus.TRUE: "MaterialControlsVerified",
        ConditionStatus.FALSE: "UnexpectedMaterialChange",
        ConditionStatus.UNKNOWN: "RequiredDimensionOpaque",
    },
}


def _derive(tmp_path: Path, *, candidate_success: bool = True):
    bundle, evidence = _valid_inputs(tmp_path)
    if not candidate_success:
        for index, attempt in enumerate(bundle.report.executions[0].attempts):
            if attempt.side == "candidate":
                bundle = _replace_public_attempt(bundle, index, success=False)
    qualification = evaluate_qualification_conditions(bundle, evidence)
    canary = evaluate_canary_conditions(bundle, evidence, "sample")
    return bundle, evidence, qualification, canary


def _with_status(
    conditions: tuple[ProtocolConditionV1, ...],
    index: int,
    status: ConditionStatus,
) -> tuple[ProtocolConditionV1, ...]:
    changed = list(conditions)
    changed[index] = changed[index].model_copy(
        update={"status": status, "reason": VALID_REASONS[changed[index].type][status]}
    )
    return tuple(changed)


@pytest.mark.parametrize(
    "candidate_success,expected",
    [
        (True, ClaimClass.NO_REGRESSION_OBSERVED),
        (False, ClaimClass.ATTRIBUTABLE_CHANGESET),
    ],
)
def test_all_true_gates_derive_observed_candidate_outcome(
    tmp_path: Path, candidate_success: bool, expected: ClaimClass
) -> None:
    bundle, evidence, qualification, canary = _derive(
        tmp_path, candidate_success=candidate_success
    )

    claim = derive_canary_claim(
        bundle, evidence, "sample", qualification, canary
    )

    assert claim.canary_id == "sample"
    assert claim.conditions == canary
    assert claim.claim is expected


@pytest.mark.parametrize("status", [ConditionStatus.FALSE, ConditionStatus.UNKNOWN])
@pytest.mark.parametrize("condition_index", range(3))
def test_each_non_true_qualification_gate_forces_unresolved(
    tmp_path: Path, condition_index: int, status: ConditionStatus
) -> None:
    bundle, evidence, qualification, canary = _derive(tmp_path, candidate_success=False)
    qualification = _with_status(qualification, condition_index, status)

    claim = derive_canary_claim(
        bundle, evidence, "sample", qualification, canary
    )

    assert claim.claim is ClaimClass.UNRESOLVED


@pytest.mark.parametrize("status", [ConditionStatus.FALSE, ConditionStatus.UNKNOWN])
@pytest.mark.parametrize("condition_index", range(7))
def test_each_non_true_canary_gate_forces_only_that_claim_unresolved(
    tmp_path: Path, condition_index: int, status: ConditionStatus
) -> None:
    bundle, evidence, qualification, canary = _derive(tmp_path, candidate_success=False)
    changed = _with_status(canary, condition_index, status)

    unresolved = derive_canary_claim(
        bundle, evidence, "sample", qualification, changed
    )
    unaffected = derive_canary_claim(
        bundle, evidence, "sample", qualification, canary
    )

    assert unresolved.claim is ClaimClass.UNRESOLVED
    assert unaffected.claim is ClaimClass.ATTRIBUTABLE_CHANGESET


@pytest.mark.parametrize("mutate", [_baseline_violation, _baseline_incomplete])
def test_unstable_or_incomplete_baseline_is_unresolved_even_if_passed_conditions_are_stale(
    tmp_path: Path, mutate
) -> None:
    bundle, evidence, qualification, canary = _derive(tmp_path, candidate_success=False)
    bundle, evidence = mutate(bundle, evidence)

    claim = derive_canary_claim(
        bundle, evidence, "sample", qualification, canary
    )

    assert claim.claim is ClaimClass.UNRESOLVED


@pytest.mark.parametrize("malformation", ["missing", "duplicate", "unexpected"])
def test_malformed_condition_sets_are_rejected_instead_of_emitted(
    tmp_path: Path, malformation: str
) -> None:
    bundle, evidence, qualification, canary = _derive(tmp_path)
    if malformation == "missing":
        changed = canary[:-1]
    elif malformation == "duplicate":
        changed = (*canary[:-1], canary[0])
    else:
        changed = (*canary[:-1], qualification[0])

    with pytest.raises(ValueError, match="canonical canary conditions"):
        derive_canary_claim(bundle, evidence, "sample", qualification, changed)


def test_fabricated_true_reason_cannot_authorize_claim(
    tmp_path: Path,
) -> None:
    bundle, evidence, qualification, canary = _derive(tmp_path, candidate_success=False)
    fabricated = list(canary)
    fabricated[0] = fabricated[0].model_copy(update={"reason": "FabricatedSuccess"})

    with pytest.raises(ValueError, match="invalid condition status/reason"):
        derive_canary_claim(bundle, evidence, "sample", qualification, tuple(fabricated))
