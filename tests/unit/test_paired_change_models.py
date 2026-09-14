from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.agents.base import AgentBinary, AgentSupportBinary
from qualock.baseline.models import ModelPin
from qualock.protocols.paired_change.design import (
    build_agent_dependency_state,
    derive_changeset,
)
from qualock.protocols.paired_change.fingerprint import digest_model
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    AttemptProtocolContextV1,
    CanaryClaimV1,
    CanaryProtocolDesignV1,
    CanaryRunEvidenceV1,
    ClaimClass,
    ClaimReceiptV1,
    ConditionStatus,
    ConditionType,
    MaterialDimension,
    PairedChangeRunV1,
    PairEvidenceV1,
    ProtocolConditionV1,
    ProtocolDesignV1,
)

SHA = "a" * 64


def state(*, binary: str = SHA, support: str | None = None) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name="codex",
        version="0.150.0",
        binary_sha256=binary,
        support_sha256=support,
        model={"id": "gpt-5.6-sol", "snapshot": None, "reasoning_effort": "high"},
    )


def attempt(side: str, repetition: int = 1) -> AttemptProtocolContextV1:
    return AttemptProtocolContextV1(
        side=side,
        repetition=repetition,
        events_sha256="b" * 64,
        protocol_design_sha256="c" * 64,
        started_offset_ms=10,
        finished_offset_ms=20,
        isolation_instance_sha256="d" * 64,
        preparation_sha256="e" * 64,
        isolation_sha256="f" * 64,
        resource_sha256="1" * 64,
        runtime_sha256=None,
    )

def design() -> ProtocolDesignV1:
    return ProtocolDesignV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest="2" * 64,
        suite_sha256="3" * 64,
        config_sha256="4" * 64,
        repetitions=1,
        order_policy="alternating-v1",
        lifecycle="FRESH",
        canaries=(
            CanaryProtocolDesignV1(
                canary_id="sample",
                canary_fingerprint_sha256="5" * 64,
                material_dimensions=(MaterialDimension.AGENT_BINARY,),
                max_pair_gap_ms=5000,
                preparation_sha256="e" * 64,
                isolation_sha256="f" * 64,
                resource_sha256="1" * 64,
                runtime_sha256=None,
            ),
        ),
    )


def run() -> PairedChangeRunV1:
    protocol_design = design()
    return PairedChangeRunV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest=protocol_design.protocol_digest,
        protocol_design=protocol_design,
        protocol_design_sha256=digest_model(protocol_design),
        qualification_id="q1",
        baseline_state=state(),
        candidate_state=state(binary="6" * 64),
        changeset_sha256="7" * 64,
        canaries=(
            CanaryRunEvidenceV1(
                canary_id="sample",
                canary_fingerprint_sha256="5" * 64,
                prepared_target_sha256="8" * 64,
                pairs=(
                    PairEvidenceV1(
                        repetition=1,
                        attempts=(attempt("baseline"), attempt("candidate")),
                    ),
                ),
            ),
        ),
    )


def test_models_forbid_unknown_fields() -> None:
    payload = state().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        AgentDependencyStateV1.model_validate(payload)


def test_sha_fields_require_lowercase_64_hex() -> None:
    with pytest.raises(ValidationError):
        state(binary="A" * 64)


def test_closed_enums_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        ProtocolConditionV1(type="UnknownGate", status="TRUE", reason="x")
    with pytest.raises(ValidationError):
        ProtocolConditionV1(type="EvidenceBound", status="MAYBE", reason="x")


def test_pair_rejects_duplicate_or_mismatched_sides() -> None:
    with pytest.raises(ValidationError):
        PairEvidenceV1(
            repetition=1,
            attempts=(attempt("baseline"), attempt("baseline")),
        )
    with pytest.raises(ValidationError):
        PairEvidenceV1(
            repetition=1,
            attempts=(attempt("baseline", 2), attempt("candidate", 1)),
        )


def test_canary_evidence_rejects_duplicate_repetitions() -> None:
    pair = PairEvidenceV1(
        repetition=1,
        attempts=(attempt("baseline"), attempt("candidate")),
    )
    with pytest.raises(ValidationError):
        CanaryRunEvidenceV1(
            canary_id="sample",
            canary_fingerprint_sha256="5" * 64,
            prepared_target_sha256="8" * 64,
            pairs=(pair, pair),
        )


def test_design_and_run_reject_duplicate_canary_ids() -> None:
    item = design().canaries[0]
    payload = design().model_dump(mode="json")
    payload["canaries"] = [item.model_dump(mode="json"), item.model_dump(mode="json")]
    with pytest.raises(ValidationError):
        ProtocolDesignV1.model_validate(payload)

    evidence = run().canaries[0]
    run_payload = run().model_dump(mode="json")
    run_payload["canaries"] = [
        evidence.model_dump(mode="json"),
        evidence.model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError):
        PairedChangeRunV1.model_validate(run_payload)


def test_run_forbids_export_time_manifest_binding() -> None:
    payload = run().model_dump(mode="json")
    payload["evidence_manifest_sha256"] = "9" * 64
    with pytest.raises(ValidationError):
        PairedChangeRunV1.model_validate(payload)


def test_public_enums_are_exact_v1_sets() -> None:
    assert {item.value for item in ConditionStatus} == {"TRUE", "FALSE", "UNKNOWN"}
    assert {item.value for item in ClaimClass} == {
        "NO_REGRESSION_OBSERVED",
        "ATTRIBUTABLE_CHANGESET",
        "UNRESOLVED",
    }
    assert len(ConditionType) == 10
    assert len(MaterialDimension) == 9


def test_state_digest_is_canonical_and_deterministic() -> None:
    left = state(support="b" * 64)
    right = AgentDependencyStateV1.model_validate(left.model_dump(mode="json"))
    assert digest_model(left) == digest_model(right)


def test_build_state_binds_generic_support_fingerprint(tmp_path: Path) -> None:
    helper = tmp_path / "helper"
    helper.write_bytes(b"helper")
    binary = AgentBinary(
        "codex",
        "0.150.0",
        tmp_path / "codex",
        "a" * 64,
        support_binaries=(
            AgentSupportBinary("helper", helper, "b" * 64, "/opt/helper"),
        ),
    )
    model = ModelPin(id="gpt-5.6-sol", snapshot=None, reasoning_effort="high")
    built = build_agent_dependency_state(binary, model)
    assert built.agent_name == "codex"
    assert built.support_sha256 is not None
    assert built.model.reasoning_effort == "high"


def test_derive_changeset_is_structural_not_user_asserted() -> None:
    baseline = state(support="b" * 64)
    candidate = AgentDependencyStateV1.model_validate(
        {
            **baseline.model_dump(mode="json"),
            "version": "0.151.0",
            "binary_sha256": "c" * 64,
            "support_sha256": "d" * 64,
            "model": {"id": "gpt-5.6-sol-2", "snapshot": None, "reasoning_effort": "high"},
        }
    )
    changeset = derive_changeset(baseline, candidate)
    assert changeset.changed_dimensions == (
        MaterialDimension.AGENT_BINARY,
        MaterialDimension.AGENT_SUPPORT,
        MaterialDimension.MODEL_DECLARATION,
    )
    assert changeset.baseline_state_sha256 == digest_model(baseline)
    assert changeset.candidate_state_sha256 == digest_model(candidate)


def test_claim_receipt_rejects_duplicate_canary_ids() -> None:
    condition = ProtocolConditionV1(
        type=ConditionType.EVIDENCE_BOUND,
        status=ConditionStatus.TRUE,
        reason="EvidenceVerified",
    )
    claim = CanaryClaimV1(
        canary_id="sample",
        conditions=(condition,),
        claim=ClaimClass.NO_REGRESSION_OBSERVED,
    )
    with pytest.raises(ValidationError):
        ClaimReceiptV1(
            schema_version=1,
            protocol_id="paired-change/v1",
            protocol_digest="1" * 64,
            qualification_id="q1",
            evidence_manifest_sha256="2" * 64,
            protocol_evidence_sha256="3" * 64,
            baseline_state_sha256="4" * 64,
            candidate_state_sha256="5" * 64,
            changeset_sha256="6" * 64,
            qualification_conditions=(condition,),
            canary_claims=(claim, claim),
            verifier_name="qualock",
            verifier_version="0.1.0",
        )
