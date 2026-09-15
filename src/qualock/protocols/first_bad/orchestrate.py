"""First-bad/v1 preflight, isolated project-input snapshotting, and edge execution."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qualock.agents.orchestration import orchestration_capabilities
from qualock.agents.releases import StableReleaseCatalog, default_stable_release_catalog
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock, write_baseline_lock
from qualock.baseline.models import BaselineLock, ModelPin
from qualock.commands import CommandError, execute_baseline, execute_check, parse_agent_spec
from qualock.evidence.export import ExportedEvidenceBundle, export_evidence_bundle
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.protocols.first_bad.claims import derive_edge_summary
from qualock.protocols.first_bad.models import FirstBadEdgeEvidenceV1, FirstBadEdgeSummaryV1
from qualock.protocols.paired_change.models import AgentDependencyStateV1, ModelDeclarationV1
from qualock.protocols.paired_change.verify import (
    VerifiedPairedChangeV1,
    verify_paired_change_details,
)
from qualock.qualification.models import QualificationResult

__all__ = [
    "FirstBadBaselineUnresolved",
    "FirstBadExecutionDependencies",
    "FirstBadPreflight",
    "VerifiedFirstBadEdge",
    "first_bad_preflight",
]

BaselineExecutor = Callable[[Path, str], BaselineLock]
CheckExecutor = Callable[[Path, str], QualificationResult]
EvidenceExporter = Callable[[Path, str, Path], ExportedEvidenceBundle]

_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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
