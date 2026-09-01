from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from qualock.config.io import load_config
from qualock.project import project_dir

from .io import read_project_lock, write_project_lock
from .models import (
    ProjectProtectResult,
    ProjectVerifyResult,
    ProtectionRun,
    ProtectionStatus,
)
from .runner import create_project_lock, read_git_state, run_protections
from .signing import ensure_signing_key, load_signing_key
from .storage import write_project_evidence


class ProjectProtectionConfigError(ValueError):
    pass


def _operation_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _overall_status(runs: tuple[ProtectionRun, ...]) -> ProtectionStatus:
    if any(run.status is ProtectionStatus.INCOMPLETE for run in runs):
        return ProtectionStatus.INCOMPLETE
    if any(run.status is ProtectionStatus.FAIL for run in runs):
        return ProtectionStatus.FAIL
    return ProtectionStatus.PASS


def execute_protect(
    root: Path,
    *,
    operation_id: str | None = None,
    created_at: str | None = None,
    key_path: Path | None = None,
) -> ProjectProtectResult:
    qdir = project_dir(root)
    config = load_config(qdir / "config.yaml")
    if not config.protections:
        raise ProjectProtectionConfigError("no project protections configured")
    oid = operation_id or _operation_id("protect")
    timestamp = created_at or datetime.now(UTC).isoformat()
    runs = run_protections(root, config.protections)
    status = _overall_status(runs)
    git_head, git_dirty = read_git_state(root)
    lock_created = status is ProtectionStatus.PASS
    if lock_created:
        key = ensure_signing_key(key_path)
        lock = create_project_lock(
            root,
            config.protections,
            runs,
            created_at=timestamp,
        )
        write_project_lock(qdir / "project.lock", lock, key)
    result = ProjectProtectResult(
        operation_id=oid,
        created_at=timestamp,
        status=status,
        git_head=git_head,
        git_dirty=git_dirty,
        runs=list(runs),
        lock_created=lock_created,
    )
    write_project_evidence(qdir / "results", oid, kind="protect", result=result)
    return result


def execute_verify(
    root: Path,
    *,
    operation_id: str | None = None,
    created_at: str | None = None,
    key_path: Path | None = None,
) -> ProjectVerifyResult:
    qdir = project_dir(root)
    key = load_signing_key(key_path)
    lock = read_project_lock(qdir / "project.lock", key)
    oid = operation_id or _operation_id("verify")
    timestamp = created_at or datetime.now(UTC).isoformat()
    runs = run_protections(root, lock.protections)
    current_git_head, current_git_dirty = read_git_state(root)
    result = ProjectVerifyResult(
        operation_id=oid,
        created_at=timestamp,
        status=_overall_status(runs),
        baseline_git_head=lock.git_head,
        baseline_git_dirty=lock.git_dirty,
        current_git_head=current_git_head,
        current_git_dirty=current_git_dirty,
        runs=list(runs),
    )
    write_project_evidence(qdir / "results", oid, kind="verify", result=result)
    return result
