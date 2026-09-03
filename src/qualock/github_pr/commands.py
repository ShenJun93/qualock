import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from packaging.version import Version
from platformdirs import user_cache_dir
from pydantic import ValidationError

import qualock
from qualock.agents.resolver import CodexResolver
from qualock.baseline.io import BaselineStaleError, assert_suite_fresh, read_baseline_lock
from qualock.baseline.models import BaselineLock
from qualock.commands import Resolver, execute_check
from qualock.github_pr.models import (
    PrClassification,
    PrReasonCode,
    PullRequestContext,
    PullRequestReport,
)
from qualock.github_pr.report import (
    incomplete_report,
    not_applicable_report,
    report_from_qualification,
)
from qualock.github_pr.source import GitHubPrSource, GitHubSourceError, prepare_pr_context
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult

_EXACT_STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_BASELINE_LOCK_PATH = ".qualock/baseline.lock"
_MAX_PROPOSED_LOCK_BYTES = 131_072


class PrValidationError(Exception):
    """Raised when a proposed baseline lock cannot be trusted for qualification."""


@dataclass(frozen=True)
class CandidateRequest:
    version: str
    binary_sha256: str


def validate_proposed_lock(
    root: Path,
    raw: bytes,
    *,
    resolver: Resolver,
) -> CandidateRequest:
    try:
        proposed = BaselineLock.model_validate_json(raw)
    except ValidationError as error:
        raise PrValidationError("proposed baseline lock is malformed") from error

    config, canaries = load_project(root)
    trusted = read_baseline_lock(project_dir(root) / "baseline.lock")
    suite_sha = suite_fingerprint(canaries)
    config_sha = config_fingerprint(config)
    assert_suite_fresh(trusted, suite_sha, config_sha)

    if (
        proposed.agent.name != "codex"
        or trusted.agent.name != "codex"
        or config.agent.name != "codex"
    ):
        raise PrValidationError("agent must be codex")

    if not _EXACT_STABLE_VERSION_RE.match(proposed.agent.version):
        raise PrValidationError("candidate version must be an exact stable release")
    if Version(proposed.agent.version) <= Version(trusted.agent.version):
        raise PrValidationError("candidate version must be strictly newer than the baseline")

    if proposed.suite_sha256 != suite_sha:
        raise PrValidationError("proposed suite fingerprint does not match trusted suite")
    if proposed.config_sha256 != config_sha:
        raise PrValidationError("proposed config fingerprint does not match trusted config")

    if (
        proposed.model.id != config.model.id
        or proposed.model.snapshot != config.model.snapshot
        or proposed.model.reasoning_effort != config.model.reasoning_effort
    ):
        raise PrValidationError("proposed model pin does not match trusted config")

    expected_canary_ids = {canary.id for canary in canaries}
    if set(proposed.canaries) != expected_canary_ids:
        raise PrValidationError("proposed canary set does not match trusted suite")

    repetitions = config.qualification.repetitions
    for canary in canaries:
        stability = proposed.canaries[canary.id]
        if not (0 <= stability.successes <= stability.valid_runs <= repetitions):
            raise PrValidationError(f"canary {canary.id} has invalid stability counters")
        if canary.critical and (
            stability.valid_runs != repetitions or stability.successes != repetitions
        ):
            raise PrValidationError(f"critical canary {canary.id} is not stable")

    if proposed.qualock_version != qualock.__version__:
        raise PrValidationError("proposed qualock version does not match trusted qualock")

    try:
        datetime.fromisoformat(proposed.created_at)
    except ValueError as error:
        raise PrValidationError("proposed created_at is not a valid timestamp") from error

    candidate_binary = resolver.resolve(proposed.agent.version)
    if candidate_binary.sha256 != proposed.agent.binary_sha256:
        raise PrValidationError("resolved candidate binary does not match proposed lock")

    return CandidateRequest(version=proposed.agent.version, binary_sha256=candidate_binary.sha256)


def _default_resolver() -> CodexResolver:
    return CodexResolver(Path(user_cache_dir("qualock")))


@dataclass(frozen=True)
class PreparePrOutcome:
    context: PullRequestContext
    proposed_lock: bytes | None
    terminal_report: PullRequestReport | None


def prepare_pr(
    root: Path,
    event_path: Path,
    *,
    source: GitHubPrSource,
    producer_run_id: int,
    expected_repository: str,
) -> PreparePrOutcome:
    context = prepare_pr_context(
        event_path,
        source=source,
        producer_run_id=producer_run_id,
        expected_repository=expected_repository,
    )
    if context.classification is PrClassification.NOT_APPLICABLE:
        return PreparePrOutcome(context, None, not_applicable_report(context))
    if context.classification is PrClassification.INVALID_SCOPE:
        return PreparePrOutcome(
            context,
            None,
            incomplete_report(context, reason_codes=(PrReasonCode.INVALID_SCOPE,)),
        )
    try:
        raw = source.read_file_at_ref(
            context.repository_full_name,
            _BASELINE_LOCK_PATH,
            context.head_sha,
            max_bytes=_MAX_PROPOSED_LOCK_BYTES,
        )
    except GitHubSourceError:
        return PreparePrOutcome(
            context,
            None,
            incomplete_report(context, reason_codes=(PrReasonCode.INVALID_PROPOSED_LOCK,)),
        )
    return PreparePrOutcome(context, raw, None)


class CheckExecutor(Protocol):
    def __call__(
        self,
        root: Path,
        candidate_spec: str,
        *,
        resolver: Resolver | None = None,
    ) -> QualificationResult: ...


def qualify_prepared_pr(
    root: Path,
    context: PullRequestContext,
    proposed_lock: bytes,
    *,
    credential_available: bool,
    resolver: Resolver | None = None,
    check_executor: CheckExecutor = execute_check,
) -> PullRequestReport:
    if not credential_available:
        return incomplete_report(
            context,
            reason_codes=(PrReasonCode.CREDENTIAL_UNAVAILABLE,),
            credential_unavailable=True,
        )
    resolver = resolver or _default_resolver()
    try:
        candidate = validate_proposed_lock(root, proposed_lock, resolver=resolver)
        result = check_executor(root, f"codex@{candidate.version}", resolver=resolver)
    except BaselineStaleError:
        return incomplete_report(context, reason_codes=(PrReasonCode.TRUSTED_BASELINE_STALE,))
    except PrValidationError:
        return incomplete_report(context, reason_codes=(PrReasonCode.INVALID_PROPOSED_LOCK,))
    except Exception:  # noqa: BLE001 - producer boundary bounds any qualification/runtime failure
        return incomplete_report(context, reason_codes=(PrReasonCode.QUALIFICATION_FAILED,))
    return report_from_qualification(context, result)
