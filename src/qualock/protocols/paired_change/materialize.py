"""Bind persisted paired-change run evidence to a verified public bundle."""

from __future__ import annotations

from qualock.evidence.bundle_models import PublicModelPin, VerifiedEvidenceBundle

from .models import AgentDependencyStateV1, PairedChangeRunV1, ProtocolEvidenceV1


class ProtocolMaterializationError(ValueError):
    """Raised when a run sidecar cannot be bound to public bundle evidence."""

    def __init__(self, label: str) -> None:
        super().__init__(f"protocol evidence mismatch: {label}")
        self.label = label


def _require(value: bool, label: str) -> None:
    if not value:
        raise ProtocolMaterializationError(label)


def _identity_matches(
    state: AgentDependencyStateV1,
    *,
    name: str,
    version: str,
    binary_sha256: str,
    support_sha256: str | None,
    model: PublicModelPin,
) -> bool:
    return (
        state.agent_name == name
        and state.version == version
        and state.binary_sha256 == binary_sha256
        and state.support_sha256 == support_sha256
        and state.model.model_dump(mode="json") == model.model_dump(mode="json")
    )


def materialize_protocol_evidence(
    run: PairedChangeRunV1, bundle: VerifiedEvidenceBundle
) -> ProtocolEvidenceV1:
    """Validate every shared identity and return a manifest-bound overlay."""

    manifest = bundle.manifest
    _require(run.qualification_id == manifest.qualification_id, "qualification_id")
    _require(
        _identity_matches(
            run.baseline_state,
            name=manifest.baseline_identity.name,
            version=manifest.baseline_identity.version,
            binary_sha256=manifest.baseline_identity.binary_sha256,
            support_sha256=manifest.baseline_identity.support_sha256,
            model=manifest.model,
        ),
        "baseline_identity",
    )
    _require(
        _identity_matches(
            run.candidate_state,
            name=manifest.candidate_identity.name,
            version=manifest.candidate_identity.version,
            binary_sha256=manifest.candidate_identity.binary_sha256,
            support_sha256=manifest.candidate_identity.support_sha256,
            model=manifest.model,
        ),
        "candidate_identity",
    )
    _require(run.protocol_design.suite_sha256 == manifest.suite_sha256, "suite_sha256")
    _require(run.protocol_design.config_sha256 == manifest.config_sha256, "config_sha256")

    public_canaries = {item.canary_id: item for item in bundle.canaries.canaries}
    design_canaries = {
        item.canary_id: item for item in run.protocol_design.canaries
    }
    _require(set(design_canaries) == set(public_canaries), "canaries")
    for canary_id, public_canary in public_canaries.items():
        _require(
            design_canaries[canary_id].canary_fingerprint_sha256
            == public_canary.canary_fingerprint_sha256,
            "canary_fingerprint_sha256",
        )
        _require(
            public_canary.repetitions == run.protocol_design.repetitions,
            "repetitions",
        )

    run_canaries = {item.canary_id: item for item in run.canaries}
    public_executions = {item.canary_id: item for item in bundle.report.executions}
    expected_run_canaries = {
        canary_id for canary_id, execution in public_executions.items() if execution.attempts
    }
    _require(set(run_canaries) == expected_run_canaries, "attempt_layout")

    observed_order: list[tuple[str, str, int]] = []
    for canary_id, run_canary in run_canaries.items():
        _require(
            run_canary.canary_fingerprint_sha256
            == public_canaries[canary_id].canary_fingerprint_sha256,
            "canary_fingerprint_sha256",
        )
        public_attempts = {
            (item.side, item.repetition): item.events_sha256
            for item in public_executions[canary_id].attempts
        }
        run_attempts: dict[tuple[str, int], str] = {}
        expected_repetitions = set(range(1, run.protocol_design.repetitions + 1))
        _require(
            {pair.repetition for pair in run_canary.pairs} == expected_repetitions,
            "repetitions",
        )
        for pair in run_canary.pairs:
            for attempt in pair.attempts:
                key = (attempt.side, attempt.repetition)
                _require(key not in run_attempts, "attempt_layout")
                run_attempts[key] = attempt.events_sha256
                observed_order.append((canary_id, attempt.side, attempt.repetition))
        _require(run_attempts == public_attempts, "events_sha256")

    _require(tuple(observed_order) == bundle.report.run_order, "run_order")
    return ProtocolEvidenceV1.model_validate(
        {
            **run.model_dump(mode="json"),
            "evidence_manifest_sha256": bundle.manifest_sha256,
        }
    )
