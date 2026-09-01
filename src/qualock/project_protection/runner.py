from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from qualock.config.models import ProjectProtectionConfig
from qualock.run.process import run_process

from .models import ProjectLock, ProtectionRun, ProtectionStatus


class ProjectProtectionError(RuntimeError):
    pass


class ProtectionBaselineError(ProjectProtectionError):
    pass


def run_protections(
    root: Path,
    definitions: Sequence[ProjectProtectionConfig],
) -> tuple[ProtectionRun, ...]:
    runs: list[ProtectionRun] = []
    for definition in definitions:
        try:
            result = run_process(
                definition.command,
                cwd=root,
                timeout_seconds=definition.timeout_seconds,
            )
        except OSError as exc:
            runs.append(
                ProtectionRun(
                    id=definition.id,
                    name=definition.name,
                    command=definition.command,
                    timeout_seconds=definition.timeout_seconds,
                    status=ProtectionStatus.INCOMPLETE,
                    exit_code=None,
                    timed_out=False,
                    duration_ms=0,
                    error=str(exc),
                )
            )
            continue

        if result.timed_out or result.exit_code is None:
            status = ProtectionStatus.INCOMPLETE
        elif result.exit_code == 0:
            status = ProtectionStatus.PASS
        else:
            status = ProtectionStatus.FAIL
        runs.append(
            ProtectionRun(
                id=definition.id,
                name=definition.name,
                command=definition.command,
                timeout_seconds=definition.timeout_seconds,
                status=status,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=max(0, int(result.elapsed_seconds * 1000)),
            )
        )
    return tuple(runs)


def read_git_state(root: Path) -> tuple[str, bool]:
    try:
        head = run_process(["git", "rev-parse", "HEAD"], cwd=root, timeout_seconds=5)
        status = run_process(["git", "status", "--porcelain"], cwd=root, timeout_seconds=5)
    except OSError as exc:
        raise ProjectProtectionError(f"unable to inspect Git state: {exc}") from exc
    if head.timed_out or head.exit_code != 0:
        raise ProjectProtectionError("project protection requires a Git repository with HEAD")
    if status.timed_out or status.exit_code != 0:
        raise ProjectProtectionError("unable to inspect Git working tree")
    return head.stdout.strip(), bool(status.stdout.strip())


def create_project_lock(
    root: Path,
    definitions: Sequence[ProjectProtectionConfig],
    runs: Sequence[ProtectionRun],
    *,
    created_at: str | None = None,
) -> ProjectLock:
    if not definitions:
        raise ProtectionBaselineError("no project protections configured")
    if len(definitions) != len(runs) or any(run.status is not ProtectionStatus.PASS for run in runs):
        raise ProtectionBaselineError("all project protections must pass before they can be locked")
    git_head, git_dirty = read_git_state(root)
    return ProjectLock(
        created_at=created_at or datetime.now(UTC).isoformat(),
        git_head=git_head,
        git_dirty=git_dirty,
        protections=list(definitions),
        baseline=list(runs),
    )
