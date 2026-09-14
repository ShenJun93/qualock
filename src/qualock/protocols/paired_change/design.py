from collections.abc import Mapping, Sequence
from typing import Literal

from qualock.agents.base import AgentBinary
from qualock.agents.support_integrity import agent_support_fingerprint
from qualock.baseline.models import ModelPin
from qualock.canary.models import CanarySpec
from qualock.project import canary_fingerprint
from qualock.run.models import AttemptControlProfiles

from .fingerprint import digest_model, digest_value
from .models import (
    AgentDependencyStateV1,
    CanaryProtocolDesignV1,
    ChangeSetV1,
    ClaimClass,
    ConditionType,
    MaterialDimension,
    ModelDeclarationV1,
    ProtocolDesignV1,
)

PROTOCOL_ID: Literal["paired-change/v1"] = "paired-change/v1"
ORDER_POLICY: Literal["alternating-v1"] = "alternating-v1"
LIFECYCLE: Literal["FRESH"] = "FRESH"

def build_agent_dependency_state(
    binary: AgentBinary,
    model: ModelPin,
) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name=binary.name,
        version=binary.version,
        binary_sha256=binary.sha256,
        support_sha256=agent_support_fingerprint(binary),
        model=ModelDeclarationV1(
            id=model.id,
            snapshot=model.snapshot,
            reasoning_effort=model.reasoning_effort,
        ),
    )


def derive_changeset(
    baseline: AgentDependencyStateV1,
    candidate: AgentDependencyStateV1,
) -> ChangeSetV1:
    changed_dimensions: list[MaterialDimension] = []
    if (
        baseline.agent_name != candidate.agent_name
        or baseline.version != candidate.version
        or baseline.binary_sha256 != candidate.binary_sha256
    ):
        changed_dimensions.append(MaterialDimension.AGENT_BINARY)
    if baseline.support_sha256 != candidate.support_sha256:
        changed_dimensions.append(MaterialDimension.AGENT_SUPPORT)
    if baseline.model != candidate.model:
        changed_dimensions.append(MaterialDimension.MODEL_DECLARATION)

    return ChangeSetV1(
        baseline_state_sha256=digest_model(baseline),
        candidate_state_sha256=digest_model(candidate),
        changed_dimensions=tuple(changed_dimensions),
    )

def protocol_descriptor() -> dict[str, object]:
    return {
        "protocol_id": PROTOCOL_ID,
        "required_conditions": [item.value for item in ConditionType],
        "order_policy": ORDER_POLICY,
        "lifecycle": LIFECYCLE,
        "claim_classes": [item.value for item in ClaimClass],
    }


PROTOCOL_DIGEST = digest_value(protocol_descriptor())


def build_protocol_design(
    *,
    suite_sha256: str,
    config_sha256: str,
    repetitions: int,
    canaries: Sequence[CanarySpec],
    control_profiles: Mapping[str, AttemptControlProfiles | None] | None = None,
) -> ProtocolDesignV1:
    profiles_by_canary = control_profiles or {}
    bound_canaries: list[CanaryProtocolDesignV1] = []

    for canary in sorted(canaries, key=lambda item: item.id):
        declaration = canary.paired_change
        profiles = profiles_by_canary.get(canary.id)
        bound_canaries.append(
            CanaryProtocolDesignV1(
                canary_id=canary.id,
                canary_fingerprint_sha256=canary_fingerprint(canary),
                material_dimensions=(
                    None
                    if declaration is None
                    else tuple(
                        MaterialDimension(value)
                        for value in declaration.material_dimensions
                    )
                ),
                max_pair_gap_ms=(
                    None if declaration is None else declaration.max_pair_gap_ms
                ),
                preparation_sha256=(
                    None if profiles is None else profiles.preparation_sha256
                ),
                isolation_sha256=(
                    None if profiles is None else profiles.isolation_sha256
                ),
                resource_sha256=(
                    None if profiles is None else profiles.resource_sha256
                ),
                runtime_sha256=(
                    None if profiles is None else profiles.runtime_sha256
                ),
            )
        )

    return ProtocolDesignV1(
        schema_version=1,
        protocol_id=PROTOCOL_ID,
        protocol_digest=PROTOCOL_DIGEST,
        suite_sha256=suite_sha256,
        config_sha256=config_sha256,
        repetitions=repetitions,
        order_policy=ORDER_POLICY,
        lifecycle=LIFECYCLE,
        canaries=tuple(bound_canaries),
    )
