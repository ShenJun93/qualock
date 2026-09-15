"""First-bad/v1 preflight and isolated project-input snapshotting."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from qualock.agents.orchestration import orchestration_capabilities
from qualock.agents.releases import StableReleaseCatalog, default_stable_release_catalog
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.baseline.models import BaselineLock, ModelPin
from qualock.commands import CommandError, parse_agent_spec
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint

__all__ = ["FirstBadPreflight", "first_bad_preflight"]

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
