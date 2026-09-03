from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import qualock
from qualock.agents.base import AgentBinary
from qualock.baseline.io import read_baseline_lock, write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, CanaryStability, ModelPin
from qualock.config.io import write_default_config
from qualock.github_pr import commands
from qualock.github_pr.commands import (
    CandidateRequest,
    PrValidationError,
    prepare_pr,
    qualify_prepared_pr,
    validate_proposed_lock,
)
from qualock.github_pr.models import (
    PrClassification,
    PrReasonCode,
    PrReportVerdict,
    PullRequestContext,
)
from qualock.github_pr.report import read_context as report_read_context
from qualock.github_pr.report import write_context as report_write_context
from qualock.github_pr.report import write_report as report_write_report
from qualock.github_pr.source import GitHubChangedFile, GitHubSourceError
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict

_TRUSTED_VERSION = "0.151.0"
_DEFAULT_CANDIDATE_VERSION = "0.152.0"


class RecordingResolver:
    def __init__(self) -> None:
        self.resolve_calls: list[str] = []

    def resolve(self, version: str) -> AgentBinary:
        self.resolve_calls.append(version)
        return AgentBinary(
            name="codex",
            version=version,
            path=Path(f"/fake/{version}/codex"),
            sha256=f"sha-{version}",
        )


def _write_trusted_project(root: Path) -> None:
    project = root / ".qualock"
    (project / "canaries").mkdir(parents=True)
    (project / "results").mkdir()
    write_default_config(project / "config.yaml")
    grader = project / "canaries/grader.patch"
    grader.write_text("patch", encoding="utf-8")
    (project / "canaries/sample.yaml").write_text(
        f"""schema_version: 1
id: sample
name: Sample
repository:
  url: https://example.invalid/repo.git
  base_sha: {'a' * 40}
runtime:
  image: python:3.12-slim
task: Fix it.
setup: []
agent:
  timeout_seconds: 60
grader:
  patch: grader.patch
  command:
    - pytest -q
constraints:
  protected_paths:
    - tests/**
critical: true
""",
        encoding="utf-8",
    )


@dataclass
class ProjectFixture:
    root: Path
    resolver: RecordingResolver = field(init=False)

    def __post_init__(self) -> None:
        self.resolver = RecordingResolver()
        _write_trusted_project(self.root)
        config, canaries = load_project(self.root)
        trusted = BaselineLock(
            schema_version=1,
            created_at="2026-09-01T00:00:00+00:00",
            agent=AgentPin(name="codex", version=_TRUSTED_VERSION, binary_sha256="trusted-sha"),
            model=ModelPin(
                id=config.model.id,
                snapshot=config.model.snapshot,
                reasoning_effort=config.model.reasoning_effort,
            ),
            qualock_version=qualock.__version__,
            suite_sha256=suite_fingerprint(canaries),
            config_sha256=config_fingerprint(config),
            canaries={"sample": CanaryStability(valid_runs=3, successes=3)},
        )
        write_baseline_lock(project_dir(self.root) / "baseline.lock", trusted)

    def proposed_lock_json(self, **overrides: Any) -> bytes:
        config, canaries = load_project(self.root)
        candidate_version = overrides.get("candidate_version", _DEFAULT_CANDIDATE_VERSION)
        default_canaries = {"sample": CanaryStability(valid_runs=3, successes=3)}
        lock = BaselineLock(
            schema_version=1,
            created_at=overrides.get("created_at", "2026-09-02T00:00:00+00:00"),
            agent=AgentPin(
                name=overrides.get("agent_name", "codex"),
                version=candidate_version,
                binary_sha256=overrides.get("binary_sha256", f"sha-{candidate_version}"),
            ),
            model=ModelPin(
                id=overrides.get("model_id", config.model.id),
                snapshot=overrides.get("model_snapshot", config.model.snapshot),
                reasoning_effort=overrides.get(
                    "model_reasoning_effort", config.model.reasoning_effort
                ),
            ),
            qualock_version=overrides.get("qualock_version", qualock.__version__),
            suite_sha256=overrides.get("suite_sha256", suite_fingerprint(canaries)),
            config_sha256=overrides.get("config_sha256", config_fingerprint(config)),
            canaries=overrides.get("canaries", default_canaries),
        )
        return lock.model_dump_json().encode("utf-8")


@pytest.fixture
def project_fixture(tmp_path: Path) -> ProjectFixture:
    return ProjectFixture(root=tmp_path)


# --- proposed-lock validation -----------------------------------------------


@pytest.mark.parametrize(
    "candidate_version",
    ["latest", "0.152.0-beta.1", "0.152.0+build.1", "0.151.0", "0.150.0"],
)
def test_non_stable_or_not_newer_candidate_is_rejected(
    project_fixture: ProjectFixture, candidate_version: str
) -> None:
    raw = project_fixture.proposed_lock_json(candidate_version=candidate_version)
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_wrong_model_pin_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(model_id="a-different-model")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_missing_canary_id_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(canaries={})
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_extra_canary_id_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={
            "sample": CanaryStability(valid_runs=3, successes=3),
            "extra": CanaryStability(valid_runs=3, successes=3),
        }
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_successes_exceeds_valid_runs_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=2, successes=3)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_counts_above_configured_repetitions_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=4, successes=4)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_unstable_critical_canary_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=3, successes=2)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_unparseable_created_at_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(created_at="not-a-timestamp")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []


def test_candidate_binary_sha_mismatch_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(binary_sha256="wrong-sha")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == [_DEFAULT_CANDIDATE_VERSION]


def test_valid_proposed_lock_is_accepted(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json()
    candidate = validate_proposed_lock(
        project_fixture.root, raw, resolver=project_fixture.resolver
    )
    assert candidate == CandidateRequest(
        version=_DEFAULT_CANDIDATE_VERSION, binary_sha256=f"sha-{_DEFAULT_CANDIDATE_VERSION}"
    )
    assert project_fixture.resolver.resolve_calls == [_DEFAULT_CANDIDATE_VERSION]


# --- producer orchestration --------------------------------------------------


class FakeSource:
    def __init__(
        self,
        *,
        read_result: bytes | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self._read_result = read_result
        self._read_error = read_error
        self.read_calls: list[tuple[str, str, str, int]] = []

    def get_pull_request(self, repository: str, pr_number: int) -> object:
        raise AssertionError("get_pull_request must not be called directly by prepare_pr")

    def list_changed_files(
        self, repository: str, pr_number: int, *, expected_count: int
    ) -> tuple[GitHubChangedFile, ...]:
        raise AssertionError("list_changed_files must not be called directly by prepare_pr")

    def read_file_at_ref(
        self, repository: str, path: str, ref: str, *, max_bytes: int
    ) -> bytes:
        self.read_calls.append((repository, path, ref, max_bytes))
        if self._read_error is not None:
            raise self._read_error
        assert self._read_result is not None
        return self._read_result


def _context(classification: PrClassification, **overrides: Any) -> PullRequestContext:
    fields: dict[str, Any] = {
        "repository_id": 123,
        "repository_full_name": "owner/repo",
        "pr_number": 7,
        "pr_author_login": "author",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "producer_run_id": 1,
        "changed_paths": (".qualock/baseline.lock",),
        "classification": classification,
    }
    fields.update(overrides)
    return PullRequestContext(**fields)


def fail_check_executor(
    root: Path, candidate_spec: str, *, resolver: Any = None
) -> QualificationResult:
    raise AssertionError(f"check must not run: {candidate_spec}")


def _qualification_result(verdict: Verdict) -> QualificationResult:
    return QualificationResult(
        qualification_id="qual-1",
        baseline_version=_TRUSTED_VERSION,
        candidate_version=_DEFAULT_CANDIDATE_VERSION,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
    )


def test_not_applicable_prepare_never_reads_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.NOT_APPLICABLE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.NOT_APPLICABLE
    assert source.read_calls == []


def test_invalid_scope_prepare_never_reads_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.INVALID_SCOPE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_SCOPE in outcome.terminal_report.reason_codes
    assert source.read_calls == []


def test_upgrade_prepare_returns_proposed_lock_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource(read_result=b"proposed-lock-bytes")

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.proposed_lock == b"proposed-lock-bytes"
    assert outcome.terminal_report is None
    assert source.read_calls == [
        ("owner/repo", ".qualock/baseline.lock", "b" * 40, 131_072)
    ]


def test_upgrade_prepare_fixed_file_read_failure_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource(read_error=GitHubSourceError("missing at fixed ref"))

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_PROPOSED_LOCK in outcome.terminal_report.reason_codes


def test_qualify_calls_check_executor_exactly_once_with_trusted_root_and_candidate(
    project_fixture: ProjectFixture,
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()
    calls: list[tuple[Path, str, Any]] = []

    def check_executor(
        root: Path, candidate_spec: str, *, resolver: Any = None
    ) -> QualificationResult:
        calls.append((root, candidate_spec, resolver))
        return _qualification_result(Verdict.PASS)

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=check_executor,
    )

    assert calls == [
        (project_fixture.root, f"codex@{_DEFAULT_CANDIDATE_VERSION}", project_fixture.resolver)
    ]
    assert report.verdict is PrReportVerdict.PASS


@pytest.mark.parametrize(
    ("verdict", "report_verdict"),
    [
        (Verdict.PASS, PrReportVerdict.PASS),
        (Verdict.WARN, PrReportVerdict.WARN),
        (Verdict.BLOCK, PrReportVerdict.BLOCK),
        (Verdict.INCOMPLETE, PrReportVerdict.INCOMPLETE),
    ],
)
def test_qualification_verdicts_are_copied_unchanged(
    project_fixture: ProjectFixture, verdict: Verdict, report_verdict: PrReportVerdict
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=lambda root, spec, *, resolver=None: _qualification_result(verdict),
    )

    assert report.verdict is report_verdict


def test_missing_credential_is_incomplete_without_check(
    project_fixture: ProjectFixture,
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=False,
        resolver=project_fixture.resolver,
        check_executor=fail_check_executor,
    )

    assert report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.CREDENTIAL_UNAVAILABLE in report.reason_codes
    assert report.credential_unavailable is True
    assert project_fixture.resolver.resolve_calls == []


def test_stale_trusted_baseline_is_incomplete_without_candidate_resolve(
    project_fixture: ProjectFixture,
) -> None:
    trusted = read_baseline_lock(project_dir(project_fixture.root) / "baseline.lock")
    stale = trusted.model_copy(update={"suite_sha256": "stale-suite-sha"})
    write_baseline_lock(project_dir(project_fixture.root) / "baseline.lock", stale)
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=fail_check_executor,
    )

    assert report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.TRUSTED_BASELINE_STALE in report.reason_codes
    assert project_fixture.resolver.resolve_calls == []


def test_invalid_proposed_lock_is_incomplete_without_check(
    project_fixture: ProjectFixture,
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json(model_id="a-different-model")

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=fail_check_executor,
    )

    assert report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_PROPOSED_LOCK in report.reason_codes


def test_check_executor_exception_is_incomplete_without_fabricated_id(
    project_fixture: ProjectFixture,
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()

    def check_executor(
        root: Path, candidate_spec: str, *, resolver: Any = None
    ) -> QualificationResult:
        raise RuntimeError("runtime blew up")

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=check_executor,
    )

    assert report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.QUALIFICATION_FAILED in report.reason_codes
    assert report.qualification_completed is False


# --- artifact write failure propagation --------------------------------------


def test_report_write_failure_propagates_and_context_remains_truthful(
    project_fixture: ProjectFixture, tmp_path: Path
) -> None:
    context = _context(PrClassification.UPGRADE)
    raw = project_fixture.proposed_lock_json()

    def check_executor(
        root: Path, candidate_spec: str, *, resolver: Any = None
    ) -> QualificationResult:
        return _qualification_result(Verdict.PASS)

    report = qualify_prepared_pr(
        project_fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=project_fixture.resolver,
        check_executor=check_executor,
    )

    context_path = tmp_path / "context.json"
    report_write_context(context_path, context)

    report_output_path = tmp_path / "report.json"
    report_output_path.mkdir()

    with pytest.raises(OSError):
        report_write_report(report_output_path, report)

    assert report_read_context(context_path) == context
