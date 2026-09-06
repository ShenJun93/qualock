"""Local-only installer for the QuaLock GitHub PR qualification workflows."""

import hashlib
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW

_PRODUCER_RELATIVE_PATH = Path(".github/workflows/qualock-pr.yml")
_REPORTER_RELATIVE_PATH = Path(".github/workflows/qualock-pr-report.yml")

_LEGACY_PRODUCER_SHA256 = (
    "29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69"
)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class GitHubSetupStatus(str, Enum):
    CREATED = "created"
    UPGRADED = "upgraded"
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


def _classify_producer(path: Path, expected: str) -> str:
    if not path.exists():
        return "missing"
    text = path.read_text(encoding="utf-8")
    if text == expected:
        return "identical"
    if _text_sha256(text) == _LEGACY_PRODUCER_SHA256:
        return "legacy"
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

    producer_state = _classify_producer(producer_path, PRODUCER_WORKFLOW)
    reporter_state = _classify(reporter_path, REPORTER_WORKFLOW)

    if producer_state == "conflict" or reporter_state == "conflict":
        raise GitHubSetupConflictError(
            f"existing workflow file(s) differ from the QuaLock template: "
            f"producer={producer_state}, reporter={reporter_state}"
        )

    if producer_state in {"missing", "legacy"}:
        _write_atomic(producer_path, PRODUCER_WORKFLOW)
    if reporter_state == "missing":
        _write_atomic(reporter_path, REPORTER_WORKFLOW)

    status = (
        GitHubSetupStatus.UPGRADED
        if producer_state == "legacy"
        else GitHubSetupStatus.CREATED
        if "missing" in {producer_state, reporter_state}
        else GitHubSetupStatus.ALREADY_CONFIGURED
    )

    return GitHubSetupOutcome(
        status=status,
        producer_path=producer_path,
        reporter_path=reporter_path,
    )
