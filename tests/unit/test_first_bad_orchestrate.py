from __future__ import annotations

import os
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import qualock.protocols.first_bad.orchestrate as first_bad_orchestrate
from qualock.agents.support_integrity import agent_support_fingerprint
from qualock.baseline.io import read_baseline_lock, write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.commands import BaselineUnstableError, CommandError, execute_baseline, execute_check
from qualock.config.models import AgentConfig, QualockConfig
from qualock.evidence.export import ExportedEvidenceBundle, export_evidence_bundle
from qualock.history.loader import scan_results
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.protocols.first_bad.claims import (
    derive_chain_claim,
    derive_edge_classification,
    derive_edge_summary,
)
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadClaimClass,
    FirstBadEdgeEvidenceV1,
    FirstBadReceiptV1,
)
from qualock.protocols.first_bad.orchestrate import (
    FirstBadBaselineUnresolved,
    FirstBadExecutionDependencies,
    FirstBadPreflight,
    VerifiedFirstBadEdge,
    _agent_dependency_state_from_lock,
    _execute_edge,
    _snapshot_project_inputs,
    execute_first_bad,
    first_bad_preflight,
)
from qualock.protocols.first_bad.verify import verify_first_bad
from qualock.protocols.paired_change.models import (
    AgentDependencyStateV1,
    CanaryClaimV1,
    ClaimClass,
    ClaimReceiptV1,
    ConditionStatus,
    ConditionType,
    ModelDeclarationV1,
    ProtocolConditionV1,
)
from qualock.protocols.paired_change.verify import VerifiedPairedChangeV1
from qualock.qualification.models import QualificationResult, Verdict
from tests.integration.test_paired_change_e2e import DeterministicProtocolBackend
from tests.unit.test_evidence_export import FakeBackend, FakeResolver, _setup_project


class FakeCatalog:
    def __init__(self, versions: tuple[str, ...]) -> None:
        self.versions = versions
        self.calls = 0

    def stable_versions(self) -> tuple[str, ...]:
        self.calls += 1
        return self.versions


def baseline_lock(agent: str = "codex", version: str = "0.151.0") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version=version, binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


class FakeAgentConfig:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeConfig:
    def __init__(self, agent_name: str) -> None:
        self.agent = FakeAgentConfig(agent_name)


def patch_project_loading(monkeypatch: pytest.MonkeyPatch, *, agent_name: str = "codex") -> None:
    monkeypatch.setattr(
        first_bad_orchestrate, "load_project", lambda root: (FakeConfig(agent_name), [])
    )
    monkeypatch.setattr(first_bad_orchestrate, "suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr(first_bad_orchestrate, "config_fingerprint", lambda config: "config-now")
    monkeypatch.setattr(first_bad_orchestrate, "assert_suite_fresh", lambda *args: None)


def patch_lock(monkeypatch: pytest.MonkeyPatch, lock: BaselineLock) -> None:
    monkeypatch.setattr(first_bad_orchestrate, "read_baseline_lock", lambda path: lock)


# --- capability / preflight (RED step 1) -----------------------------------


def test_config_lock_agent_mismatch_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    patch_lock(monkeypatch, baseline_lock(agent="codex"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_antigravity_agent_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="antigravity")
    patch_lock(monkeypatch, baseline_lock(agent="antigravity", version="1.0.0"))
    catalog = FakeCatalog(("1.0.0", "1.1.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "antigravity@1.1.0", catalog=catalog)
    assert catalog.calls == 0


def test_unknown_agent_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="other")
    patch_lock(monkeypatch, baseline_lock(agent="other"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_cross_agent_upper_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    patch_lock(monkeypatch, baseline_lock(agent="claude", version="2.1.260"))
    catalog = FakeCatalog(("2.1.261", "2.1.263"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@2.1.261", catalog=catalog)
    assert catalog.calls == 0


@pytest.mark.parametrize("upper", ["codex@latest", "codex@0.153.0-beta.1", "codex@0.153"])
def test_non_exact_non_stable_upper_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, upper, catalog=catalog)
    assert catalog.calls == 0


def test_non_stable_baseline_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0-beta.1"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_upper_absent_from_catalog_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.151.0", "0.152.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


def test_baseline_absent_from_catalog_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


@pytest.mark.parametrize("upper", ["0.151.0", "0.150.0"])
def test_upper_must_be_newer_than_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, f"codex@{upper}", catalog=catalog)


def test_malformed_catalog_entry_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.151.0", "0.152.0", "not-a-version", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


@pytest.mark.parametrize(
    "versions",
    [
        ("0.151.0", "0.152.0", "0.152.0", "0.153.0"),
        ("0.151.0", "0.153.0", "0.152.0"),
    ],
    ids=["duplicate", "out-of-order"],
)
def test_frozen_catalog_rejects_non_unique_or_unordered_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions: tuple[str, ...]
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(versions)

    with pytest.raises(CommandError, match="strictly increasing"):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)

    assert catalog.calls == 1


def test_frozen_range_preserves_fetched_order_and_is_fetched_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    lock = baseline_lock(agent="codex", version="0.151.0")
    patch_lock(monkeypatch, lock)
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0", "0.154.0"))

    result = first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)

    assert catalog.calls == 1
    assert result.catalog == ("0.151.0", "0.152.0", "0.153.0")


@pytest.mark.parametrize("agent_name", ["codex", "claude", "gemini"])
def test_preflight_accepts_supported_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_name: str
) -> None:
    patch_project_loading(monkeypatch, agent_name=agent_name)
    lock = baseline_lock(agent=agent_name, version="1.0.0")
    patch_lock(monkeypatch, lock)
    catalog = FakeCatalog(("1.0.0", "1.1.0", "1.2.0"))

    result = first_bad_preflight(tmp_path, f"{agent_name}@1.2.0", catalog=catalog)

    assert result == FirstBadPreflight(
        agent_name=agent_name,
        baseline_lock=lock,
        upper_version="1.2.0",
        catalog=("1.0.0", "1.1.0", "1.2.0"),
        suite_sha256=lock.suite_sha256,
        config_sha256=lock.config_sha256,
        model_pin=lock.model,
    )


def test_stale_baseline_stops_before_catalog_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.baseline.io import BaselineStaleError

    patch_project_loading(monkeypatch, agent_name="codex")
    monkeypatch.setattr(
        first_bad_orchestrate,
        "assert_suite_fresh",
        lambda *args: (_ for _ in ()).throw(BaselineStaleError("suite changed")),
    )
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(BaselineStaleError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


# --- real-filesystem preflight: trusted baseline immutability --------------


def _write_config(path: Path, agent: str) -> None:
    config = QualockConfig(agent=AgentConfig(name=agent))
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")


def _write_real_project(
    root: Path, *, agent: str = "codex", baseline_version: str = "0.151.0"
) -> Path:
    qualock_dir = root / ".qualock"
    qualock_dir.mkdir(parents=True)
    _write_config(qualock_dir / "config.yaml", agent)
    (qualock_dir / "canaries").mkdir()

    config, canaries = load_project(root)
    lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version=baseline_version, binary_sha256="a" * 64),
        model=ModelPin(
            id=config.model.effective_model,
            snapshot=None,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={},
    )
    baseline_path = qualock_dir / "baseline.lock"
    write_baseline_lock(baseline_path, lock)
    return baseline_path


def test_real_preflight_leaves_baseline_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    baseline_path = _write_real_project(root, agent="codex", baseline_version="0.151.0")
    before = baseline_path.read_bytes()
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0"))

    result = first_bad_preflight(root, "codex@0.153.0", catalog=catalog)

    assert baseline_path.read_bytes() == before
    assert result.agent_name == "codex"
    assert result.upper_version == "0.153.0"
    assert result.catalog == ("0.151.0", "0.152.0", "0.153.0")


def test_real_preflight_permits_read_only_real_baseline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    baseline_path = _write_real_project(root, agent="codex", baseline_version="0.151.0")
    before = baseline_path.read_bytes()
    baseline_path.chmod(0o444)
    try:
        catalog = FakeCatalog(("0.151.0", "0.152.0"))
        result = first_bad_preflight(root, "codex@0.152.0", catalog=catalog)
        assert result.upper_version == "0.152.0"
    finally:
        baseline_path.chmod(0o644)
    assert baseline_path.read_bytes() == before


# --- workspace snapshot (RED step 3) ---------------------------------------


def _build_full_project(root: Path) -> Path:
    qualock_dir = root / ".qualock"
    qualock_dir.mkdir(parents=True)
    _write_config(qualock_dir / "config.yaml", "codex")

    canaries_dir = qualock_dir / "canaries"
    canaries_dir.mkdir()
    (canaries_dir / "demo.yaml").write_text("id: demo\n", encoding="utf-8")
    nested = canaries_dir / "patches"
    nested.mkdir()
    (nested / "demo.patch").write_text("--- a\n+++ b\n", encoding="utf-8")

    baseline_path = qualock_dir / "baseline.lock"
    baseline_path.write_text('{"trusted": true}', encoding="utf-8")

    (qualock_dir / "results").mkdir()
    (qualock_dir / "results" / "summary.json").write_text("{}", encoding="utf-8")
    (qualock_dir / "work").mkdir()
    (qualock_dir / "work" / "scratch.txt").write_text("scratch", encoding="utf-8")
    (qualock_dir / "cache").mkdir()
    (qualock_dir / "cache" / "blob.bin").write_bytes(b"\x00\x01")
    (qualock_dir / "sidecar.json").write_text("{}", encoding="utf-8")
    (root / "README.md").write_text("unrelated\n", encoding="utf-8")
    return baseline_path


def test_snapshot_copies_only_config_and_canaries(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)

    _snapshot_project_inputs(root, workspace)

    ws_qualock = workspace / ".qualock"
    assert (ws_qualock / "config.yaml").read_text(encoding="utf-8") == (
        root / ".qualock" / "config.yaml"
    ).read_text(encoding="utf-8")
    assert (ws_qualock / "canaries" / "demo.yaml").read_text(encoding="utf-8") == "id: demo\n"
    assert (
        ws_qualock / "canaries" / "patches" / "demo.patch"
    ).read_text(encoding="utf-8") == "--- a\n+++ b\n"

    assert not (ws_qualock / "baseline.lock").exists()
    assert not (ws_qualock / "results").exists()
    assert not (ws_qualock / "work").exists()
    assert not (ws_qualock / "cache").exists()
    assert not (ws_qualock / "sidecar.json").exists()
    assert not (workspace / "README.md").exists()


def test_snapshot_rejects_symlinked_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    real_config = root / ".qualock" / "config.yaml"
    outside_target = tmp_path / "outside-config.yaml"
    outside_target.write_text(real_config.read_text(encoding="utf-8"), encoding="utf-8")
    real_config.unlink()
    real_config.symlink_to(outside_target)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_rejects_symlinked_canary_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    canary_path = root / ".qualock" / "canaries" / "demo.yaml"
    outside_target = tmp_path / "outside-canary.yaml"
    outside_target.write_text(canary_path.read_text(encoding="utf-8"), encoding="utf-8")
    canary_path.unlink()
    canary_path.symlink_to(outside_target)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_rejects_symlinked_canaries_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    canaries_dir = root / ".qualock" / "canaries"
    outside_dir = tmp_path / "outside-canaries"
    shutil.copytree(canaries_dir, outside_dir)
    shutil.rmtree(canaries_dir)
    canaries_dir.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_leaves_real_baseline_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    baseline_path = _build_full_project(root)
    before = baseline_path.read_bytes()

    _snapshot_project_inputs(root, workspace)

    assert baseline_path.read_bytes() == before


def test_snapshot_permits_read_only_real_baseline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    baseline_path = _build_full_project(root)
    before = baseline_path.read_bytes()
    baseline_path.chmod(0o444)
    try:
        _snapshot_project_inputs(root, workspace)
    finally:
        baseline_path.chmod(0o644)
    assert baseline_path.read_bytes() == before


# --- Task 7: verified adjacent edge execution ------------------------------


def _state(
    *,
    agent_name: str = "codex",
    version: str = "0.151.0",
    binary_sha256: str = "a" * 64,
    support_sha256: str | None = None,
    model_id: str = "gpt-5",
    snapshot: str | None = None,
    reasoning_effort: str = "medium",
) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name=agent_name,
        version=version,
        binary_sha256=binary_sha256,
        support_sha256=support_sha256,
        model=ModelDeclarationV1(id=model_id, snapshot=snapshot, reasoning_effort=reasoning_effort),
    )


def _fake_receipt(
    claims: tuple[ClaimClass, ...], qualification_id: str = "check-q"
) -> ClaimReceiptV1:
    condition = ProtocolConditionV1(
        type=ConditionType.EVIDENCE_BOUND, status=ConditionStatus.TRUE, reason="ok"
    )
    canary_claims = tuple(
        CanaryClaimV1(canary_id=f"canary-{i}", conditions=(condition,), claim=claim)
        for i, claim in enumerate(claims)
    )
    return ClaimReceiptV1(
        schema_version=1,
        protocol_id="paired-change/v1",
        protocol_digest="b" * 64,
        qualification_id=qualification_id,
        evidence_manifest_sha256="c" * 64,
        protocol_evidence_sha256="d" * 64,
        baseline_state_sha256="e" * 64,
        candidate_state_sha256="f" * 64,
        changeset_sha256="1" * 64,
        qualification_conditions=(condition,),
        canary_claims=canary_claims,
        verifier_name="qualock",
        verifier_version="0.1.1",
    )


def _fake_details(
    *,
    baseline_state: AgentDependencyStateV1,
    candidate_state: AgentDependencyStateV1,
    claims: tuple[ClaimClass, ...] = (ClaimClass.NO_REGRESSION_OBSERVED,),
    qualification_id: str = "check-q",
) -> VerifiedPairedChangeV1:
    receipt = _fake_receipt(claims, qualification_id=qualification_id)
    return VerifiedPairedChangeV1(
        bundle=SimpleNamespace(manifest_sha256="2" * 64),
        evidence=SimpleNamespace(baseline_state=baseline_state, candidate_state=candidate_state),
        receipt=receipt,
        protocol_evidence_sha256="3" * 64,
    )


def _edge_preflight(
    *,
    agent_name: str = "codex",
    baseline_version: str = "0.150.0",
    catalog: tuple[str, ...] = ("0.150.0", "0.151.0", "0.152.0"),
) -> FirstBadPreflight:
    lock = baseline_lock(agent=agent_name, version=baseline_version)
    return FirstBadPreflight(
        agent_name=agent_name,
        baseline_lock=lock,
        upper_version=catalog[-1],
        catalog=catalog,
        suite_sha256=lock.suite_sha256,
        config_sha256=lock.config_sha256,
        model_pin=lock.model,
    )


def _qualification_result(
    *,
    qualification_id: str = "check-q",
    baseline_version: str = "0.150.0",
    candidate_version: str = "0.151.0",
    verdict: Verdict = Verdict.PASS,
) -> QualificationResult:
    return QualificationResult(
        qualification_id=qualification_id,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
    )


class _RecordingBaselineExecutor:
    def __init__(self, lock: BaselineLock) -> None:
        self.calls: list[tuple[Path, str]] = []
        self.lock = lock

    def __call__(self, workspace: Path, spec: str) -> BaselineLock:
        self.calls.append((workspace, spec))
        return self.lock


class _PoisonBaselineExecutor:
    def __call__(self, workspace: Path, spec: str) -> BaselineLock:
        raise AssertionError("baseline_executor must not be called for the first edge")


class _RecordingCheckExecutor:
    def __init__(self, result: QualificationResult) -> None:
        self.calls: list[tuple[Path, str]] = []
        self.result = result

    def __call__(self, workspace: Path, spec: str) -> QualificationResult:
        self.calls.append((workspace, spec))
        return self.result


class _RecordingEvidenceExporter:
    def __init__(self, exported: ExportedEvidenceBundle, *, create_dirs: bool = True) -> None:
        self.calls: list[tuple[Path, str, Path]] = []
        self.exported = exported
        self.create_dirs = create_dirs

    def __call__(
        self, workspace: Path, qualification_id: str, destination: Path
    ) -> ExportedEvidenceBundle:
        self.calls.append((workspace, qualification_id, destination))
        if self.create_dirs:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "marker.txt").write_text("bundle", encoding="utf-8")
            if self.exported.protocol_path is not None:
                self.exported.protocol_path.mkdir(parents=True, exist_ok=True)
                (self.exported.protocol_path / "marker.txt").write_text(
                    "protocol", encoding="utf-8"
                )
        return self.exported


def test_first_edge_writes_trusted_lock_and_never_calls_baseline_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-000"
    preflight = _edge_preflight()
    baseline_state = _agent_dependency_state_from_lock(preflight.baseline_lock)
    candidate_state = _state(version="0.151.0")

    check = _RecordingCheckExecutor(
        _qualification_result(qualification_id="check-q", candidate_version="0.151.0")
    )
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q",
        manifest_sha256="2" * 64,
        protocol_path=tmp_path / "owned-protocol",
    )
    exporter = _RecordingEvidenceExporter(exported)
    details = _fake_details(baseline_state=baseline_state, candidate_state=candidate_state)
    monkeypatch.setattr(
        first_bad_orchestrate, "verify_paired_change_details", lambda b, p: details
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=_PoisonBaselineExecutor(),
        check_executor=check,
        evidence_exporter=exporter,
    )

    edge = _execute_edge(preflight, 0, workspace, edge_dir, None, deps)

    assert check.calls == [(workspace, "codex@0.151.0")]
    assert exporter.calls == [(workspace, "check-q", edge_dir / "bundle")]
    written = read_baseline_lock(project_dir(workspace) / "baseline.lock")
    assert written == preflight.baseline_lock
    assert isinstance(edge, VerifiedFirstBadEdge)
    assert edge.record.baseline_version == "0.150.0"
    assert edge.record.candidate_version == "0.151.0"


def test_later_edge_without_expected_state_raises_before_baseline_executor(
    tmp_path: Path,
) -> None:
    preflight = _edge_preflight()
    baseline_exec = _RecordingBaselineExecutor(preflight.baseline_lock)
    deps = FirstBadExecutionDependencies(baseline_executor=baseline_exec)

    with pytest.raises(FirstBadBaselineUnresolved):
        _execute_edge(preflight, 1, tmp_path / "workspace", tmp_path / "edge-001", None, deps)

    assert baseline_exec.calls == []


def test_later_edge_baseline_mismatch_raises_before_check_or_export(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-001"
    preflight = _edge_preflight()
    expected_candidate = _state(version="0.151.0", binary_sha256="b" * 64)
    mismatched_lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name="codex", version="0.151.0", binary_sha256="c" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )
    baseline_exec = _RecordingBaselineExecutor(mismatched_lock)
    check_exec = _RecordingCheckExecutor(_qualification_result())
    exporter = _RecordingEvidenceExporter(
        ExportedEvidenceBundle(
            path=edge_dir / "bundle",
            qualification_id="check-q",
            manifest_sha256="2" * 64,
            protocol_path=None,
        ),
        create_dirs=False,
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=baseline_exec, check_executor=check_exec, evidence_exporter=exporter
    )

    with pytest.raises(FirstBadBaselineUnresolved):
        _execute_edge(preflight, 1, workspace, edge_dir, expected_candidate, deps)

    assert baseline_exec.calls == [(workspace, "codex@0.151.0")]
    assert check_exec.calls == []
    assert exporter.calls == []


def test_later_edge_baseline_match_proceeds_to_check_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-001"
    preflight = _edge_preflight()
    expected_candidate = _state(version="0.151.0")
    matching_lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name="codex", version="0.151.0", binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )
    assert _agent_dependency_state_from_lock(matching_lock) == expected_candidate

    baseline_exec = _RecordingBaselineExecutor(matching_lock)
    check_exec = _RecordingCheckExecutor(
        _qualification_result(
            qualification_id="check-q2", baseline_version="0.151.0", candidate_version="0.152.0"
        )
    )
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q2",
        manifest_sha256="2" * 64,
        protocol_path=tmp_path / "owned-protocol-2",
    )
    exporter = _RecordingEvidenceExporter(exported)
    details = _fake_details(
        baseline_state=expected_candidate, candidate_state=_state(version="0.152.0")
    )
    monkeypatch.setattr(
        first_bad_orchestrate, "verify_paired_change_details", lambda b, p: details
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=baseline_exec, check_executor=check_exec, evidence_exporter=exporter
    )

    edge = _execute_edge(preflight, 1, workspace, edge_dir, expected_candidate, deps)

    assert baseline_exec.calls == [(workspace, "codex@0.151.0")]
    assert check_exec.calls == [(workspace, "codex@0.152.0")]
    assert edge.record.baseline_version == "0.151.0"
    assert edge.record.candidate_version == "0.152.0"


def test_export_normalizes_protocol_companion_and_final_inventory_is_bundle_and_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-000"
    preflight = _edge_preflight()
    baseline_state = _agent_dependency_state_from_lock(preflight.baseline_lock)
    candidate_state = _state(version="0.151.0")
    protocol_src = tmp_path / "owned-companion"
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q",
        manifest_sha256="2" * 64,
        protocol_path=protocol_src,
    )
    exporter = _RecordingEvidenceExporter(exported)
    check = _RecordingCheckExecutor(_qualification_result())
    details = _fake_details(baseline_state=baseline_state, candidate_state=candidate_state)
    captured: dict[str, Path] = {}

    def fake_verify(bundle_path: Path, protocol_path: Path) -> VerifiedPairedChangeV1:
        captured["bundle"] = bundle_path
        captured["protocol"] = protocol_path
        assert bundle_path.exists()
        assert protocol_path.exists()
        return details

    monkeypatch.setattr(first_bad_orchestrate, "verify_paired_change_details", fake_verify)

    deps = FirstBadExecutionDependencies(
        baseline_executor=_PoisonBaselineExecutor(),
        check_executor=check,
        evidence_exporter=exporter,
    )

    _execute_edge(preflight, 0, workspace, edge_dir, None, deps)

    assert captured["bundle"] == edge_dir / "bundle"
    assert captured["protocol"] == edge_dir / "protocol"
    assert not protocol_src.exists()
    assert sorted(p.name for p in edge_dir.iterdir()) == ["bundle", "protocol"]


def test_missing_protocol_companion_raises_before_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-000"
    preflight = _edge_preflight()
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q",
        manifest_sha256="2" * 64,
        protocol_path=None,
    )
    exporter = _RecordingEvidenceExporter(exported)
    check = _RecordingCheckExecutor(_qualification_result())
    called: list[bool] = []
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_paired_change_details",
        lambda b, p: called.append(True),
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=_PoisonBaselineExecutor(),
        check_executor=check,
        evidence_exporter=exporter,
    )

    with pytest.raises(CommandError):
        _execute_edge(preflight, 0, workspace, edge_dir, None, deps)

    assert called == []


def test_edge_record_version_mismatch_against_frozen_catalog_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-000"
    preflight = _edge_preflight(catalog=("0.150.0", "0.151.0", "0.152.0"))
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q",
        manifest_sha256="2" * 64,
        protocol_path=tmp_path / "owned-protocol",
    )
    exporter = _RecordingEvidenceExporter(exported)
    check = _RecordingCheckExecutor(_qualification_result())
    wrong_candidate_state = _state(version="9.9.9")
    details = _fake_details(
        baseline_state=_agent_dependency_state_from_lock(preflight.baseline_lock),
        candidate_state=wrong_candidate_state,
    )
    monkeypatch.setattr(
        first_bad_orchestrate, "verify_paired_change_details", lambda b, p: details
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=_PoisonBaselineExecutor(),
        check_executor=check,
        evidence_exporter=exporter,
    )

    with pytest.raises(CommandError):
        _execute_edge(preflight, 0, workspace, edge_dir, None, deps)


@pytest.mark.parametrize(
    "claims,verdict,expected",
    [
        (
            (ClaimClass.NO_REGRESSION_OBSERVED, ClaimClass.NO_REGRESSION_OBSERVED),
            Verdict.BLOCK,
            EdgeClassification.NO_REGRESSION_OBSERVED,
        ),
        (
            (ClaimClass.ATTRIBUTABLE_CHANGESET, ClaimClass.NO_REGRESSION_OBSERVED),
            Verdict.PASS,
            EdgeClassification.ATTRIBUTABLE_CHANGESET,
        ),
        (
            (ClaimClass.UNRESOLVED, ClaimClass.NO_REGRESSION_OBSERVED),
            Verdict.PASS,
            EdgeClassification.UNRESOLVED,
        ),
    ],
    ids=["no-regression", "attributable", "unresolved"],
)
def test_edge_classification_driven_by_canary_claims_not_suite_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claims: tuple[ClaimClass, ...],
    verdict: Verdict,
    expected: EdgeClassification,
) -> None:
    workspace = tmp_path / "workspace"
    edge_dir = tmp_path / "edge-000"
    preflight = _edge_preflight()
    baseline_state = _agent_dependency_state_from_lock(preflight.baseline_lock)
    candidate_state = _state(version="0.151.0")
    exported = ExportedEvidenceBundle(
        path=edge_dir / "bundle",
        qualification_id="check-q",
        manifest_sha256="2" * 64,
        protocol_path=tmp_path / "owned-protocol",
    )
    exporter = _RecordingEvidenceExporter(exported)
    check = _RecordingCheckExecutor(_qualification_result(verdict=verdict))
    details = _fake_details(
        baseline_state=baseline_state, candidate_state=candidate_state, claims=claims
    )
    monkeypatch.setattr(
        first_bad_orchestrate, "verify_paired_change_details", lambda b, p: details
    )

    deps = FirstBadExecutionDependencies(
        baseline_executor=_PoisonBaselineExecutor(),
        check_executor=check,
        evidence_exporter=exporter,
    )

    edge = _execute_edge(preflight, 0, workspace, edge_dir, None, deps)

    assert edge.summary.classification is expected
    assert edge.summary.classification == derive_edge_classification(claims)


def _write_paired_change_project(root: Path) -> None:
    _setup_project(root)
    canary_path = root / ".qualock/canaries/sample.yaml"
    canary = yaml.safe_load(canary_path.read_text(encoding="utf-8"))
    canary["paired_change"] = {
        "material_dimensions": ["AGENT_BINARY"],
        "max_pair_gap_ms": 5000,
    }
    canary_path.write_text(yaml.safe_dump(canary, sort_keys=False), encoding="utf-8")


def test_execute_edge_full_lifecycle_derives_record_from_recomputed_details(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_paired_change_project(root)
    resolver = FakeResolver(with_support_binary=True)
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})

    config, canaries = load_project(root)
    trusted_binary = resolver.resolve("0.150.0")
    real_lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(
            name="codex",
            version="0.150.0",
            binary_sha256=trusted_binary.sha256,
            support_sha256=agent_support_fingerprint(trusted_binary),
        ),
        model=ModelPin(
            id=config.model.effective_model,
            snapshot=None,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={},
    )
    resolver.calls.clear()
    real_baseline_path = project_dir(root) / "baseline.lock"
    write_baseline_lock(real_baseline_path, real_lock)
    before = real_baseline_path.read_bytes()

    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    preflight = first_bad_preflight(root, "codex@0.151.0", catalog=catalog)

    workspace = tmp_path / "workspace"
    _snapshot_project_inputs(root, workspace)

    deps = FirstBadExecutionDependencies(
        baseline_executor=lambda ws, spec: execute_baseline(
            ws, spec, resolver=resolver, backend=backend, qualification_id="edge-base"
        ),
        check_executor=lambda ws, spec: execute_check(
            ws, spec, resolver=resolver, backend=backend, qualification_id="edge-check"
        ),
        evidence_exporter=export_evidence_bundle,
    )

    edge_dir = tmp_path / "edge-000"
    edge = _execute_edge(preflight, 0, workspace, edge_dir, None, deps)

    assert edge.record.index == 0
    assert edge.record.baseline_version == "0.150.0"
    assert edge.record.candidate_version == "0.151.0"
    assert edge.record.bundle_manifest_sha256 == edge.details.bundle.manifest_sha256
    assert edge.record.protocol_evidence_sha256 == edge.details.protocol_evidence_sha256
    assert edge.summary.index == 0
    assert edge.summary.classification is EdgeClassification.NO_REGRESSION_OBSERVED
    assert sorted(p.name for p in edge_dir.iterdir()) == ["bundle", "protocol"]
    assert real_baseline_path.read_bytes() == before


def test_two_edge_chain_continuity_with_real_executors(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_paired_change_project(root)
    resolver = FakeResolver(with_support_binary=True)
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0", "0.152.0"})

    config, canaries = load_project(root)
    trusted_binary = resolver.resolve("0.150.0")
    real_lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(
            name="codex",
            version="0.150.0",
            binary_sha256=trusted_binary.sha256,
            support_sha256=agent_support_fingerprint(trusted_binary),
        ),
        model=ModelPin(
            id=config.model.effective_model,
            snapshot=None,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={},
    )
    resolver.calls.clear()
    write_baseline_lock(project_dir(root) / "baseline.lock", real_lock)

    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0"))
    preflight = first_bad_preflight(root, "codex@0.152.0", catalog=catalog)

    workspace = tmp_path / "workspace"
    _snapshot_project_inputs(root, workspace)

    deps = FirstBadExecutionDependencies(
        baseline_executor=lambda ws, spec: execute_baseline(
            ws, spec, resolver=resolver, backend=backend, qualification_id=f"edge-base-{spec}"
        ),
        check_executor=lambda ws, spec: execute_check(
            ws, spec, resolver=resolver, backend=backend, qualification_id=f"edge-check-{spec}"
        ),
        evidence_exporter=export_evidence_bundle,
    )

    edge0 = _execute_edge(preflight, 0, workspace, tmp_path / "edge-000", None, deps)
    expected = edge0.record.candidate_runtime_identity
    edge1 = _execute_edge(preflight, 1, workspace, tmp_path / "edge-001", expected, deps)

    assert edge1.record.baseline_version == "0.151.0"
    assert edge1.record.candidate_version == "0.152.0"
    assert edge1.summary.classification is EdgeClassification.NO_REGRESSION_OBSERVED


# --- Task 8: scan-stop orchestration (scripted _execute_edge) --------------


class _ScriptedEdgeExecutor:
    def __init__(self, script: list) -> None:
        self.script = script
        self.calls: list[int] = []

    def __call__(
        self,
        preflight: FirstBadPreflight,
        index: int,
        workspace: Path,
        edge_dir: Path,
        expected_state: AgentDependencyStateV1 | None,
        deps: FirstBadExecutionDependencies,
    ) -> VerifiedFirstBadEdge:
        self.calls.append(index)
        outcome = self.script[index]
        if isinstance(outcome, BaseException):
            raise outcome
        (edge_dir / "bundle").mkdir(parents=True, exist_ok=True)
        (edge_dir / "bundle" / "marker.json").write_text("{}", encoding="utf-8")
        (edge_dir / "protocol").mkdir(parents=True, exist_ok=True)
        (edge_dir / "protocol" / "marker.json").write_text("{}", encoding="utf-8")
        return outcome


def _fake_edge(
    index: int,
    baseline_version: str,
    candidate_version: str,
    classification: EdgeClassification,
) -> VerifiedFirstBadEdge:
    baseline_state = _state(version=baseline_version)
    candidate_state = _state(version=candidate_version)
    record = FirstBadEdgeEvidenceV1(
        index=index,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        baseline_runtime_identity=baseline_state,
        candidate_runtime_identity=candidate_state,
        bundle_manifest_sha256="b" * 64,
        protocol_evidence_sha256="c" * 64,
    )
    claim = {
        EdgeClassification.NO_REGRESSION_OBSERVED: ClaimClass.NO_REGRESSION_OBSERVED,
        EdgeClassification.ATTRIBUTABLE_CHANGESET: ClaimClass.ATTRIBUTABLE_CHANGESET,
        EdgeClassification.UNRESOLVED: ClaimClass.UNRESOLVED,
    }[classification]
    receipt = _fake_receipt((claim,), qualification_id=f"edge-{index}")
    summary = derive_edge_summary(index, baseline_version, candidate_version, receipt)
    details = SimpleNamespace(evidence=SimpleNamespace(protocol_design_sha256="d" * 64))
    return VerifiedFirstBadEdge(record=record, summary=summary, details=details)


def _receipt_from_edges(
    edges: tuple[VerifiedFirstBadEdge, ...], catalog_versions: tuple[str, ...]
) -> FirstBadReceiptV1:
    summaries = tuple(edge.summary for edge in edges)
    claim, boundary_version, boundary_index = derive_chain_claim(catalog_versions, summaries)
    return FirstBadReceiptV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        chain_sha256="0" * 64,
        catalog_sha256="0" * 64,
        conditions=(),
        edges=summaries,
        claim=claim,
        boundary_version=boundary_version,
        boundary_edge_index=boundary_index,
    )


def _setup_scripted_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versions: tuple[str, ...],
    script: list,
) -> tuple[Path, FakeCatalog, _ScriptedEdgeExecutor]:
    root = tmp_path / "project"
    _write_real_project(root, agent="codex", baseline_version=versions[0])
    catalog = FakeCatalog(versions)
    executor = _ScriptedEdgeExecutor(script)
    monkeypatch.setattr(first_bad_orchestrate, "_execute_edge", executor)
    return root, catalog, executor


def test_full_scan_no_regression_runs_every_edge_and_finds_no_attributable_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(i, versions[i], versions[i + 1], EdgeClassification.NO_REGRESSION_OBSERVED)
        for i in range(3)
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_first_bad",
        lambda staging: _receipt_from_edges(tuple(script), catalog.versions),
    )

    outcome = execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1, 2]
    assert catalog.calls == 1
    assert outcome.receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert len(outcome.receipt.edges) == 3


def test_scan_stops_immediately_after_first_attributable_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(0, versions[0], versions[1], EdgeClassification.NO_REGRESSION_OBSERVED),
        _fake_edge(1, versions[1], versions[2], EdgeClassification.ATTRIBUTABLE_CHANGESET),
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_first_bad",
        lambda staging: _receipt_from_edges(tuple(script), catalog.versions),
    )

    outcome = execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1]
    assert outcome.receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert outcome.receipt.boundary_version == versions[2]
    assert outcome.receipt.boundary_edge_index == 1


def test_scan_includes_and_stops_at_unresolved_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(0, versions[0], versions[1], EdgeClassification.NO_REGRESSION_OBSERVED),
        _fake_edge(1, versions[1], versions[2], EdgeClassification.UNRESOLVED),
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_first_bad",
        lambda staging: _receipt_from_edges(tuple(script), catalog.versions),
    )

    outcome = execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1]
    assert outcome.receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert len(outcome.receipt.edges) == 2


def test_later_baseline_unstable_truncates_clean_prefix_to_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(0, versions[0], versions[1], EdgeClassification.NO_REGRESSION_OBSERVED),
        BaselineUnstableError("critical canary unstable while re-verifying baseline"),
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_first_bad",
        lambda staging: _receipt_from_edges((script[0],), catalog.versions),
    )

    outcome = execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1]
    assert outcome.receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert len(outcome.receipt.edges) == 1


def test_later_baseline_unresolved_truncates_clean_prefix_to_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(0, versions[0], versions[1], EdgeClassification.NO_REGRESSION_OBSERVED),
        FirstBadBaselineUnresolved("re-verified baseline diverges from prior candidate"),
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)
    monkeypatch.setattr(
        first_bad_orchestrate,
        "verify_first_bad",
        lambda staging: _receipt_from_edges((script[0],), catalog.versions),
    )

    outcome = execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1]
    assert outcome.receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert len(outcome.receipt.edges) == 1


def test_unexpected_edge_error_propagates_and_publishes_no_final_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    script = [
        _fake_edge(0, versions[0], versions[1], EdgeClassification.NO_REGRESSION_OBSERVED),
        CommandError("export backend exploded"),
    ]
    root, catalog, executor = _setup_scripted_scan(tmp_path, monkeypatch, versions, script)

    with pytest.raises(CommandError):
        execute_first_bad(root, f"codex@{versions[-1]}", catalog=catalog)

    assert executor.calls == [0, 1]
    results_dir = project_dir(root) / "results"
    published = [p.name for p in results_dir.iterdir() if p.name.startswith("first-bad-")]
    assert published == []


# --- Task 8: real filesystem publication, verification, immutability -------


def _versioned_check_executor(resolver: FakeResolver, backend: DeterministicProtocolBackend):
    def run(workspace: Path, spec: str) -> QualificationResult:
        return execute_check(
            workspace, spec, resolver=resolver, backend=backend, qualification_id=f"chk-{spec}"
        )

    return run


def _versioned_baseline_executor(resolver: FakeResolver, backend: DeterministicProtocolBackend):
    def run(workspace: Path, spec: str) -> BaselineLock:
        return execute_baseline(
            workspace, spec, resolver=resolver, backend=backend, qualification_id=f"base-{spec}"
        )

    return run


def _write_real_scan_project(
    root: Path, *, baseline_version: str
) -> tuple[FakeResolver, Path]:
    _write_paired_change_project(root)
    resolver = FakeResolver(with_support_binary=True)
    trusted_binary = resolver.resolve(baseline_version)
    config, canaries = load_project(root)
    real_lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(
            name="codex",
            version=baseline_version,
            binary_sha256=trusted_binary.sha256,
            support_sha256=agent_support_fingerprint(trusted_binary),
        ),
        model=ModelPin(
            id=config.model.effective_model,
            snapshot=None,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={},
    )
    resolver.calls.clear()
    baseline_path = project_dir(root) / "baseline.lock"
    write_baseline_lock(baseline_path, real_lock)
    return resolver, baseline_path


def test_real_full_scan_publishes_self_contained_verifiable_package(tmp_path: Path) -> None:
    root = tmp_path / "project"
    resolver, baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    before = baseline_path.read_bytes()
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )

    outcome = execute_first_bad(root, "codex@0.151.0", catalog=catalog, deps=deps)

    assert outcome.receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert outcome.package_path == project_dir(root) / "results" / outcome.first_bad_id
    assert sorted(p.name for p in outcome.package_path.iterdir()) == [
        "chain-evidence.json",
        "chain-receipt.json",
        "edges",
    ]
    edges = sorted(p.name for p in (outcome.package_path / "edges").iterdir())
    assert edges == ["000000"]
    edge_dir = outcome.package_path / "edges" / "000000"
    assert sorted(p.name for p in edge_dir.iterdir()) == ["bundle", "protocol"]

    for json_path in outcome.package_path.rglob("*.json"):
        text = json_path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text
        assert "qualock-first-bad-edge-" not in text
        assert ".first-bad-tmp-" not in text

    assert baseline_path.read_bytes() == before
    assert verify_first_bad(outcome.package_path) == outcome.receipt


def test_real_scan_stops_after_attributable_boundary_and_skips_later_edge(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0"))
    check_calls: list[str] = []

    def counting_check(workspace: Path, spec: str) -> QualificationResult:
        check_calls.append(spec)
        return execute_check(
            workspace, spec, resolver=resolver, backend=backend, qualification_id=f"chk-{spec}"
        )

    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=counting_check,
        evidence_exporter=export_evidence_bundle,
    )

    outcome = execute_first_bad(root, "codex@0.153.0", catalog=catalog, deps=deps)

    assert check_calls == ["codex@0.151.0", "codex@0.152.0"]
    assert outcome.receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert outcome.receipt.boundary_version == "0.152.0"
    assert len(outcome.receipt.edges) == 2
    assert verify_first_bad(outcome.package_path) == outcome.receipt


class _LaterBaselineUnstableBackend(DeterministicProtocolBackend):
    def __init__(self, *, success_versions: set[str], unstable_baselines: set[str]) -> None:
        super().__init__(success_versions=success_versions)
        self.unstable_baselines = unstable_baselines

    def run_attempt(self, **kwargs: object):
        result = FakeBackend.run_attempt(self, **kwargs)
        binary = kwargs["binary"]
        side = kwargs["side"]
        if side.value == "baseline" and binary.version in self.unstable_baselines:
            return replace(result, success=False)
        return result


def test_real_later_baseline_instability_publishes_verifiable_truncated_unresolved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    resolver, baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    before = baseline_path.read_bytes()
    backend = _LaterBaselineUnstableBackend(
        success_versions={"0.150.0", "0.151.0", "0.152.0"},
        unstable_baselines={"0.151.0"},
    )
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )

    outcome = execute_first_bad(root, "codex@0.152.0", catalog=catalog, deps=deps)

    assert outcome.receipt.claim is FirstBadClaimClass.UNRESOLVED
    assert len(outcome.receipt.edges) == 1
    assert outcome.receipt.edges[0].classification is EdgeClassification.NO_REGRESSION_OBSERVED
    assert verify_first_bad(outcome.package_path) == outcome.receipt
    assert baseline_path.read_bytes() == before


@pytest.mark.parametrize(
    "bad_id",
    ["../escaped-first-bad", "nested/first-bad", "/tmp/absolute-first-bad"],
)
def test_first_bad_id_cannot_escape_results_directory(
    tmp_path: Path, bad_id: str
) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )

    with pytest.raises(CommandError, match="invalid first-bad id"):
        execute_first_bad(
            root,
            "codex@0.151.0",
            catalog=catalog,
            deps=deps,
            first_bad_id=bad_id,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_first_bad_staging_is_private_even_with_permissive_umask(tmp_path: Path) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )
    prior_umask = os.umask(0o022)
    try:
        outcome = execute_first_bad(root, "codex@0.151.0", catalog=catalog, deps=deps)
    finally:
        os.umask(prior_umask)

    assert stat.S_IMODE(outcome.package_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((outcome.package_path / "edges").stat().st_mode) == 0o700


def test_real_publish_collision_preserves_prior_package_and_leaves_staging(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )

    first = execute_first_bad(
        root, "codex@0.151.0", catalog=catalog, deps=deps, first_bad_id="first-bad-20260915T000000Z-deadbeef"
    )
    before_contents = (first.package_path / "chain-evidence.json").read_bytes()
    results_dir = project_dir(root) / "results"
    before_tmp_dirs = {p.name for p in results_dir.iterdir() if p.name.startswith(".first-bad-tmp-")}

    catalog2 = FakeCatalog(("0.150.0", "0.151.0"))
    with pytest.raises(OSError):
        execute_first_bad(
            root, "codex@0.151.0", catalog=catalog2, deps=deps, first_bad_id="first-bad-20260915T000000Z-deadbeef"
        )

    assert (first.package_path / "chain-evidence.json").read_bytes() == before_contents
    after_tmp_dirs = {p.name for p in results_dir.iterdir() if p.name.startswith(".first-bad-tmp-")}
    assert after_tmp_dirs - before_tmp_dirs


def test_history_scan_ignores_first_bad_result_directories(tmp_path: Path) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )

    execute_first_bad(root, "codex@0.151.0", catalog=catalog, deps=deps)
    results_dir = project_dir(root) / "results"
    (results_dir / ".first-bad-tmp-orphan").mkdir()

    summary = scan_results(results_dir)

    assert summary.loaded == ()
    assert summary.ignored == ()


def test_real_callbacks_run_only_after_staging_and_verified_edge(tmp_path: Path) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )
    callback_order: list[str] = []
    staging: Path | None = None

    def on_start(preflight: FirstBadPreflight, staging_path: Path) -> None:
        nonlocal staging
        staging = staging_path
        callback_order.append("start")
        assert preflight.catalog == ("0.150.0", "0.151.0")
        assert staging_path.is_dir()
        assert (staging_path / "edges").is_dir()
        assert list((staging_path / "edges").iterdir()) == []

    def on_edge(summary) -> None:
        callback_order.append("edge")
        assert staging is not None
        edge_dir = staging / "edges" / f"{summary.index:06d}"
        assert sorted(path.name for path in edge_dir.iterdir()) == ["bundle", "protocol"]
        details = first_bad_orchestrate.verify_paired_change_details(
            edge_dir / "bundle", edge_dir / "protocol"
        )
        recomputed = derive_edge_summary(
            summary.index,
            summary.baseline_version,
            summary.candidate_version,
            details.receipt,
        )
        assert recomputed == summary

    outcome = execute_first_bad(
        root,
        "codex@0.151.0",
        catalog=catalog,
        deps=deps,
        on_start=on_start,
        on_edge=on_edge,
    )

    assert callback_order == ["start", "edge"]
    assert outcome.receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND


@pytest.mark.parametrize("failure_point", ["start", "edge"])
def test_real_callback_failure_propagates_without_publication(
    tmp_path: Path, failure_point: str
) -> None:
    root = tmp_path / "project"
    resolver, _baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0"))
    deps = FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=_versioned_check_executor(resolver, backend),
        evidence_exporter=export_evidence_bundle,
    )
    staging: Path | None = None

    def on_start(_preflight: FirstBadPreflight, staging_path: Path) -> None:
        nonlocal staging
        staging = staging_path
        if failure_point == "start":
            raise RuntimeError("callback failed")

    def on_edge(summary) -> None:
        assert staging is not None
        edge_dir = staging / "edges" / f"{summary.index:06d}"
        assert (edge_dir / "bundle").is_dir()
        assert (edge_dir / "protocol").is_dir()
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        execute_first_bad(
            root,
            "codex@0.151.0",
            catalog=catalog,
            deps=deps,
            on_start=on_start,
            on_edge=on_edge,
        )

    results_dir = project_dir(root) / "results"
    assert staging is not None and staging.is_dir()
    assert [path for path in results_dir.iterdir() if path.name.startswith("first-bad-")] == []
    if failure_point == "start":
        assert list((staging / "edges").iterdir()) == []
    else:
        assert sorted(path.name for path in (staging / "edges" / "000000").iterdir()) == [
            "bundle",
            "protocol",
        ]
