from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

import qualock
from qualock.agents.base import AgentBinary
from qualock.baseline.io import BaselineStaleError, read_baseline_lock, write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, CanaryStability, ModelPin
from qualock.config.models import AgentConfig, QualockConfig
from qualock.github_pr import commands
from qualock.github_pr.commands import (
    CandidateRequest,
    PrValidationError,
    UnsupportedPrAgentError,
    _trusted_pr_agent,
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


def _write_trusted_project(root: Path, *, agent: str = "codex") -> None:
    project = root / ".qualock"
    (project / "canaries").mkdir(parents=True)
    (project / "results").mkdir()
    config = QualockConfig(agent=AgentConfig(name=agent))
    (project / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
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


def _build_trusted_lock(root: Path, *, agent: str, version: str, sha: str = "b" * 64) -> BaselineLock:
    config, canaries = load_project(root)
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-01T00:00:00+00:00",
        agent=AgentPin(name=agent, version=version, binary_sha256=sha),
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


def _setup_trusted_project(
    root: Path,
    *,
    config_agent: str,
    baseline_agent: str,
    trusted_version: str = _TRUSTED_VERSION,
) -> None:
    _write_trusted_project(root, agent=config_agent)
    lock = _build_trusted_lock(root, agent=baseline_agent, version=trusted_version)
    write_baseline_lock(project_dir(root) / "baseline.lock", lock)


@dataclass
class ProjectFixture:
    root: Path
    agent: str = "codex"
    trusted_version: str = _TRUSTED_VERSION
    candidate_version: str = _DEFAULT_CANDIDATE_VERSION
    resolver: RecordingResolver = field(init=False)

    def __post_init__(self) -> None:
        self.resolver = RecordingResolver()
        _setup_trusted_project(
            self.root,
            config_agent=self.agent,
            baseline_agent=self.agent,
            trusted_version=self.trusted_version,
        )

    def proposed_lock_json(self, **overrides: Any) -> bytes:
        config, canaries = load_project(self.root)
        candidate_version = overrides.get("candidate_version", self.candidate_version)
        default_canaries = {"sample": CanaryStability(valid_runs=3, successes=3)}
        lock = BaselineLock(
            schema_version=1,
            created_at=overrides.get("created_at", "2026-09-02T00:00:00+00:00"),
            agent=AgentPin(
                name=overrides.get("agent_name", self.agent),
                version=candidate_version,
                binary_sha256=overrides.get("binary_sha256", "a" * 64),
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


@pytest.fixture
def claude_project_fixture(tmp_path: Path) -> ProjectFixture:
    return ProjectFixture(
        root=tmp_path, agent="claude", trusted_version="2.1.200", candidate_version="2.1.263"
    )


# --- trusted-agent preflight --------------------------------------------------


def test_trusted_pr_agent_returns_codex(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    assert _trusted_pr_agent(tmp_path) == "codex"


def test_trusted_pr_agent_returns_claude(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="claude", baseline_agent="claude")
    assert _trusted_pr_agent(tmp_path) == "claude"


def test_trusted_pr_agent_rejects_config_baseline_mismatch(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="claude", baseline_agent="codex")
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_stale_fingerprint(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    trusted = read_baseline_lock(project_dir(tmp_path) / "baseline.lock")
    stale = trusted.model_copy(update={"suite_sha256": "stale-suite-sha"})
    write_baseline_lock(project_dir(tmp_path) / "baseline.lock", stale)
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_antigravity(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="antigravity", baseline_agent="antigravity")
    with pytest.raises(UnsupportedPrAgentError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_missing_project(tmp_path: Path) -> None:
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_corrupt_config(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    (project_dir(tmp_path) / "config.yaml").write_text("not: [valid, yaml", encoding="utf-8")
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_missing_baseline(tmp_path: Path) -> None:
    _write_trusted_project(tmp_path, agent="codex")
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_corrupt_baseline(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    (project_dir(tmp_path) / "baseline.lock").write_text("not-json-at-all", encoding="utf-8")
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


def test_trusted_pr_agent_rejects_invalid_trusted_version(tmp_path: Path) -> None:
    _write_trusted_project(tmp_path, agent="codex")
    lock = _build_trusted_lock(tmp_path, agent="codex", version="not-a-version")
    write_baseline_lock(project_dir(tmp_path) / "baseline.lock", lock)
    with pytest.raises(BaselineStaleError):
        _trusted_pr_agent(tmp_path)


# --- proposed-lock validation -----------------------------------------------


def test_validate_proposed_lock_has_no_resolver_parameter() -> None:
    assert "resolver" not in inspect.signature(validate_proposed_lock).parameters


def test_validate_proposed_lock_rejects_stale_before_parsing_proposed(tmp_path: Path) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    trusted = read_baseline_lock(project_dir(tmp_path) / "baseline.lock")
    stale = trusted.model_copy(update={"suite_sha256": "stale-suite-sha"})
    write_baseline_lock(project_dir(tmp_path) / "baseline.lock", stale)

    with pytest.raises(BaselineStaleError):
        validate_proposed_lock(tmp_path, b"not-json-at-all")


def test_validate_proposed_lock_rejects_antigravity_before_parsing_proposed(
    tmp_path: Path,
) -> None:
    _setup_trusted_project(tmp_path, config_agent="antigravity", baseline_agent="antigravity")

    with pytest.raises(UnsupportedPrAgentError):
        validate_proposed_lock(tmp_path, b"not-json-at-all")


@pytest.mark.parametrize(
    "candidate_version",
    ["latest", "0.152.0-beta.1", "0.152.0+build.1", "0.151.0", "0.150.0"],
)
def test_non_stable_or_not_newer_candidate_is_rejected(
    project_fixture: ProjectFixture, candidate_version: str
) -> None:
    raw = project_fixture.proposed_lock_json(candidate_version=candidate_version)
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_cross_agent_candidate_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(agent_name="claude")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_wrong_model_pin_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(model_id="a-different-model")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_missing_canary_id_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(canaries={})
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_extra_canary_id_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={
            "sample": CanaryStability(valid_runs=3, successes=3),
            "extra": CanaryStability(valid_runs=3, successes=3),
        }
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_successes_exceeds_valid_runs_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=2, successes=3)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_counts_above_configured_repetitions_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=4, successes=4)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_unstable_critical_canary_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(
        canaries={"sample": CanaryStability(valid_runs=3, successes=2)}
    )
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_unparseable_created_at_is_rejected(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json(created_at="not-a-timestamp")
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


@pytest.mark.parametrize(
    "binary_sha256",
    ["A" * 64, "g" * 64, "a" * 63, "a" * 65, "not-a-sha-at-all", "sha-0.152.0"],
)
def test_invalid_binary_sha_format_is_rejected(
    project_fixture: ProjectFixture, binary_sha256: str
) -> None:
    raw = project_fixture.proposed_lock_json(binary_sha256=binary_sha256)
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw)


def test_valid_proposed_lock_is_accepted(project_fixture: ProjectFixture) -> None:
    raw = project_fixture.proposed_lock_json()
    candidate = validate_proposed_lock(project_fixture.root, raw)
    assert candidate == CandidateRequest(
        agent_name="codex",
        version=_DEFAULT_CANDIDATE_VERSION,
        binary_sha256="a" * 64,
    )


def test_valid_claude_proposed_lock_is_accepted(tmp_path: Path) -> None:
    _setup_trusted_project(
        tmp_path, config_agent="claude", baseline_agent="claude", trusted_version="2.1.200"
    )
    config, canaries = load_project(tmp_path)
    raw = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name="claude", version="2.1.263", binary_sha256="a" * 64),
        model=ModelPin(
            id=config.model.id,
            snapshot=config.model.snapshot,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version=qualock.__version__,
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={"sample": CanaryStability(valid_runs=3, successes=3)},
    ).model_dump_json().encode("utf-8")

    candidate = validate_proposed_lock(tmp_path, raw)
    assert candidate == CandidateRequest(
        agent_name="claude",
        version="2.1.263",
        binary_sha256="a" * 64,
    )


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
    trusted_agent_calls: list[Path] = []
    monkeypatch.setattr(
        commands, "_trusted_pr_agent", lambda root: trusted_agent_calls.append(root)
    )
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.NOT_APPLICABLE
    assert source.read_calls == []
    assert trusted_agent_calls == []


def test_invalid_scope_prepare_never_reads_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.INVALID_SCOPE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    trusted_agent_calls: list[Path] = []
    monkeypatch.setattr(
        commands, "_trusted_pr_agent", lambda root: trusted_agent_calls.append(root)
    )
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context == context
    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_SCOPE in outcome.terminal_report.reason_codes
    assert source.read_calls == []
    assert trusted_agent_calls == []


def test_codex_upgrade_prepare_returns_agent_and_proposed_bytes(
    monkeypatch: pytest.MonkeyPatch, project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    raw = project_fixture.proposed_lock_json()
    source = FakeSource(read_result=raw)

    outcome = prepare_pr(
        project_fixture.root,
        project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent == "codex"
    assert outcome.proposed_lock == raw
    assert outcome.terminal_report is None
    assert source.read_calls == [("owner/repo", ".qualock/baseline.lock", "b" * 40, 131_072)]


def test_claude_upgrade_prepare_returns_agent_and_proposed_bytes(
    monkeypatch: pytest.MonkeyPatch, claude_project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    raw = claude_project_fixture.proposed_lock_json()
    source = FakeSource(read_result=raw)

    outcome = prepare_pr(
        claude_project_fixture.root,
        claude_project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent == "claude"
    assert outcome.proposed_lock == raw
    assert outcome.terminal_report is None


def test_config_baseline_mismatch_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_trusted_project(tmp_path, config_agent="claude", baseline_agent="codex")
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_stale_fingerprint_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    trusted = read_baseline_lock(project_dir(tmp_path) / "baseline.lock")
    stale = trusted.model_copy(update={"suite_sha256": "stale-suite-sha"})
    write_baseline_lock(project_dir(tmp_path) / "baseline.lock", stale)
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_antigravity_prepare_is_unsupported_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_trusted_project(tmp_path, config_agent="antigravity", baseline_agent="antigravity")
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.UNSUPPORTED_AGENT in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_missing_project_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_missing_baseline_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_trusted_project(tmp_path, agent="codex")
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_corrupt_config_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    (project_dir(tmp_path) / "config.yaml").write_text("not: [valid, yaml", encoding="utf-8")
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_corrupt_baseline_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_trusted_project(tmp_path, config_agent="codex", baseline_agent="codex")
    (project_dir(tmp_path) / "baseline.lock").write_text("not-json-at-all", encoding="utf-8")
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_invalid_trusted_version_prepare_is_stale_before_proposed_head_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_trusted_project(tmp_path, agent="codex")
    lock = _build_trusted_lock(tmp_path, agent="codex", version="not-a-version")
    write_baseline_lock(project_dir(tmp_path) / "baseline.lock", lock)
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource()

    outcome = prepare_pr(
        tmp_path,
        tmp_path / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent is None
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent is None
    assert source.read_calls == []


def test_prepare_preserves_stale_reason_on_second_validation_pass(
    monkeypatch: pytest.MonkeyPatch, project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    raw = project_fixture.proposed_lock_json()
    source = FakeSource(read_result=raw)

    real_trusted_pr_agent = commands._trusted_pr_agent
    calls = {"n": 0}

    def flaky(root: Path) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_trusted_pr_agent(root)
        raise commands.BaselineStaleError("re-observed staleness on second pass")

    monkeypatch.setattr(commands, "_trusted_pr_agent", flaky)

    outcome = prepare_pr(
        project_fixture.root,
        project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.TRUSTED_BASELINE_STALE in outcome.terminal_report.reason_codes
    assert PrReasonCode.INVALID_PROPOSED_LOCK not in outcome.terminal_report.reason_codes


def test_prepare_preserves_unsupported_reason_on_second_validation_pass(
    monkeypatch: pytest.MonkeyPatch, project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    raw = project_fixture.proposed_lock_json()
    source = FakeSource(read_result=raw)

    real_trusted_pr_agent = commands._trusted_pr_agent
    calls = {"n": 0}

    def flaky(root: Path) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_trusted_pr_agent(root)
        raise commands.UnsupportedPrAgentError("re-observed unsupported agent on second pass")

    monkeypatch.setattr(commands, "_trusted_pr_agent", flaky)

    outcome = prepare_pr(
        project_fixture.root,
        project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.UNSUPPORTED_AGENT in outcome.terminal_report.reason_codes
    assert PrReasonCode.INVALID_PROPOSED_LOCK not in outcome.terminal_report.reason_codes


def test_invalid_proposed_lock_prepare_preserves_established_agent(
    monkeypatch: pytest.MonkeyPatch, project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    raw = project_fixture.proposed_lock_json(agent_name="claude")
    source = FakeSource(read_result=raw)

    outcome = prepare_pr(
        project_fixture.root,
        project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent == "codex"
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_PROPOSED_LOCK in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent == "codex"


def test_upgrade_prepare_fixed_file_read_failure_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, project_fixture: ProjectFixture
) -> None:
    context = _context(PrClassification.UPGRADE)
    monkeypatch.setattr(commands, "prepare_pr_context", lambda *a, **kw: context)
    source = FakeSource(read_error=GitHubSourceError("missing at fixed ref"))

    outcome = prepare_pr(
        project_fixture.root,
        project_fixture.root / "event.json",
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )

    assert outcome.context.agent == "codex"
    assert outcome.proposed_lock is None
    assert outcome.terminal_report is not None
    assert outcome.terminal_report.verdict is PrReportVerdict.INCOMPLETE
    assert PrReasonCode.INVALID_PROPOSED_LOCK in outcome.terminal_report.reason_codes
    assert outcome.terminal_report.agent == "codex"


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
    assert report.qualification_id is None


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
