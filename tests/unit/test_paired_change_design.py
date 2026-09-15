from pathlib import Path

from qualock.canary.models import CanarySpec
from qualock.protocols.paired_change.design import (
    LIFECYCLE,
    ORDER_POLICY,
    PROTOCOL_DIGEST,
    PROTOCOL_ID,
    build_protocol_design,
    protocol_descriptor,
)
from qualock.protocols.paired_change.fingerprint import digest_model, digest_value
from qualock.protocols.paired_change.models import (
    ClaimClass,
    ConditionType,
    MaterialDimension,
)
from qualock.run.models import AttemptControlProfiles


def make_canary(
    root: Path,
    canary_id: str,
    *,
    paired_change: dict[str, object] | None,
) -> CanarySpec:
    root.mkdir()
    patch = root / "grader.patch"
    patch.write_text("same grader", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": canary_id.title(),
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(patch), "command": ["pytest -q"]},
            "constraints": {"protected_paths": ["tests/**"]},
            "critical": True,
            "paired_change": paired_change,
        }
    )


def profiles(seed: str) -> AttemptControlProfiles:
    return AttemptControlProfiles(
        preparation_sha256=seed * 64,
        isolation_sha256=str((int(seed, 16) + 1) % 16) * 64,
        resource_sha256=str((int(seed, 16) + 2) % 16) * 64,
        runtime_sha256=str((int(seed, 16) + 3) % 16) * 64,
    )


def test_protocol_descriptor_binds_exact_v1_semantics() -> None:
    descriptor = protocol_descriptor()

    assert descriptor == {
        "protocol_id": "paired-change/v1",
        "required_conditions": [
            "EvidenceBound",
            "DesignFrozen",
            "StateBound",
            "PreparationEquivalent",
            "BaselineStable",
            "AttemptsComplete",
            "AttemptsIsolated",
            "OrderValid",
            "TemporalPairValid",
            "MaterialDimensionsControlled",
        ],
        "order_policy": "alternating-v1",
        "lifecycle": "FRESH",
        "claim_classes": [
            "NO_REGRESSION_OBSERVED",
            "ATTRIBUTABLE_CHANGESET",
            "UNRESOLVED",
        ],
    }
    assert [item.value for item in ConditionType] == descriptor["required_conditions"]
    assert [item.value for item in ClaimClass] == descriptor["claim_classes"]
    assert PROTOCOL_ID == descriptor["protocol_id"]
    assert ORDER_POLICY == descriptor["order_policy"]
    assert LIFECYCLE == descriptor["lifecycle"]
    assert PROTOCOL_DIGEST == digest_value(descriptor)


def test_build_protocol_design_binds_canary_declaration_and_profiles(tmp_path: Path) -> None:
    canary = make_canary(
        tmp_path / "sample",
        "sample",
        paired_change={
            "material_dimensions": [
                "AGENT_BINARY",
                "AGENT_SUPPORT",
                "PREPARATION_PROFILE",
            ],
            "max_pair_gap_ms": 5000,
        },
    )
    control = profiles("1")

    design = build_protocol_design(
        suite_sha256="a" * 64,
        config_sha256="b" * 64,
        repetitions=3,
        canaries=[canary],
        control_profiles={"sample": control},
    )

    assert design.schema_version == 1
    assert design.protocol_id == PROTOCOL_ID
    assert design.protocol_digest == PROTOCOL_DIGEST
    assert design.suite_sha256 == "a" * 64
    assert design.config_sha256 == "b" * 64
    assert design.repetitions == 3
    assert design.order_policy == ORDER_POLICY
    assert design.lifecycle == LIFECYCLE
    assert len(design.canaries) == 1

    bound = design.canaries[0]
    assert bound.canary_id == "sample"
    assert bound.material_dimensions == (
        MaterialDimension.AGENT_BINARY,
        MaterialDimension.AGENT_SUPPORT,
        MaterialDimension.PREPARATION_PROFILE,
    )
    assert bound.max_pair_gap_ms == 5000
    assert bound.preparation_sha256 == control.preparation_sha256
    assert bound.isolation_sha256 == control.isolation_sha256
    assert bound.resource_sha256 == control.resource_sha256
    assert bound.runtime_sha256 == control.runtime_sha256


def test_build_protocol_design_is_canonical_across_input_order(tmp_path: Path) -> None:
    alpha = make_canary(
        tmp_path / "alpha",
        "alpha",
        paired_change={
            "material_dimensions": ["AGENT_BINARY"],
            "max_pair_gap_ms": 1000,
        },
    )
    beta = make_canary(
        tmp_path / "beta",
        "beta",
        paired_change={
            "material_dimensions": ["AGENT_SUPPORT"],
            "max_pair_gap_ms": 2000,
        },
    )
    controls = {"beta": profiles("5"), "alpha": profiles("1")}

    left = build_protocol_design(
        suite_sha256="a" * 64,
        config_sha256="b" * 64,
        repetitions=2,
       canaries=[beta, alpha],
        control_profiles=controls,
    )
    right = build_protocol_design(
        suite_sha256="a" * 64,
        config_sha256="b" * 64,
        repetitions=2,
        canaries=[alpha, beta],
        control_profiles={"alpha": controls["alpha"], "beta": controls["beta"]},
    )

    assert [item.canary_id for item in left.canaries] == ["alpha", "beta"]
    assert left == right
    assert digest_model(left) == digest_model(right)


def test_legacy_canary_keeps_material_declaration_unavailable(tmp_path: Path) -> None:
    canary = make_canary(tmp_path / "legacy", "legacy", paired_change=None)

    design = build_protocol_design(
        suite_sha256="a" * 64,
        config_sha256="b" * 64,
        repetitions=1,
        canaries=[canary],
        control_profiles={},
    )

    bound = design.canaries[0]
    assert bound.material_dimensions is None
    assert bound.max_pair_gap_ms is None
    assert bound.preparation_sha256 is None
    assert bound.isolation_sha256 is None
    assert bound.resource_sha256 is None
    assert bound.runtime_sha256 is None
