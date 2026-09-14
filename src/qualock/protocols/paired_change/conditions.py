"""Pure evaluation of paired-change/v1 causal validity conditions."""

from __future__ import annotations

from collections.abc import Iterable

from qualock.evidence.bundle_models import (
    EvidenceManifest,
    PublicExecution,
    VerifiedEvidenceBundle,
)

from .design import derive_changeset
from .fingerprint import digest_model
from .models import (
    AgentDependencyStateV1,
    AttemptProtocolContextV1,
    CanaryProtocolDesignV1,
    CanaryRunEvidenceV1,
    ConditionStatus,
    ConditionType,
    MaterialDimension,
    ProtocolConditionV1,
    ProtocolEvidenceV1,
)


def _condition(
    condition_type: ConditionType, status: ConditionStatus, reason: str
) -> ProtocolConditionV1:
    return ProtocolConditionV1(type=condition_type, status=status, reason=reason)


def _public_execution(
    bundle: VerifiedEvidenceBundle, canary_id: str
) -> PublicExecution | None:
    return next(
        (item for item in bundle.report.executions if item.canary_id == canary_id),
        None,
    )


def _design_canary(
    evidence: ProtocolEvidenceV1, canary_id: str
) -> CanaryProtocolDesignV1 | None:
    return next(
        (item for item in evidence.protocol_design.canaries if item.canary_id == canary_id),
        None,
    )


def _run_canary(
    evidence: ProtocolEvidenceV1, canary_id: str
) -> CanaryRunEvidenceV1 | None:
    return next((item for item in evidence.canaries if item.canary_id == canary_id), None)


def _state_complete(state: AgentDependencyStateV1) -> bool:
    common_identity_complete = bool(
        state.agent_name
        and state.version
        and state.binary_sha256
        and state.model.id
        and state.model.reasoning_effort
    )
    support_identity_complete = (
        state.agent_name.lower() not in {"codex", "gemini"}
        or state.support_sha256 is not None
    )
    return common_identity_complete and support_identity_complete


def _state_matches_manifest(
    state: AgentDependencyStateV1,
    manifest: EvidenceManifest,
    *,
    baseline: bool,
) -> bool:
    identity = manifest.baseline_identity if baseline else manifest.candidate_identity
    return (
        state.agent_name == identity.name
        and state.version == identity.version
        and state.binary_sha256 == identity.binary_sha256
        and state.support_sha256 == identity.support_sha256
        and state.model.model_dump(mode="json") == manifest.model.model_dump(mode="json")
    )


def _evidence_bound(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> ProtocolConditionV1:
    if not bundle.manifest_sha256 or not evidence.evidence_manifest_sha256:
        return _condition(
            ConditionType.EVIDENCE_BOUND,
            ConditionStatus.UNKNOWN,
            "EvidenceUnavailable",
        )
    matches = (
        evidence.evidence_manifest_sha256 == bundle.manifest_sha256
        and evidence.qualification_id == bundle.manifest.qualification_id
        and evidence.qualification_id == bundle.report.qualification_id
    )
    return _condition(
        ConditionType.EVIDENCE_BOUND,
        ConditionStatus.TRUE if matches else ConditionStatus.FALSE,
        "EvidenceVerified" if matches else "EvidenceMismatch",
    )


def _public_slot_evidence_status(
    execution: PublicExecution, repetitions: int
) -> ConditionStatus:
    expected = {
        (side, repetition)
        for repetition in range(1, repetitions + 1)
        for side in ("baseline", "candidate")
    }
    slots = tuple((attempt.side, attempt.repetition) for attempt in execution.attempts)
    if len(set(slots)) != len(slots):
        return ConditionStatus.FALSE
    actual = set(slots)
    if actual <= expected:
        return ConditionStatus.TRUE if actual == expected else ConditionStatus.UNKNOWN
    return ConditionStatus.FALSE


def _design_frozen(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> ProtocolConditionV1:
    attempts = tuple(
        attempt
        for canary in evidence.canaries
        for pair in canary.pairs
        for attempt in pair.attempts
    )
    if not evidence.protocol_design_sha256:
        return _condition(
            ConditionType.DESIGN_FROZEN,
            ConditionStatus.UNKNOWN,
            "DesignFreezeUnavailable",
        )
    public_canaries = {item.canary_id: item for item in bundle.canaries.canaries}
    manifest_canaries = set(bundle.manifest.canaries)
    design_canaries = {item.canary_id: item for item in evidence.protocol_design.canaries}
    run_canaries = {item.canary_id: item for item in evidence.canaries}
    executions = {item.canary_id: item for item in bundle.report.executions}
    execution_canaries = set(executions)
    expected_canaries = set(design_canaries)
    if (
        not expected_canaries
        or not expected_canaries.issubset(public_canaries)
        or not expected_canaries.issubset(run_canaries)
        or not expected_canaries.issubset(execution_canaries)
    ):
        return _condition(
            ConditionType.DESIGN_FROZEN,
            ConditionStatus.UNKNOWN,
            "DesignFreezeUnavailable",
        )
    repetitions = evidence.protocol_design.repetitions
    expected_repetitions = set(range(1, repetitions + 1))
    run_slot_statuses = tuple(
        _slot_evidence_status(run_canaries[canary_id], repetitions)
        for canary_id in expected_canaries
    )
    public_slot_statuses = tuple(
        _public_slot_evidence_status(executions[canary_id], repetitions)
        for canary_id in expected_canaries
    )
    if (
        ConditionStatus.UNKNOWN in run_slot_statuses
        or ConditionStatus.UNKNOWN in public_slot_statuses
        or any(not attempt.protocol_design_sha256 for attempt in attempts)
    ):
        return _condition(
            ConditionType.DESIGN_FROZEN,
            ConditionStatus.UNKNOWN,
            "DesignFreezeUnavailable",
        )
    if (
        ConditionStatus.FALSE in run_slot_statuses
        or ConditionStatus.FALSE in public_slot_statuses
    ):
        return _condition(
            ConditionType.DESIGN_FROZEN,
            ConditionStatus.FALSE,
            "DesignChanged",
        )
    matches = (
        digest_model(evidence.protocol_design) == evidence.protocol_design_sha256
        and evidence.protocol_digest == evidence.protocol_design.protocol_digest
        and evidence.protocol_design.suite_sha256 == bundle.manifest.suite_sha256
        and evidence.protocol_design.config_sha256 == bundle.manifest.config_sha256
        and evidence.protocol_design.order_policy == "alternating-v1"
        and evidence.protocol_design.lifecycle == "FRESH"
        and expected_canaries == set(public_canaries)
        and expected_canaries == manifest_canaries
        and expected_canaries == set(run_canaries)
        and expected_canaries == execution_canaries
        and all(
            item.canary_fingerprint_sha256
            == public_canaries[canary_id].canary_fingerprint_sha256
            == bundle.manifest.canaries[canary_id].canary_fingerprint_sha256
            and public_canaries[canary_id].repetitions
            == bundle.manifest.canaries[canary_id].repetitions
            == repetitions
            for canary_id, item in design_canaries.items()
        )
        and all(
            attempt.protocol_design_sha256 == evidence.protocol_design_sha256
            for attempt in attempts
        )
        and all(
            run_canary.canary_id in design_canaries
            and run_canary.canary_fingerprint_sha256
            == design_canaries[run_canary.canary_id].canary_fingerprint_sha256
            for run_canary in evidence.canaries
        )
        and all(
            {
                (attempt.side, attempt.repetition): attempt.events_sha256
                for attempt in executions[canary_id].attempts
            }
            == {
                (attempt.side, attempt.repetition): attempt.events_sha256
                for pair in run_canaries[canary_id].pairs
                for attempt in pair.attempts
            }
            for canary_id in expected_canaries
        )
        and all(
            {pair.repetition for pair in run_canary.pairs} == expected_repetitions
            and len(run_canary.pairs) == evidence.protocol_design.repetitions
            and all(
                pair.repetition in expected_repetitions
                and len(pair.attempts) == 2
                and {(item.side, item.repetition) for item in pair.attempts}
                == {
                    ("baseline", pair.repetition),
                    ("candidate", pair.repetition),
                }
                for pair in run_canary.pairs
            )
            for run_canary in evidence.canaries
        )
    )
    return _condition(
        ConditionType.DESIGN_FROZEN,
        ConditionStatus.TRUE if matches else ConditionStatus.FALSE,
        "DesignVerified" if matches else "DesignChanged",
    )


def _state_bound(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> ProtocolConditionV1:
    if not _state_complete(evidence.baseline_state) or not _state_complete(
        evidence.candidate_state
    ):
        return _condition(
            ConditionType.STATE_BOUND,
            ConditionStatus.UNKNOWN,
            "StateIdentityIncomplete",
        )
    matches = _state_matches_manifest(
        evidence.baseline_state, bundle.manifest, baseline=True
    ) and _state_matches_manifest(
        evidence.candidate_state, bundle.manifest, baseline=False
    )
    return _condition(
        ConditionType.STATE_BOUND,
        ConditionStatus.TRUE if matches else ConditionStatus.FALSE,
        "StateVerified" if matches else "StateMismatch",
    )


def evaluate_qualification_conditions(
    bundle: VerifiedEvidenceBundle, evidence: ProtocolEvidenceV1
) -> tuple[ProtocolConditionV1, ...]:
    """Evaluate qualification-wide gates in canonical protocol order."""

    return (
        _evidence_bound(bundle, evidence),
        _design_frozen(bundle, evidence),
        _state_bound(bundle, evidence),
    )


def _preparation_equivalent(
    bundle: VerifiedEvidenceBundle,
    design: CanaryProtocolDesignV1 | None,
    run: CanaryRunEvidenceV1 | None,
    public: PublicExecution | None,
    repetitions: int | None,
) -> ProtocolConditionV1:
    attempts = () if run is None else tuple(a for p in run.pairs for a in p.attempts)
    expected_target = (
        None
        if public is None or not public.prepared_image_digest
        else public.prepared_image_digest.removeprefix("sha256:")
    )
    if (
        design is None
        or run is None
        or expected_target is None
        or run.prepared_target_sha256 is None
        or design.preparation_sha256 is None
        or not attempts
        or repetitions is None
        or _slot_evidence_status(run, repetitions) is not ConditionStatus.TRUE
        or any(item.preparation_sha256 is None for item in attempts)
    ):
        return _condition(
            ConditionType.PREPARATION_EQUIVALENT,
            ConditionStatus.UNKNOWN,
            "PreparationUnverified",
        )
    matches = run.prepared_target_sha256 == expected_target and all(
        item.preparation_sha256 == design.preparation_sha256 for item in attempts
    )
    return _condition(
        ConditionType.PREPARATION_EQUIVALENT,
        ConditionStatus.TRUE if matches else ConditionStatus.FALSE,
        "PreparationVerified" if matches else "PreparationMismatch",
    )


def _baseline_stable(
    public: PublicExecution | None, repetitions: int | None
) -> ProtocolConditionV1:
    if public is None or repetitions is None:
        status, reason = ConditionStatus.UNKNOWN, "BaselineEvidenceIncomplete"
    else:
        baseline = tuple(item for item in public.attempts if item.side == "baseline")
        expected = set(range(1, repetitions + 1))
        if (
            {item.repetition for item in baseline} != expected
            or len(baseline) != repetitions
            or any(not item.valid for item in baseline)
        ):
            status, reason = ConditionStatus.UNKNOWN, "BaselineEvidenceIncomplete"
        elif any(not item.success for item in baseline):
            status, reason = ConditionStatus.FALSE, "BaselineViolation"
        else:
            status, reason = ConditionStatus.TRUE, "BaselineStable"
    return _condition(ConditionType.BASELINE_STABLE, status, reason)


def _attempts_complete(
    public: PublicExecution | None,
    run: CanaryRunEvidenceV1 | None,
    repetitions: int | None,
) -> ProtocolConditionV1:
    if public is None or run is None or repetitions is None or any(
        item.side not in ("baseline", "candidate") for item in public.attempts
    ):
        status, reason = ConditionStatus.UNKNOWN, "AttemptLayoutUnknown"
    else:
        expected = {
            (side, repetition)
            for repetition in range(1, repetitions + 1)
            for side in ("baseline", "candidate")
        }
        public_slots = {(item.side, item.repetition) for item in public.attempts}
        protocol_attempts = tuple(
            attempt for pair in run.pairs for attempt in pair.attempts
        )
        protocol_slots = {
            (item.side, item.repetition) for item in protocol_attempts
        }
        if (
            public_slots != expected
            or protocol_slots != expected
            or len(public.attempts) != len(expected)
            or len(protocol_attempts) != len(expected)
        ):
            status, reason = ConditionStatus.FALSE, "AttemptMissing"
        elif any(not item.valid for item in public.attempts):
            status, reason = ConditionStatus.FALSE, "AttemptInvalid"
        else:
            status, reason = ConditionStatus.TRUE, "AttemptsComplete"
    return _condition(ConditionType.ATTEMPTS_COMPLETE, status, reason)


def _attempts_isolated(
    design: CanaryProtocolDesignV1 | None, run: CanaryRunEvidenceV1 | None
) -> ProtocolConditionV1:
    attempts = () if run is None else tuple(a for p in run.pairs for a in p.attempts)
    if (
        design is None
        or design.isolation_sha256 is None
        or not attempts
        or any(
            item.isolation_instance_sha256 is None or item.isolation_sha256 is None
            for item in attempts
        )
    ):
        status, reason = ConditionStatus.UNKNOWN, "IsolationUnverified"
    else:
        instances = [item.isolation_instance_sha256 for item in attempts]
        profile_matches = all(
            item.isolation_sha256 == design.isolation_sha256 for item in attempts
        )
        instances_distinct = len(set(instances)) == len(instances)
        if not profile_matches:
            status, reason = ConditionStatus.FALSE, "IsolationProfileMismatch"
        elif not instances_distinct:
            status, reason = ConditionStatus.FALSE, "IsolationReuseDetected"
        else:
            status, reason = ConditionStatus.TRUE, "IsolationVerified"
    return _condition(ConditionType.ATTEMPTS_ISOLATED, status, reason)


def _canary_order(
    bundle: VerifiedEvidenceBundle, canary_id: str
) -> tuple[tuple[str, str, int], ...]:
    return tuple(item for item in bundle.report.run_order if item[0] == canary_id)


def _canary_order_is_adjacent(bundle: VerifiedEvidenceBundle, canary_id: str) -> bool:
    positions = [
        index
        for index, item in enumerate(bundle.report.run_order)
        if item[0] == canary_id
    ]
    return all(
        positions[index + 1] == positions[index] + 1
        for index in range(0, len(positions), 2)
        if index + 1 < len(positions)
    )


def _order_valid(
    bundle: VerifiedEvidenceBundle, canary_id: str, repetitions: int | None
) -> ProtocolConditionV1:
    order = _canary_order(bundle, canary_id)
    if repetitions is None or not order or any(
        side not in ("baseline", "candidate") for _, side, _ in order
    ):
        status, reason = ConditionStatus.UNKNOWN, "OrderUnknown"
    elif len(order) != repetitions * 2 or not _canary_order_is_adjacent(
        bundle, canary_id
    ):
        status, reason = ConditionStatus.FALSE, "PairLayoutInvalid"
    else:
        orientations: list[tuple[str, str]] = []
        valid_layout = True
        for index in range(0, len(order), 2):
            first, second = order[index : index + 2]
            repetition = index // 2 + 1
            if (
                first[2] != repetition
                or second[2] != repetition
                or {first[1], second[1]} != {"baseline", "candidate"}
            ):
                valid_layout = False
                break
            orientations.append((first[1], second[1]))
        if not valid_layout:
            status, reason = ConditionStatus.FALSE, "PairLayoutInvalid"
        else:
            ab = orientations.count(("baseline", "candidate"))
            ba = orientations.count(("candidate", "baseline"))
            alternates = all(
                orientations[index] != orientations[index - 1]
                for index in range(1, len(orientations))
            )
            if abs(ab - ba) > 1 or not alternates:
                status, reason = ConditionStatus.FALSE, "OrderImbalanced"
            else:
                status, reason = ConditionStatus.TRUE, "OrderVerified"
    return _condition(ConditionType.ORDER_VALID, status, reason)


def _attempts_by_slot(
    run: CanaryRunEvidenceV1,
) -> dict[tuple[str, int], AttemptProtocolContextV1]:
    return {
        (attempt.side, attempt.repetition): attempt
        for pair in run.pairs
        for attempt in pair.attempts
    }


def _slot_evidence_status(
    run: CanaryRunEvidenceV1, repetitions: int
) -> ConditionStatus:
    expected = {
        (side, repetition)
        for repetition in range(1, repetitions + 1)
        for side in ("baseline", "candidate")
    }
    attempts = tuple(item for pair in run.pairs for item in pair.attempts)
    actual = {(item.side, item.repetition) for item in attempts}
    pair_repetitions = {pair.repetition for pair in run.pairs}
    expected_repetitions = set(range(1, repetitions + 1))
    if actual <= expected and pair_repetitions <= expected_repetitions:
        if (
            actual != expected
            or pair_repetitions != expected_repetitions
            or len(attempts) != len(expected)
            or len(run.pairs) != repetitions
        ):
            return ConditionStatus.UNKNOWN
        return ConditionStatus.TRUE
    return ConditionStatus.FALSE


def _temporal_pair_valid(
    bundle: VerifiedEvidenceBundle,
    canary_id: str,
    design: CanaryProtocolDesignV1 | None,
    run: CanaryRunEvidenceV1 | None,
    repetitions: int | None,
) -> ProtocolConditionV1:
    if (
        design is None
        or run is None
        or repetitions is None
        or design.max_pair_gap_ms is None
    ):
        return _condition(
            ConditionType.TEMPORAL_PAIR_VALID,
            ConditionStatus.UNKNOWN,
            "TemporalEvidenceMissing",
        )
    slot_status = _slot_evidence_status(run, repetitions)
    if slot_status is ConditionStatus.UNKNOWN:
        return _condition(
            ConditionType.TEMPORAL_PAIR_VALID,
            ConditionStatus.UNKNOWN,
            "TemporalEvidenceMissing",
        )
    if slot_status is ConditionStatus.FALSE:
        return _condition(
            ConditionType.TEMPORAL_PAIR_VALID,
            ConditionStatus.FALSE,
            "PairLayoutInvalid",
        )
    attempts = _attempts_by_slot(run)
    order = _canary_order(bundle, canary_id)
    if len(order) != repetitions * 2 or not _canary_order_is_adjacent(
        bundle, canary_id
    ):
        return _condition(
            ConditionType.TEMPORAL_PAIR_VALID,
            ConditionStatus.FALSE,
            "PairLayoutInvalid",
        )
    gaps: list[int] = []
    for index in range(0, len(order), 2):
        if index + 1 >= len(order):
            return _condition(
                ConditionType.TEMPORAL_PAIR_VALID,
                ConditionStatus.FALSE,
                "PairLayoutInvalid",
            )
        first_slot, second_slot = order[index], order[index + 1]
        expected_repetition = index // 2 + 1
        if (
            first_slot[2] != expected_repetition
            or second_slot[2] != expected_repetition
            or {first_slot[1], second_slot[1]} != {"baseline", "candidate"}
        ):
            return _condition(
                ConditionType.TEMPORAL_PAIR_VALID,
                ConditionStatus.FALSE,
                "PairLayoutInvalid",
            )
        first = attempts.get((first_slot[1], first_slot[2]))
        second = attempts.get((second_slot[1], second_slot[2]))
        if first is None or second is None:
            return _condition(
                ConditionType.TEMPORAL_PAIR_VALID,
                ConditionStatus.FALSE,
                "PairLayoutInvalid",
            )
        finish = first.finished_offset_ms
        start = second.started_offset_ms
        if finish is None or start is None:
            return _condition(
                ConditionType.TEMPORAL_PAIR_VALID,
                ConditionStatus.UNKNOWN,
                "TemporalEvidenceMissing",
            )
        gap = start - finish
        if gap < 0:
            return _condition(
                ConditionType.TEMPORAL_PAIR_VALID,
                ConditionStatus.FALSE,
                "PairLayoutInvalid",
            )
        gaps.append(gap)
    exceeded = any(gap > design.max_pair_gap_ms for gap in gaps)
    return _condition(
        ConditionType.TEMPORAL_PAIR_VALID,
        ConditionStatus.FALSE if exceeded else ConditionStatus.TRUE,
        "TemporalGapExceeded" if exceeded else "TemporalPairVerified",
    )


def _all_equal(values: Iterable[str | None]) -> ConditionStatus:
    items = tuple(values)
    known = {item for item in items if item is not None}
    if len(known) > 1:
        return ConditionStatus.FALSE
    if not items or any(item is None for item in items):
        return ConditionStatus.UNKNOWN
    return ConditionStatus.TRUE


def _controlled_dimension_status(
    dimension: MaterialDimension,
    bundle: VerifiedEvidenceBundle,
    evidence: ProtocolEvidenceV1,
    design: CanaryProtocolDesignV1,
    run: CanaryRunEvidenceV1,
    repetitions: int,
) -> ConditionStatus:
    attempts = tuple(attempt for pair in run.pairs for attempt in pair.attempts)
    if dimension is MaterialDimension.AGENT_BINARY:
        return (
            ConditionStatus.TRUE
            if (
                evidence.baseline_state.agent_name,
                evidence.baseline_state.version,
                evidence.baseline_state.binary_sha256,
            )
            == (
                evidence.candidate_state.agent_name,
                evidence.candidate_state.version,
                evidence.candidate_state.binary_sha256,
            )
            else ConditionStatus.FALSE
        )
    if dimension is MaterialDimension.AGENT_SUPPORT:
        states = (evidence.baseline_state, evidence.candidate_state)
        if any(
            item.agent_name.lower() in {"codex", "gemini"}
            and item.support_sha256 is None
            for item in states
        ):
            return ConditionStatus.UNKNOWN
        return (
            ConditionStatus.TRUE
            if evidence.baseline_state.support_sha256
            == evidence.candidate_state.support_sha256
            else ConditionStatus.FALSE
        )
    if dimension is MaterialDimension.MODEL_DECLARATION:
        return (
            ConditionStatus.TRUE
            if evidence.baseline_state.model == evidence.candidate_state.model
            else ConditionStatus.FALSE
        )
    if dimension is MaterialDimension.PROJECT_CONFIG:
        return (
            ConditionStatus.TRUE
            if design is not None
            and evidence.protocol_design.config_sha256 == bundle.manifest.config_sha256
            else ConditionStatus.FALSE
        )
    if dimension is MaterialDimension.PREPARED_TARGET:
        public = _public_execution(bundle, run.canary_id)
        if public is None or not public.prepared_image_digest or run.prepared_target_sha256 is None:
            return ConditionStatus.UNKNOWN
        return (
            ConditionStatus.TRUE
            if run.prepared_target_sha256
            == public.prepared_image_digest.removeprefix("sha256:")
            else ConditionStatus.FALSE
        )
    profile_fields = {
        MaterialDimension.RUNTIME_PROFILE: "runtime_sha256",
        MaterialDimension.PREPARATION_PROFILE: "preparation_sha256",
        MaterialDimension.ISOLATION_PROFILE: "isolation_sha256",
        MaterialDimension.RESOURCE_POLICY: "resource_sha256",
    }
    field = profile_fields[dimension]
    expected = getattr(design, field)
    if expected is None or _slot_evidence_status(run, repetitions) is not ConditionStatus.TRUE:
        return ConditionStatus.UNKNOWN
    status = _all_equal((expected, *(getattr(item, field) for item in attempts)))
    return status


def _material_dimensions_controlled(
    bundle: VerifiedEvidenceBundle,
    evidence: ProtocolEvidenceV1,
    design: CanaryProtocolDesignV1 | None,
    run: CanaryRunEvidenceV1 | None,
    repetitions: int | None,
) -> ProtocolConditionV1:
    if (
        design is None
        or run is None
        or repetitions is None
        or design.material_dimensions is None
        or _slot_evidence_status(run, repetitions) is not ConditionStatus.TRUE
    ):
        status, reason = ConditionStatus.UNKNOWN, "RequiredDimensionOpaque"
    else:
        changeset = derive_changeset(evidence.baseline_state, evidence.candidate_state)
        statuses = tuple(
            ConditionStatus.TRUE
            if dimension in changeset.changed_dimensions
            else _controlled_dimension_status(
                dimension, bundle, evidence, design, run, repetitions
            )
            for dimension in design.material_dimensions
        )
        if ConditionStatus.FALSE in statuses:
            status, reason = ConditionStatus.FALSE, "UnexpectedMaterialChange"
        elif ConditionStatus.UNKNOWN in statuses:
            status, reason = ConditionStatus.UNKNOWN, "RequiredDimensionOpaque"
        else:
            status, reason = ConditionStatus.TRUE, "MaterialControlsVerified"
    return _condition(ConditionType.MATERIAL_DIMENSIONS_CONTROLLED, status, reason)


def evaluate_canary_conditions(
    bundle: VerifiedEvidenceBundle,
    evidence: ProtocolEvidenceV1,
    canary_id: str,
) -> tuple[ProtocolConditionV1, ...]:
    """Evaluate one canary's gates in canonical protocol order."""

    design = _design_canary(evidence, canary_id)
    run = _run_canary(evidence, canary_id)
    public = _public_execution(bundle, canary_id)
    repetitions = evidence.protocol_design.repetitions if design is not None else None
    return (
        _preparation_equivalent(bundle, design, run, public, repetitions),
        _baseline_stable(public, repetitions),
        _attempts_complete(public, run, repetitions),
        _attempts_isolated(design, run),
        _order_valid(bundle, canary_id, repetitions),
        _temporal_pair_valid(bundle, canary_id, design, run, repetitions),
        _material_dimensions_controlled(bundle, evidence, design, run, repetitions),
    )
