from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.protocols.first_bad.fingerprint import digest_catalog, digest_chain_evidence
from qualock.protocols.first_bad.io import (
    EdgePackageSnapshot,
    FirstBadPackageSnapshot,
    FirstBadVerificationError,
    FirstBadVerificationReason,
)
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadChainEvidenceV1,
    FirstBadClaimClass,
    FirstBadConditionType,
    FirstBadEdgeEvidenceV1,
)
from qualock.protocols.first_bad.verify import verify_first_bad, verify_first_bad_snapshot
from qualock.protocols.paired_change.io import (
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
)
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    CanaryClaimV1,
    ClaimClass,
    ClaimReceiptV1,
    ConditionStatus,
    ModelDeclarationV1,
)
from qualock.protocols.paired_change.verify import verify_paired_change_payloads

FIXTURES = Path(__file__).parents[1] / "fixtures" / "paired_change_v1"
SHA0 = "0" * 64


def _fixture_snapshot(name: str) -> tuple[EdgePackageSnapshot, object]:
    root = FIXTURES / name
    bundle_files = MappingProxyType(
        {path.name: path.read_bytes() for path in (root / "bundle").iterdir() if path.is_file()}
    )
    protocol_bytes = (root / "protocol" / "protocol-evidence.json").read_bytes()
    receipt_path = root / "protocol" / "claim-receipt.json"
    stored = receipt_path.read_bytes() if receipt_path.exists() else None
    edge_snapshot = EdgePackageSnapshot(
        bundle_files=bundle_files,
        protocol_evidence_bytes=protocol_bytes,
        claim_receipt_bytes=stored,
    )
    verified = verify_paired_change_payloads(bundle_files, protocol_bytes, stored)
    return edge_snapshot, verified


def _finish_evidence(evidence: FirstBadChainEvidenceV1) -> FirstBadChainEvidenceV1:
    evidence = evidence.model_copy(update={"catalog_sha256": digest_catalog(evidence.catalog_versions)})
    return evidence.model_copy(update={"chain_sha256": digest_chain_evidence(evidence)})


def _single_fixture_snapshot(
    name: str,
    *,
    catalog_versions: tuple[str, ...] | None = None,
    stored_receipt: object | None = None,
) -> tuple[FirstBadPackageSnapshot, object]:
    edge_snapshot, verified = _fixture_snapshot(name)
    baseline = verified.evidence.baseline_state
    candidate = verified.evidence.candidate_state
    catalog = catalog_versions or (baseline.version, candidate.version)
    edge = FirstBadEdgeEvidenceV1(
        index=0,
        baseline_version=baseline.version,
        candidate_version=candidate.version,
        baseline_runtime_identity=baseline,
        candidate_runtime_identity=candidate,
        bundle_manifest_sha256=verified.bundle.manifest_sha256,
        protocol_evidence_sha256=verified.protocol_evidence_sha256,
    )
    evidence = FirstBadChainEvidenceV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        agent_name=baseline.agent_name,
        baseline_version=catalog[0],
        baseline_runtime_identity=baseline,
        upper_version=catalog[-1],
        catalog_versions=catalog,
        catalog_sha256=SHA0,
        suite_sha256=verified.bundle.manifest.suite_sha256,
        config_sha256=verified.bundle.manifest.config_sha256,
        model_pin=baseline.model,
        protocol_design_sha256=verified.evidence.protocol_design_sha256,
        edges=(edge,),
        chain_sha256=SHA0,
    )
    evidence = _finish_evidence(evidence)
    snapshot = FirstBadPackageSnapshot(
        evidence=evidence,
        evidence_bytes=canonical_json_file_bytes(evidence.model_dump(mode="json")),
        stored_receipt=stored_receipt,  # type: ignore[arg-type]
        edges=(edge_snapshot,),
    )
    return snapshot, verified


def _receipt_with_claim(receipt: ClaimReceiptV1, claim: ClaimClass) -> ClaimReceiptV1:
    claims = tuple(
        CanaryClaimV1(canary_id=item.canary_id, conditions=item.conditions, claim=claim)
        for item in receipt.canary_claims
    )
    return receipt.model_copy(update={"canary_claims": claims})


def _state_from(
    state: AgentDependencyStateV1,
    *,
    version: str | None = None,
    binary_label: str | None = None,
    model: ModelDeclarationV1 | None = None,
) -> AgentDependencyStateV1:
    updates: dict[str, object] = {}
    if version is not None:
        updates["version"] = version
    if binary_label is not None:
        updates["binary_sha256"] = hashlib.sha256(binary_label.encode()).hexdigest()
    if model is not None:
        updates["model"] = model
    return state.model_copy(update=updates)


def _fake_verified(
    template: object,
    *,
    baseline: AgentDependencyStateV1,
    candidate: AgentDependencyStateV1,
    claim: ClaimClass,
    manifest_sha256: str | None = None,
    protocol_sha256: str | None = None,
    suite_sha256: str | None = None,
    config_sha256: str | None = None,
    design_sha256: str | None = None,
) -> object:
    t = template
    return SimpleNamespace(
        bundle=SimpleNamespace(
            manifest=SimpleNamespace(
                suite_sha256=suite_sha256 or t.bundle.manifest.suite_sha256,
                config_sha256=config_sha256 or t.bundle.manifest.config_sha256,
            ),
            manifest_sha256=manifest_sha256 or t.bundle.manifest_sha256,
        ),
        evidence=SimpleNamespace(
            baseline_state=baseline,
            candidate_state=candidate,
            protocol_design_sha256=design_sha256 or t.evidence.protocol_design_sha256,
        ),
        receipt=_receipt_with_claim(t.receipt, claim),
        protocol_evidence_sha256=protocol_sha256 or t.protocol_evidence_sha256,
    )


def _fake_chain(
    classifications: tuple[ClaimClass, ...],
    *,
    full_catalog_extra: int = 0,
) -> tuple[FirstBadPackageSnapshot, tuple[object, ...]]:
    edge_snapshot, template = _fixture_snapshot("no-regression-clean")
    first = template.evidence.baseline_state
    states = [first]
    for index in range(len(classifications) + full_catalog_extra):
        states.append(
            _state_from(
                states[-1],
                version=f"0.{151 + index}.0",
                binary_label=f"binary-{index + 1}",
            )
        )
    catalog = tuple(item.version for item in states)
    verified_edges: list[object] = []
    edge_evidence: list[FirstBadEdgeEvidenceV1] = []
    snapshots: list[EdgePackageSnapshot] = []
    for index, claim in enumerate(classifications):
        verified = _fake_verified(
            template,
            baseline=states[index],
            candidate=states[index + 1],
            claim=claim,
            manifest_sha256=hashlib.sha256(f"manifest-{index}".encode()).hexdigest(),
            protocol_sha256=hashlib.sha256(f"protocol-{index}".encode()).hexdigest(),
        )
        verified_edges.append(verified)
        edge_evidence.append(
            FirstBadEdgeEvidenceV1(
                index=index,
                baseline_version=states[index].version,
                candidate_version=states[index + 1].version,
                baseline_runtime_identity=states[index],
                candidate_runtime_identity=states[index + 1],
                bundle_manifest_sha256=verified.bundle.manifest_sha256,
                protocol_evidence_sha256=verified.protocol_evidence_sha256,
            )
        )
        snapshots.append(edge_snapshot)
    evidence = FirstBadChainEvidenceV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        agent_name=first.agent_name,
        baseline_version=catalog[0],
        baseline_runtime_identity=first,
        upper_version=catalog[-1],
        catalog_versions=catalog,
        catalog_sha256=SHA0,
        suite_sha256=template.bundle.manifest.suite_sha256,
        config_sha256=template.bundle.manifest.config_sha256,
        model_pin=first.model,
        protocol_design_sha256=template.evidence.protocol_design_sha256,
        edges=tuple(edge_evidence),
        chain_sha256=SHA0,
    )
    evidence = _finish_evidence(evidence)
    return (
        FirstBadPackageSnapshot(
            evidence=evidence,
            evidence_bytes=canonical_json_file_bytes(evidence.model_dump(mode="json")),
            stored_receipt=None,
            edges=tuple(snapshots),
        ),
        tuple(verified_edges),
    )


def _patch_children(monkeypatch: pytest.MonkeyPatch, verified_edges: tuple[object, ...]) -> list[int]:
    import qualock.protocols.first_bad.verify as verify_module

    calls: list[int] = []
    iterator = iter(enumerate(verified_edges))

    def fake_verify(*args: object, **kwargs: object) -> object:
        index, verified = next(iterator)
        calls.append(index)
        return verified

    monkeypatch.setattr(verify_module, "verify_paired_change_payloads", fake_verify)
    return calls


def _mutate_evidence(
    snapshot: FirstBadPackageSnapshot, **updates: object
) -> FirstBadPackageSnapshot:
    evidence = snapshot.evidence.model_copy(update=updates)
    evidence = _finish_evidence(evidence)
    return FirstBadPackageSnapshot(
        evidence=evidence,
        evidence_bytes=canonical_json_file_bytes(evidence.model_dump(mode="json")),
        stored_receipt=snapshot.stored_receipt,
        edges=snapshot.edges,
    )


def _assert_reason(snapshot: FirstBadPackageSnapshot, reason: FirstBadVerificationReason) -> None:
    with pytest.raises(FirstBadVerificationError) as exc_info:
        verify_first_bad_snapshot(snapshot)
    assert exc_info.value.reason is reason


def test_real_no_regression_child_yields_complete_no_bad_receipt() -> None:
    snapshot, _verified = _single_fixture_snapshot("no-regression-clean")

    receipt = verify_first_bad_snapshot(snapshot)

    assert receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert receipt.boundary_version is None
    assert receipt.boundary_edge_index is None
    assert [item.type for item in receipt.conditions] == list(FirstBadConditionType)
    assert [item.status for item in receipt.conditions[:9]] == [ConditionStatus.TRUE] * 9
    assert receipt.conditions[9].status is ConditionStatus.FALSE
    assert [item.reason for item in receipt.conditions] == [
        "catalog digest and declared range are bound",
        "first edge baseline matches the chain anchor",
        "carried edges form a contiguous catalog prefix",
        "catalog versions are strictly increasing stable releases",
        "suite identity is frozen across carried edges",
        "config identity is frozen across carried edges",
        "model and paired-change design are frozen across carried edges",
        "all carried edges passed structural paired-change verification",
        "all edges before any attributable boundary are verified no-regression",
        "complete range contains no attributable boundary",
    ]


def test_real_attributable_child_yields_first_bad_at_edge_zero() -> None:
    snapshot, _verified = _single_fixture_snapshot("attributable-clean")
    receipt = verify_first_bad_snapshot(snapshot)
    assert receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert receipt.boundary_version == "0.151.0"
    assert receipt.boundary_edge_index == 0
    assert all(item.status is ConditionStatus.TRUE for item in receipt.conditions)
    assert receipt.conditions[-1].reason == "terminal edge is an attributable boundary"


def test_real_unresolved_child_remains_causal_unresolved() -> None:
    snapshot, _verified = _single_fixture_snapshot("preparation-unknown")
    receipt = verify_first_bad_snapshot(snapshot)
    assert receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert receipt.edges[0].classification is EdgeClassification.UNRESOLVED
    assert receipt.conditions[7].status is ConditionStatus.TRUE
    assert receipt.conditions[8].status is ConditionStatus.UNKNOWN
    assert receipt.conditions[9].status is ConditionStatus.UNKNOWN
    assert receipt.conditions[9].reason == "carried prefix does not justify an attributable boundary"


def test_clean_truncated_prefix_is_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verified = _fake_chain((ClaimClass.NO_REGRESSION_OBSERVED,), full_catalog_extra=1)
    _patch_children(monkeypatch, verified)
    receipt = verify_first_bad_snapshot(snapshot)
    assert receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert receipt.conditions[8].status is ConditionStatus.TRUE
    assert receipt.conditions[9].status is ConditionStatus.UNKNOWN


def test_no_regression_prefix_then_attributable_is_earliest_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, verified = _fake_chain(
        (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.ATTRIBUTABLE_CHANGESET)
    )
    calls = _patch_children(monkeypatch, verified)
    receipt = verify_first_bad_snapshot(snapshot)
    assert calls == [0, 1]
    assert receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert receipt.boundary_version == "0.152.0"
    assert receipt.boundary_edge_index == 1
    assert all(item.status is ConditionStatus.TRUE for item in receipt.conditions)


def test_catalog_digest_mismatch_is_digest_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    evidence = snapshot.evidence.model_copy(update={"catalog_sha256": "f" * 64})
    evidence = evidence.model_copy(update={"chain_sha256": digest_chain_evidence(evidence)})
    _assert_reason(
        FirstBadPackageSnapshot(evidence, snapshot.evidence_bytes, None, snapshot.edges),
        FirstBadVerificationReason.DIGEST_MISMATCH,
    )


def test_chain_digest_mismatch_is_digest_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    bad = snapshot.evidence.model_copy(update={"chain_sha256": "f" * 64})
    _assert_reason(
        FirstBadPackageSnapshot(bad, snapshot.evidence_bytes, None, snapshot.edges),
        FirstBadVerificationReason.DIGEST_MISMATCH,
    )


@pytest.mark.parametrize(
    "catalog",
    [
        ("0.150.0", "0.150.0"),
        ("0.151.0", "0.150.0"),
        ("0.150.0", "0.151.0-beta"),
    ],
)
def test_invalid_catalog_order_or_stability_is_catalog_binding_error(
    catalog: tuple[str, ...],
) -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    evidence = snapshot.evidence.model_copy(
        update={
            "catalog_versions": catalog,
            "baseline_version": catalog[0],
            "upper_version": catalog[-1],
        }
    )
    evidence = _finish_evidence(evidence)
    _assert_reason(
        FirstBadPackageSnapshot(evidence, snapshot.evidence_bytes, None, snapshot.edges),
        FirstBadVerificationReason.CATALOG_BINDING_MISMATCH,
    )


def test_catalog_endpoint_disagreement_is_catalog_binding_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    bad = _mutate_evidence(snapshot, upper_version="0.199.0")
    _assert_reason(bad, FirstBadVerificationReason.CATALOG_BINDING_MISMATCH)


@pytest.mark.parametrize(
    "edge_update",
    [
        {"index": 1},
        {"baseline_version": "0.149.0"},
        {"candidate_version": "0.152.0"},
    ],
)
def test_edge_index_or_catalog_adjacency_mismatch_is_layout_error(
    edge_update: dict[str, object],
) -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    edge = snapshot.evidence.edges[0].model_copy(update=edge_update)
    bad = _mutate_evidence(snapshot, edges=(edge,))
    _assert_reason(bad, FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH)


@pytest.mark.parametrize("field", ["bundle", "protocol"])
def test_declared_child_digest_mismatch_is_digest_error(field: str) -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    edge = snapshot.evidence.edges[0]
    update = (
        {"bundle_manifest_sha256": "f" * 64}
        if field == "bundle"
        else {"protocol_evidence_sha256": "f" * 64}
    )
    bad = _mutate_evidence(snapshot, edges=(edge.model_copy(update=update),))
    _assert_reason(bad, FirstBadVerificationReason.DIGEST_MISMATCH)


def test_top_baseline_anchor_mismatch_is_baseline_binding_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    changed = _state_from(snapshot.evidence.baseline_runtime_identity, binary_label="wrong-anchor")
    bad = _mutate_evidence(snapshot, baseline_runtime_identity=changed)
    _assert_reason(bad, FirstBadVerificationReason.BASELINE_BINDING_MISMATCH)


def test_declared_candidate_identity_mismatch_is_edge_binding_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    edge = snapshot.evidence.edges[0]
    changed = _state_from(edge.candidate_runtime_identity, binary_label="wrong-candidate")
    bad = _mutate_evidence(snapshot, edges=(edge.model_copy(update={"candidate_runtime_identity": changed}),))
    _assert_reason(bad, FirstBadVerificationReason.EDGE_BINDING_MISMATCH)


def test_cross_edge_runtime_discontinuity_is_edge_binding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, verified = _fake_chain(
        (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.NO_REGRESSION_OBSERVED)
    )
    second = snapshot.evidence.edges[1]
    broken = _state_from(second.baseline_runtime_identity, binary_label="broken-link")
    edges = (snapshot.evidence.edges[0], second.model_copy(update={"baseline_runtime_identity": broken}))
    bad = _mutate_evidence(snapshot, edges=edges)
    verified = (
        verified[0],
        SimpleNamespace(
            **{
                **verified[1].__dict__,
                "evidence": SimpleNamespace(
                    baseline_state=broken,
                    candidate_state=verified[1].evidence.candidate_state,
                    protocol_design_sha256=verified[1].evidence.protocol_design_sha256,
                ),
            }
        ),
    )
    _patch_children(monkeypatch, verified)
    _assert_reason(bad, FirstBadVerificationReason.EDGE_BINDING_MISMATCH)


@pytest.mark.parametrize("kind", ["suite", "config", "model", "design", "agent"])
def test_frozen_experiment_drift_is_edge_binding_error(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    snapshot, verified = _fake_chain((ClaimClass.NO_REGRESSION_OBSERVED,))
    child = verified[0]
    if kind == "suite":
        child.bundle.manifest.suite_sha256 = "f" * 64
    elif kind == "config":
        child.bundle.manifest.config_sha256 = "f" * 64
    elif kind == "design":
        child.evidence.protocol_design_sha256 = "f" * 64
    elif kind == "model":
        altered_model = ModelDeclarationV1(id="other", snapshot=None, reasoning_effort="high")
        child.evidence.baseline_state = _state_from(child.evidence.baseline_state, model=altered_model)
        child.evidence.candidate_state = _state_from(child.evidence.candidate_state, model=altered_model)
        edge = snapshot.evidence.edges[0].model_copy(
            update={
                "baseline_runtime_identity": child.evidence.baseline_state,
                "candidate_runtime_identity": child.evidence.candidate_state,
            }
        )
        snapshot = _mutate_evidence(
            snapshot,
            baseline_runtime_identity=child.evidence.baseline_state,
            edges=(edge,),
        )
    else:
        child.evidence.baseline_state = child.evidence.baseline_state.model_copy(update={"agent_name": "claude"})
        child.evidence.candidate_state = child.evidence.candidate_state.model_copy(update={"agent_name": "claude"})
        edge = snapshot.evidence.edges[0].model_copy(
            update={
                "baseline_runtime_identity": child.evidence.baseline_state,
                "candidate_runtime_identity": child.evidence.candidate_state,
            }
        )
        snapshot = _mutate_evidence(
            snapshot,
            baseline_runtime_identity=child.evidence.baseline_state,
            edges=(edge,),
        )
    _patch_children(monkeypatch, verified)
    _assert_reason(snapshot, FirstBadVerificationReason.EDGE_BINDING_MISMATCH)


def test_child_verification_failure_is_normalized_with_fixed_edge_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualock.protocols.first_bad.verify as verify_module

    snapshot, _ = _single_fixture_snapshot("no-regression-clean")

    def fail_child(*args: object, **kwargs: object) -> object:
        raise PairedChangeVerificationError(
            PairedChangeVerificationReason.DIGEST_MISMATCH, "attacker-controlled-field"
        )

    monkeypatch.setattr(verify_module, "verify_paired_change_payloads", fail_child)
    with pytest.raises(FirstBadVerificationError) as exc_info:
        verify_first_bad_snapshot(snapshot)
    assert exc_info.value.reason is FirstBadVerificationReason.EDGE_BINDING_MISMATCH
    assert exc_info.value.field == "edge[000000]"
    assert "attacker-controlled-field" not in str(exc_info.value)


def test_edge_after_attributable_boundary_is_layout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, verified = _fake_chain(
        (ClaimClass.ATTRIBUTABLE_CHANGESET, ClaimClass.NO_REGRESSION_OBSERVED)
    )
    _patch_children(monkeypatch, verified)
    _assert_reason(snapshot, FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH)


def test_stored_receipt_is_only_cross_checked() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    clean = verify_first_bad_snapshot(snapshot)
    tampered = clean.model_copy(update={"claim": FirstBadClaimClass.UNRESOLVED})
    with_stored = FirstBadPackageSnapshot(
        snapshot.evidence,
        snapshot.evidence_bytes,
        tampered,
        snapshot.edges,
    )
    _assert_reason(with_stored, FirstBadVerificationReason.CHAIN_CLAIM_MISMATCH)


def test_verify_first_bad_reads_package_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualock.protocols.first_bad.verify as verify_module

    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    calls: list[Path] = []

    def fake_read(path: Path) -> FirstBadPackageSnapshot:
        calls.append(path)
        return snapshot

    monkeypatch.setattr(verify_module, "read_first_bad_package", fake_read)
    receipt = verify_first_bad(Path("/does/not/matter"))
    assert receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert calls == [Path("/does/not/matter")]


def test_snapshot_verification_is_offline_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import subprocess

    from qualock.agents.resolver import CodexResolver
    from qualock.run.docker import DockerRunner

    snapshot, _ = _single_fixture_snapshot("no-regression-clean")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("offline first-bad verification attempted external execution")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(DockerRunner, "daemon_ready", forbidden)
    monkeypatch.setattr(CodexResolver, "resolve", forbidden)

    receipt = verify_first_bad_snapshot(snapshot)
    assert receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND


def test_portable_chain_verifies_after_original_source_is_deleted(tmp_path: Path) -> None:
    import shutil

    source = tmp_path / "original-project-export"
    shutil.copytree(FIXTURES / "no-regression-clean", source)
    bundle_files = MappingProxyType(
        {path.name: path.read_bytes() for path in (source / "bundle").iterdir() if path.is_file()}
    )
    protocol_bytes = (source / "protocol" / "protocol-evidence.json").read_bytes()
    verified = verify_paired_change_payloads(bundle_files, protocol_bytes, None)
    baseline = verified.evidence.baseline_state
    candidate = verified.evidence.candidate_state
    edge = FirstBadEdgeEvidenceV1(
        index=0,
        baseline_version=baseline.version,
        candidate_version=candidate.version,
        baseline_runtime_identity=baseline,
        candidate_runtime_identity=candidate,
        bundle_manifest_sha256=verified.bundle.manifest_sha256,
        protocol_evidence_sha256=verified.protocol_evidence_sha256,
    )
    evidence = _finish_evidence(
        FirstBadChainEvidenceV1(
            schema_version=1,
            protocol_id="first-bad/v1",
            agent_name=baseline.agent_name,
            baseline_version=baseline.version,
            baseline_runtime_identity=baseline,
            upper_version=candidate.version,
            catalog_versions=(baseline.version, candidate.version),
            catalog_sha256=SHA0,
            suite_sha256=verified.bundle.manifest.suite_sha256,
            config_sha256=verified.bundle.manifest.config_sha256,
            model_pin=baseline.model,
            protocol_design_sha256=verified.evidence.protocol_design_sha256,
            edges=(edge,),
            chain_sha256=SHA0,
        )
    )

    chain = tmp_path / "portable-chain"
    edge_root = chain / "edges" / "000000"
    edge_root.mkdir(parents=True)
    (chain / "chain-evidence.json").write_bytes(
        canonical_json_file_bytes(evidence.model_dump(mode="json"))
    )
    shutil.copytree(source / "bundle", edge_root / "bundle")
    shutil.copytree(source / "protocol", edge_root / "protocol")
    shutil.rmtree(source)

    receipt = verify_first_bad(chain)
    assert receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": 2},
        {"protocol_id": "paired-change/v1"},
    ],
)
def test_snapshot_protocol_or_schema_mismatch_is_unsupported(
    update: dict[str, object],
) -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    invalid = snapshot.evidence.model_copy(update=update)
    bad = FirstBadPackageSnapshot(
        invalid,
        snapshot.evidence_bytes,
        None,
        snapshot.edges,
    )
    _assert_reason(bad, FirstBadVerificationReason.UNSUPPORTED_CHAIN_PROTOCOL)


def test_snapshot_edge_payload_count_mismatch_is_layout_error() -> None:
    snapshot, _ = _single_fixture_snapshot("no-regression-clean")
    bad = FirstBadPackageSnapshot(snapshot.evidence, snapshot.evidence_bytes, None, ())
    _assert_reason(bad, FirstBadVerificationReason.EDGE_LAYOUT_MISMATCH)
