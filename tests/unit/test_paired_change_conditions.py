import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from qualock.evidence.bundle_models import VerifiedEvidenceBundle
from qualock.protocols.paired_change.conditions import (
    evaluate_canary_conditions,
    evaluate_qualification_conditions,
)
from qualock.protocols.paired_change.design import derive_changeset
from qualock.protocols.paired_change.fingerprint import digest_model
from qualock.protocols.paired_change.models import (
    ConditionStatus,
    ConditionType,
    MaterialDimension,
    ProtocolEvidenceV1,
)
from tests.unit.test_paired_change_materialize import _export_inputs

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64

Inputs = tuple[VerifiedEvidenceBundle, ProtocolEvidenceV1]
Mutation = Callable[[VerifiedEvidenceBundle, ProtocolEvidenceV1], Inputs]


def _valid_inputs(tmp_path: Path) -> Inputs:
    run, bundle = _export_inputs(tmp_path)
    original = run.canaries[0]
    changed_pairs = []
    for pair in original.pairs:
        pair_order = tuple(
            side
            for canary, side, repetition in bundle.report.run_order
            if canary == "sample" and repetition == pair.repetition
        )
        attempts = tuple(
            attempt.model_copy(
                update={
                    "started_offset_ms": 10 if attempt.side == pair_order[0] else 30,
                    "finished_offset_ms": 20 if attempt.side == pair_order[0] else 40,
                    "isolation_instance_sha256": hashlib.sha256(
                        f"{attempt.side}:{attempt.repetition}".encode()
                    ).hexdigest(),
                    "preparation_sha256": SHA_C,
                    "isolation_sha256": SHA_D,
                    "resource_sha256": SHA_A,
                }
            )
            for attempt in pair.attempts
        )
        changed_pairs.append(pair.model_copy(update={"attempts": attempts}))
    prepared = bundle.report.executions[0].prepared_image_digest.removeprefix("sha256:")
    changed_canary = original.model_copy(
        update={"prepared_target_sha256": prepared, "pairs": tuple(changed_pairs)}
    )
    design_canary = run.protocol_design.canaries[0].model_copy(
        update={
            "material_dimensions": (MaterialDimension.AGENT_BINARY,),
            "max_pair_gap_ms": 100,
            "preparation_sha256": SHA_C,
            "isolation_sha256": SHA_D,
            "resource_sha256": SHA_A,
        }
    )
    design = run.protocol_design.model_copy(update={"canaries": (design_canary,)})
    design_sha = digest_model(design)
    rebound_pairs = tuple(
        pair.model_copy(
            update={
                "attempts": tuple(
                    attempt.model_copy(update={"protocol_design_sha256": design_sha})
                    for attempt in pair.attempts
                )
            }
        )
        for pair in changed_pairs
    )
    changed_canary = changed_canary.model_copy(update={"pairs": rebound_pairs})
    changeset = derive_changeset(run.baseline_state, run.candidate_state)
    evidence = ProtocolEvidenceV1.model_validate(
        {
            **run.model_dump(mode="json"),
            "protocol_design": design.model_dump(mode="json"),
            "protocol_design_sha256": design_sha,
            "changeset_sha256": digest_model(changeset),
            "canaries": [changed_canary.model_dump(mode="json")],
            "evidence_manifest_sha256": bundle.manifest_sha256,
        }
    )
    return bundle, evidence


def _replace_attempt(
    evidence: ProtocolEvidenceV1, index: int, **updates: object
) -> ProtocolEvidenceV1:
    canary = evidence.canaries[0]
    pair = canary.pairs[0]
    attempts = list(pair.attempts)
    attempts[index] = attempts[index].model_copy(update=updates)
    changed_pair = pair.model_copy(update={"attempts": tuple(attempts)})
    return evidence.model_copy(
        update={
            "canaries": (
                canary.model_copy(update={"pairs": (changed_pair, *canary.pairs[1:])}),
            )
        }
    )


def _replace_public_attempt(
    bundle: VerifiedEvidenceBundle, index: int, **updates: object
) -> VerifiedEvidenceBundle:
    execution = bundle.report.executions[0]
    attempts = list(execution.attempts)
    attempts[index] = attempts[index].model_copy(update=updates)
    report = bundle.report.model_copy(
        update={
            "executions": (
                execution.model_copy(update={"attempts": tuple(attempts)}),
            )
        }
    )
    return bundle.model_copy(update={"report": report})


def _identity_mutation(field: str, value: object) -> Mutation:
    def mutate(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
        state = evidence.baseline_state.model_copy(update={field: value})
        return bundle, evidence.model_copy(update={"baseline_state": state})

    return mutate


def _design_mutation(field: str, value: object, *, rehash: bool = False) -> Mutation:
    def mutate(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
        design = evidence.protocol_design.model_copy(update={field: value})
        digest = digest_model(design) if rehash else evidence.protocol_design_sha256
        return bundle, evidence.model_copy(
            update={"protocol_design": design, "protocol_design_sha256": digest}
        )

    return mutate


def _canary_design_mutation(**updates: object) -> Mutation:
    def mutate(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
        item = evidence.protocol_design.canaries[0].model_copy(update=updates)
        design = evidence.protocol_design.model_copy(update={"canaries": (item,)})
        design_sha = digest_model(design)
        changed = evidence.model_copy(
            update={"protocol_design": design, "protocol_design_sha256": design_sha}
        )
        for index in range(2):
            changed = _replace_attempt(changed, index, protocol_design_sha256=design_sha)
        return bundle, changed

    return mutate


def _evidence_mismatch(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return bundle, evidence.model_copy(update={"evidence_manifest_sha256": SHA_D})


def _evidence_unavailable(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return bundle.model_copy(update={"manifest_sha256": None}), evidence


def _design_binding_unavailable(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return bundle, _replace_attempt(evidence, 0, protocol_design_sha256=None)


def _preparation_mismatch(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return bundle, _replace_attempt(evidence, 1, preparation_sha256=SHA_B)


def _preparation_unknown(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return bundle, _replace_attempt(evidence, 1, preparation_sha256=None)


def _baseline_violation(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return _replace_public_attempt(bundle, 0, success=False), evidence


def _baseline_incomplete(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return _replace_public_attempt(bundle, 0, valid=False), evidence


def _attempt_missing(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    execution = bundle.report.executions[0]
    report = bundle.report.model_copy(
        update={"executions": (execution.model_copy(update={"attempts": execution.attempts[:1]}),)}
    )
    return bundle.model_copy(update={"report": report}), evidence


def _attempt_invalid(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return _replace_public_attempt(bundle, 1, valid=False), evidence


def _attempt_layout_unknown(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> Inputs:
    return _replace_public_attempt(bundle, 1, side="opaque"), evidence


def _isolation_reuse(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    reused = evidence.canaries[0].pairs[0].attempts[0].isolation_instance_sha256
    return bundle, _replace_attempt(evidence, 1, isolation_instance_sha256=reused)


def _isolation_unknown(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return bundle, _replace_attempt(evidence, 1, isolation_instance_sha256=None)


def _run_order(bundle: VerifiedEvidenceBundle, order: tuple[tuple[str, str, int], ...]) -> VerifiedEvidenceBundle:
    return bundle.model_copy(update={"report": bundle.report.model_copy(update={"run_order": order})})


def _order_invalid(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return _run_order(bundle, (("sample", "baseline", 1), ("sample", "baseline", 1))), evidence


def _order_unknown(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return _run_order(bundle, (("sample", "opaque", 1), ("sample", "candidate", 1))), evidence


def _order_imbalanced(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    original_pair = evidence.canaries[0].pairs[0]
    pairs = []
    public_attempts = []
    for repetition in (1, 2):
        attempts = tuple(
            attempt.model_copy(update={"repetition": repetition})
            for attempt in original_pair.attempts
        )
        pairs.append(original_pair.model_copy(update={"repetition": repetition, "attempts": attempts}))
        public_attempts.extend(
            attempt.model_copy(update={"repetition": repetition})
            for attempt in bundle.report.executions[0].attempts
        )
    design = evidence.protocol_design.model_copy(update={"repetitions": 2})
    design_sha = digest_model(design)
    pairs = [
        pair.model_copy(
            update={
                "attempts": tuple(
                    attempt.model_copy(update={"protocol_design_sha256": design_sha})
                    for attempt in pair.attempts
                )
            }
        )
        for pair in pairs
    ]
    canary = evidence.canaries[0].model_copy(update={"pairs": tuple(pairs)})
    evidence = evidence.model_copy(
        update={
            "protocol_design": design,
            "protocol_design_sha256": design_sha,
            "canaries": (canary,),
        }
    )
    execution = bundle.report.executions[0].model_copy(update={"attempts": tuple(public_attempts)})
    report = bundle.report.model_copy(
        update={
            "executions": (execution,),
            "run_order": (
                ("sample", "baseline", 1),
                ("sample", "candidate", 1),
                ("sample", "baseline", 2),
                ("sample", "candidate", 2),
            ),
        }
    )
    return bundle.model_copy(update={"report": report}), evidence


def _temporal_gap(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    index = 0 if evidence.canaries[0].pairs[0].attempts[0].side == bundle.report.run_order[1][1] else 1
    return bundle, _replace_attempt(evidence, index, started_offset_ms=1000, finished_offset_ms=1010)


def _temporal_missing(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    index = 0 if evidence.canaries[0].pairs[0].attempts[0].side == bundle.report.run_order[1][1] else 1
    return bundle, _replace_attempt(evidence, index, started_offset_ms=None)


def _temporal_overlap(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    index = 0 if evidence.canaries[0].pairs[0].attempts[0].side == bundle.report.run_order[1][1] else 1
    return bundle, _replace_attempt(evidence, index, started_offset_ms=15, finished_offset_ms=25)


def _material_unexpected(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    mutation = _canary_design_mutation(
        material_dimensions=(MaterialDimension.AGENT_BINARY, MaterialDimension.RUNTIME_PROFILE),
        runtime_sha256=SHA_C,
    )
    bundle, changed = mutation(bundle, evidence)
    changed = _replace_attempt(changed, 0, runtime_sha256=SHA_C)
    changed = _replace_attempt(changed, 1, runtime_sha256=SHA_D)
    return bundle, changed


def _material_opaque(bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1) -> Inputs:
    return _canary_design_mutation(
        material_dimensions=(MaterialDimension.AGENT_BINARY, MaterialDimension.RUNTIME_PROFILE),
        runtime_sha256=None,
    )(bundle, evidence)


QUALIFICATION_CASES = (
    (ConditionType.EVIDENCE_BOUND, lambda b, e: (b, e), ConditionStatus.TRUE, "EvidenceVerified"),
    (ConditionType.EVIDENCE_BOUND, _evidence_mismatch, ConditionStatus.FALSE, "EvidenceMismatch"),
    (ConditionType.EVIDENCE_BOUND, _evidence_unavailable, ConditionStatus.UNKNOWN, "EvidenceUnavailable"),
    (ConditionType.DESIGN_FROZEN, lambda b, e: (b, e), ConditionStatus.TRUE, "DesignVerified"),
    (ConditionType.DESIGN_FROZEN, _design_mutation("suite_sha256", SHA_D), ConditionStatus.FALSE, "DesignChanged"),
    (ConditionType.DESIGN_FROZEN, _design_binding_unavailable, ConditionStatus.UNKNOWN, "DesignFreezeUnavailable"),
    (ConditionType.STATE_BOUND, lambda b, e: (b, e), ConditionStatus.TRUE, "StateVerified"),
    (ConditionType.STATE_BOUND, _identity_mutation("version", "9.9.9"), ConditionStatus.FALSE, "StateMismatch"),
    (ConditionType.STATE_BOUND, _identity_mutation("binary_sha256", None), ConditionStatus.UNKNOWN, "StateIdentityIncomplete"),
)


@pytest.mark.parametrize("condition_type,mutate,status,reason", QUALIFICATION_CASES)
def test_qualification_condition_truth_tables(
    tmp_path: Path,
    condition_type: ConditionType,
    mutate: Mutation,
    status: ConditionStatus,
    reason: str,
) -> None:
    bundle, evidence = mutate(*_valid_inputs(tmp_path))
    conditions = evaluate_qualification_conditions(bundle, evidence)
    selected = next(item for item in conditions if item.type is condition_type)
    assert (selected.status, selected.reason) == (status, reason)
    assert tuple(item.type for item in conditions) == (
        ConditionType.EVIDENCE_BOUND,
        ConditionType.DESIGN_FROZEN,
        ConditionType.STATE_BOUND,
    )


CANARY_CASES = (
    (ConditionType.PREPARATION_EQUIVALENT, lambda b, e: (b, e), ConditionStatus.TRUE, "PreparationVerified"),
    (ConditionType.PREPARATION_EQUIVALENT, _preparation_mismatch, ConditionStatus.FALSE, "PreparationMismatch"),
    (ConditionType.PREPARATION_EQUIVALENT, _preparation_unknown, ConditionStatus.UNKNOWN, "PreparationUnverified"),
    (ConditionType.BASELINE_STABLE, lambda b, e: (b, e), ConditionStatus.TRUE, "BaselineStable"),
    (ConditionType.BASELINE_STABLE, _baseline_violation, ConditionStatus.FALSE, "BaselineViolation"),
    (ConditionType.BASELINE_STABLE, _baseline_incomplete, ConditionStatus.UNKNOWN, "BaselineEvidenceIncomplete"),
    (ConditionType.ATTEMPTS_COMPLETE, lambda b, e: (b, e), ConditionStatus.TRUE, "AttemptsComplete"),
    (ConditionType.ATTEMPTS_COMPLETE, _attempt_missing, ConditionStatus.FALSE, "AttemptMissing"),
    (ConditionType.ATTEMPTS_COMPLETE, _attempt_invalid, ConditionStatus.FALSE, "AttemptInvalid"),
    (ConditionType.ATTEMPTS_COMPLETE, _attempt_layout_unknown, ConditionStatus.UNKNOWN, "AttemptLayoutUnknown"),
    (ConditionType.ATTEMPTS_ISOLATED, lambda b, e: (b, e), ConditionStatus.TRUE, "IsolationVerified"),
    (ConditionType.ATTEMPTS_ISOLATED, _isolation_reuse, ConditionStatus.FALSE, "IsolationReuseDetected"),
    (ConditionType.ATTEMPTS_ISOLATED, _isolation_unknown, ConditionStatus.UNKNOWN, "IsolationUnverified"),
    (ConditionType.ORDER_VALID, lambda b, e: (b, e), ConditionStatus.TRUE, "OrderVerified"),
    (ConditionType.ORDER_VALID, _order_invalid, ConditionStatus.FALSE, "PairLayoutInvalid"),
    (ConditionType.ORDER_VALID, _order_imbalanced, ConditionStatus.FALSE, "OrderImbalanced"),
    (ConditionType.ORDER_VALID, _order_unknown, ConditionStatus.UNKNOWN, "OrderUnknown"),
    (ConditionType.TEMPORAL_PAIR_VALID, lambda b, e: (b, e), ConditionStatus.TRUE, "TemporalPairVerified"),
    (ConditionType.TEMPORAL_PAIR_VALID, _temporal_gap, ConditionStatus.FALSE, "TemporalGapExceeded"),
    (ConditionType.TEMPORAL_PAIR_VALID, _temporal_overlap, ConditionStatus.FALSE, "PairLayoutInvalid"),
    (ConditionType.TEMPORAL_PAIR_VALID, _temporal_missing, ConditionStatus.UNKNOWN, "TemporalEvidenceMissing"),
    (ConditionType.MATERIAL_DIMENSIONS_CONTROLLED, lambda b, e: (b, e), ConditionStatus.TRUE, "MaterialControlsVerified"),
    (ConditionType.MATERIAL_DIMENSIONS_CONTROLLED, _material_unexpected, ConditionStatus.FALSE, "UnexpectedMaterialChange"),
    (ConditionType.MATERIAL_DIMENSIONS_CONTROLLED, _material_opaque, ConditionStatus.UNKNOWN, "RequiredDimensionOpaque"),
    (ConditionType.MATERIAL_DIMENSIONS_CONTROLLED, _canary_design_mutation(material_dimensions=None), ConditionStatus.UNKNOWN, "RequiredDimensionOpaque"),
)


@pytest.mark.parametrize("condition_type,mutate,status,reason", CANARY_CASES)
def test_canary_condition_truth_tables(
    tmp_path: Path,
    condition_type: ConditionType,
    mutate: Mutation,
    status: ConditionStatus,
    reason: str,
) -> None:
    bundle, evidence = mutate(*_valid_inputs(tmp_path))
    conditions = evaluate_canary_conditions(bundle, evidence, "sample")
    selected = next(item for item in conditions if item.type is condition_type)
    assert (selected.status, selected.reason) == (status, reason)
    assert tuple(item.type for item in conditions) == (
        ConditionType.PREPARATION_EQUIVALENT,
        ConditionType.BASELINE_STABLE,
        ConditionType.ATTEMPTS_COMPLETE,
        ConditionType.ATTEMPTS_ISOLATED,
        ConditionType.ORDER_VALID,
        ConditionType.TEMPORAL_PAIR_VALID,
        ConditionType.MATERIAL_DIMENSIONS_CONTROLLED,
    )
