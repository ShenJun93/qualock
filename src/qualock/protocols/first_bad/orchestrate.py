"""First-bad/v1 preflight, isolated project-input snapshotting, and full-scan orchestration."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qualock.agents.orchestration import orchestration_capabilities
from qualock.agents.releases import StableReleaseCatalog, default_stable_release_catalog
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock, write_baseline_lock
from qualock.baseline.models import BaselineLock, ModelPin
from qualock.commands import (
    BaselineUnstableError,
    CommandError,
    execute_baseline,
    execute_check,
    parse_agent_spec,
)
from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.evidence.export import (
    ExportedEvidenceBundle,
    _rename_noreplace,
    export_evidence_bundle,
)
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.protocols.first_bad.claims import derive_edge_summary
from qualock.protocols.first_bad.fingerprint import digest_catalog, digest_chain_evidence
from qualock.protocols.first_bad.io import (
    CHAIN_EVIDENCE_FILENAME,
    EDGE_INDEX_WIDTH,
    EDGES_DIRNAME,
    write_first_bad_receipt,
)
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadChainEvidenceV1,
    FirstBadEdgeEvidenceV1,
    FirstBadEdgeSummaryV1,
    FirstBadReceiptV1,
)
from qualock.protocols.first_bad.verify import verify_first_bad
from qualock.protocols.paired_change.models import AgentDependencyStateV1, ModelDeclarationV1
from qualock.protocols.paired_change.verify import (
    VerifiedPairedChangeV1,
    verify_paired_change_details,
)
from qualock.qualification.models import QualificationResult

__all__ = [
    "FirstBadBaselineUnresolved",
    "FirstBadExecutionDependencies",
    "FirstBadOrchestrationOutcome",
    "FirstBadPreflight",
    "VerifiedFirstBadEdge",
    "execute_first_bad",
    "first_bad_preflight",
]

BaselineExecutor = Callable[[Path, str], BaselineLock]
CheckExecutor = Callable[[Path, str], QualificationResult]
EvidenceExporter = Callable[[Path, str, Path], ExportedEvidenceBundle]

_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_FIRST_BAD_ID_RE = re.compile(r"^first-bad-\d{8}T\d{6}Z-[0-9a-f]{8}$")


@dataclass(frozen=True)
class FirstBadPreflight:
    agent_name: str
    baseline_lock: BaselineLock
    upper_version: str
    catalog: tuple[str, ...]
    suite_sha256: str
    config_sha256: str
    model_pin: ModelPin


def _version_key(version: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise CommandError(f"not a stable version: {version!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def first_bad_preflight(
    root: Path,
    upper_spec: str,
    *,
    catalog: StableReleaseCatalog | None = None,
) -> FirstBadPreflight:
    upper_name, upper_version = parse_agent_spec(upper_spec)

    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))

    agent_name = lock.agent.name
    if config.agent.name != agent_name:
        raise CommandError(
            f"config agent {config.agent.name} does not match baseline agent {lock.agent.name}"
        )
    if agent_name == "antigravity":
        raise CommandError(f"first-bad does not support agent {agent_name!r}")
    if not orchestration_capabilities(agent_name).first_bad:
        raise CommandError(f"first-bad does not support agent {agent_name!r}")
    if upper_name != agent_name:
        raise CommandError(
            f"upper bound agent {upper_name} does not match baseline agent {agent_name}"
        )

    _version_key(upper_version)
    baseline_version = lock.agent.version
    _version_key(baseline_version)

    catalog_source = catalog or default_stable_release_catalog(agent_name)
    frozen_snapshot = tuple(catalog_source.stable_versions())
    for entry in frozen_snapshot:
        _version_key(entry)

    if upper_version not in frozen_snapshot:
        raise CommandError(f"{agent_name}@{upper_version} is not a published stable release")
    if baseline_version not in frozen_snapshot:
        raise CommandError(
            f"trusted baseline {agent_name}@{baseline_version} is not a published stable release"
        )
    if _version_key(upper_version) <= _version_key(baseline_version):
        raise CommandError("upper bound must be numerically newer than the trusted baseline")

    frozen_range = tuple(
        version
        for version in sorted(set(frozen_snapshot), key=_version_key)
        if _version_key(baseline_version) <= _version_key(version) <= _version_key(upper_version)
    )

    return FirstBadPreflight(
        agent_name=agent_name,
        baseline_lock=lock,
        upper_version=upper_version,
        catalog=frozen_range,
        suite_sha256=lock.suite_sha256,
        config_sha256=lock.config_sha256,
        model_pin=lock.model,
    )


def _copy_regular_file(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise CommandError(f"refusing to snapshot symlinked path: {source}")
    if not source.is_file():
        raise CommandError(f"expected a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _snapshot_project_inputs(root: Path, workspace: Path) -> None:
    root = root.resolve()
    workspace = workspace.resolve()

    _copy_regular_file(
        project_dir(root) / "config.yaml", project_dir(workspace) / "config.yaml"
    )

    source_canaries = project_dir(root) / "canaries"
    if not source_canaries.exists():
        return
    if source_canaries.is_symlink():
        raise CommandError(f"refusing to snapshot symlinked path: {source_canaries}")
    if not source_canaries.is_dir():
        raise CommandError(f"expected a directory: {source_canaries}")

    dest_canaries = project_dir(workspace) / "canaries"
    for dirpath, dirnames, filenames in os.walk(source_canaries, followlinks=False):
        current = Path(dirpath)
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                raise CommandError(f"refusing to snapshot symlinked path: {child}")
        for name in filenames:
            child = current / name
            if child.is_symlink():
                raise CommandError(f"refusing to snapshot symlinked path: {child}")
            relative = child.relative_to(source_canaries)
            _copy_regular_file(child, dest_canaries / relative)


class FirstBadBaselineUnresolved(Exception):
    """A carried edge's re-verified baseline diverges from the previous candidate."""


@dataclass(frozen=True)
class FirstBadExecutionDependencies:
    baseline_executor: BaselineExecutor = execute_baseline
    check_executor: CheckExecutor = execute_check
    evidence_exporter: EvidenceExporter = export_evidence_bundle


@dataclass(frozen=True)
class VerifiedFirstBadEdge:
    record: FirstBadEdgeEvidenceV1
    summary: FirstBadEdgeSummaryV1
    details: VerifiedPairedChangeV1


def _agent_dependency_state_from_lock(lock: BaselineLock) -> AgentDependencyStateV1:
    return AgentDependencyStateV1(
        agent_name=lock.agent.name,
        version=lock.agent.version,
        binary_sha256=lock.agent.binary_sha256,
        support_sha256=lock.agent.support_sha256,
        model=ModelDeclarationV1(
            id=lock.model.id,
            snapshot=lock.model.snapshot,
            reasoning_effort=lock.model.reasoning_effort,
        ),
    )


def _execute_edge(
    preflight: FirstBadPreflight,
    index: int,
    workspace: Path,
    edge_dir: Path,
    expected_baseline_state: AgentDependencyStateV1 | None,
    deps: FirstBadExecutionDependencies,
) -> VerifiedFirstBadEdge:
    agent_name = preflight.agent_name
    baseline_version = preflight.catalog[index]
    candidate_version = preflight.catalog[index + 1]

    if index == 0:
        write_baseline_lock(project_dir(workspace) / "baseline.lock", preflight.baseline_lock)
    else:
        if expected_baseline_state is None:
            raise FirstBadBaselineUnresolved(
                f"edge[{index}] has no verified previous candidate identity to anchor against"
            )
        fresh_lock = deps.baseline_executor(workspace, f"{agent_name}@{baseline_version}")
        actual_state = _agent_dependency_state_from_lock(fresh_lock)
        if actual_state != expected_baseline_state:
            raise FirstBadBaselineUnresolved(
                f"edge[{index}] re-verified baseline identity does not match "
                "the previous edge's candidate identity"
            )

    qualification_result = deps.check_executor(workspace, f"{agent_name}@{candidate_version}")

    bundle_dest = edge_dir / "bundle"
    exported = deps.evidence_exporter(
        workspace, qualification_result.qualification_id, bundle_dest
    )
    if exported.protocol_path is None:
        raise CommandError(
            f"edge[{index}] export is missing a required paired-change protocol companion"
        )

    protocol_dest = edge_dir / "protocol"
    os.replace(exported.protocol_path, protocol_dest)

    details = verify_paired_change_details(bundle_dest, protocol_dest)

    record = FirstBadEdgeEvidenceV1(
        index=index,
        baseline_version=details.evidence.baseline_state.version,
        candidate_version=details.evidence.candidate_state.version,
        baseline_runtime_identity=details.evidence.baseline_state,
        candidate_runtime_identity=details.evidence.candidate_state,
        bundle_manifest_sha256=details.bundle.manifest_sha256,
        protocol_evidence_sha256=details.protocol_evidence_sha256,
    )
    if record.baseline_version != baseline_version or record.candidate_version != candidate_version:
        raise CommandError(
            f"edge[{index}] verified versions {record.baseline_version}->"
            f"{record.candidate_version} do not match the frozen catalog edge "
            f"{baseline_version}->{candidate_version}"
        )

    summary = derive_edge_summary(
        index, record.baseline_version, record.candidate_version, details.receipt
    )

    return VerifiedFirstBadEdge(record=record, summary=summary, details=details)


@dataclass(frozen=True)
class FirstBadOrchestrationOutcome:
    first_bad_id: str
    agent_name: str
    baseline_version: str
    upper_version: str
    package_path: Path
    receipt: FirstBadReceiptV1


OnStart = Callable[[FirstBadPreflight], None]
OnEdge = Callable[[VerifiedFirstBadEdge], None]


def _first_bad_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"first-bad-{stamp}-{uuid.uuid4().hex[:8]}"


def _resolve_first_bad_id(first_bad_id: str | None) -> str:
    resolved = first_bad_id or _first_bad_id()
    if _FIRST_BAD_ID_RE.fullmatch(resolved) is None:
        raise CommandError("invalid first-bad id")
    return resolved


def _edge_dirname(index: int) -> str:
    return f"{index:0{EDGE_INDEX_WIDTH}d}"


def _create_new_file(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)


def _run_edges(
    preflight: FirstBadPreflight,
    root: Path,
    edges_dir: Path,
    deps: FirstBadExecutionDependencies,
    on_edge: OnEdge | None,
) -> tuple[VerifiedFirstBadEdge, ...]:
    verified_edges: list[VerifiedFirstBadEdge] = []
    expected_state: AgentDependencyStateV1 | None = None

    for index in range(len(preflight.catalog) - 1):
        with tempfile.TemporaryDirectory(prefix="qualock-first-bad-edge-") as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            edge_scratch = tmp_path / "edge"
            _snapshot_project_inputs(root, workspace)
            try:
                edge = _execute_edge(preflight, index, workspace, edge_scratch, expected_state, deps)
            except (BaselineUnstableError, FirstBadBaselineUnresolved):
                return tuple(verified_edges)
            shutil.copytree(edge_scratch, edges_dir / _edge_dirname(index))

        verified_edges.append(edge)
        if on_edge is not None:
            on_edge(edge)
        if edge.summary.classification is not EdgeClassification.NO_REGRESSION_OBSERVED:
            break
        expected_state = edge.record.candidate_runtime_identity

    return tuple(verified_edges)


def _build_chain_evidence(
    preflight: FirstBadPreflight, verified_edges: tuple[VerifiedFirstBadEdge, ...]
) -> FirstBadChainEvidenceV1:
    first_edge = verified_edges[0]
    protocol_design_sha256 = first_edge.details.evidence.protocol_design_sha256
    for edge in verified_edges[1:]:
        if edge.details.evidence.protocol_design_sha256 != protocol_design_sha256:
            raise CommandError("protocol design drifted across verified edges")

    temp = FirstBadChainEvidenceV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        agent_name=preflight.agent_name,
        baseline_version=preflight.catalog[0],
        baseline_runtime_identity=first_edge.record.baseline_runtime_identity,
        upper_version=preflight.upper_version,
        catalog_versions=preflight.catalog,
        catalog_sha256=digest_catalog(preflight.catalog),
        suite_sha256=preflight.suite_sha256,
        config_sha256=preflight.config_sha256,
        model_pin=ModelDeclarationV1(
            id=preflight.model_pin.id,
            snapshot=preflight.model_pin.snapshot,
            reasoning_effort=preflight.model_pin.reasoning_effort,
        ),
        protocol_design_sha256=protocol_design_sha256,
        edges=tuple(edge.record for edge in verified_edges),
        chain_sha256="0" * 64,
    )
    return temp.model_copy(update={"chain_sha256": digest_chain_evidence(temp)})


def execute_first_bad(
    root: Path,
    upper_spec: str,
    *,
    catalog: StableReleaseCatalog | None = None,
    deps: FirstBadExecutionDependencies | None = None,
    first_bad_id: str | None = None,
    on_start: OnStart | None = None,
    on_edge: OnEdge | None = None,
) -> FirstBadOrchestrationOutcome:
    resolved_id = _resolve_first_bad_id(first_bad_id)
    preflight = first_bad_preflight(root, upper_spec, catalog=catalog)
    if on_start is not None:
        on_start(preflight)
    deps = deps or FirstBadExecutionDependencies()

    results_dir = project_dir(root) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    staging_root = results_dir / f".first-bad-tmp-{uuid.uuid4().hex}"
    staging_root.mkdir(mode=0o700)
    edges_dir = staging_root / EDGES_DIRNAME
    edges_dir.mkdir(mode=0o700)

    verified_edges = _run_edges(preflight, root, edges_dir, deps, on_edge)
    evidence = _build_chain_evidence(preflight, verified_edges)

    _create_new_file(
        staging_root / CHAIN_EVIDENCE_FILENAME,
        canonical_json_file_bytes(evidence.model_dump(mode="json")),
    )

    receipt = verify_first_bad(staging_root)
    write_first_bad_receipt(staging_root, receipt)
    receipt = verify_first_bad(staging_root)

    final_path = results_dir / resolved_id
    _rename_noreplace(staging_root, final_path)

    return FirstBadOrchestrationOutcome(
        first_bad_id=resolved_id,
        agent_name=preflight.agent_name,
        baseline_version=preflight.catalog[0],
        upper_version=preflight.upper_version,
        package_path=final_path,
        receipt=receipt,
    )
