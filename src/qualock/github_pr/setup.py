"""Local-only installer for the QuaLock GitHub PR qualification workflows."""

import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW

_PRODUCER_RELATIVE_PATH = Path(".github/workflows/qualock-pr.yml")
_REPORTER_RELATIVE_PATH = Path(".github/workflows/qualock-pr-report.yml")


class GitHubSetupStatus(str, Enum):
    CREATED = "created"
    ALREADY_CONFIGURED = "already_configured"


@dataclass(frozen=True)
class GitHubSetupOutcome:
    status: GitHubSetupStatus
    producer_path: Path
    reporter_path: Path


class GitHubSetupConflictError(Exception):
    """Raised when an existing workflow file differs from the QuaLock template."""


def _classify(path: Path, expected: str) -> str:
    if not path.exists():
        return "missing"
    if path.read_text(encoding="utf-8") == expected:
        return "identical"
    return "conflict"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_github_workflows(root: Path) -> GitHubSetupOutcome:
    producer_path = root / _PRODUCER_RELATIVE_PATH
    reporter_path = root / _REPORTER_RELATIVE_PATH

    producer_state = _classify(producer_path, PRODUCER_WORKFLOW)
    reporter_state = _classify(reporter_path, REPORTER_WORKFLOW)

    if producer_state == "conflict" or reporter_state == "conflict":
        raise GitHubSetupConflictError(
            f"existing workflow file(s) differ from the QuaLock template: "
            f"producer={producer_state}, reporter={reporter_state}"
        )

    if producer_state == "identical" and reporter_state == "identical":
        return GitHubSetupOutcome(
            status=GitHubSetupStatus.ALREADY_CONFIGURED,
            producer_path=producer_path,
            reporter_path=reporter_path,
        )

    if producer_state == "missing":
        _write_atomic(producer_path, PRODUCER_WORKFLOW)
    if reporter_state == "missing":
        _write_atomic(reporter_path, REPORTER_WORKFLOW)

    return GitHubSetupOutcome(
        status=GitHubSetupStatus.CREATED,
        producer_path=producer_path,
        reporter_path=reporter_path,
    )
