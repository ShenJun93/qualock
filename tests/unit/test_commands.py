import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

import qualock.commands as commands_module
from qualock.agents.antigravity import AntigravityAdapter
from qualock.agents.antigravity_resolver import AntigravityResolver
from qualock.agents.base import AgentBinary
from qualock.baseline.io import read_baseline_lock, write_baseline_lock
from qualock.canary.loader import CanaryLoadError
from qualock.commands import (
    BaselineUnstableError,
    CommandError,
    _default_backend,
    _default_resolver,
    agent_display_name,
    execute_baseline,
    execute_check,
    execute_history,
    parse_agent_spec,
)
from qualock.config.io import ConfigError, write_default_config
from qualock.history.models import HistoryAnalysis, HistorySummary, SuiteEstimate
from qualock.project import load_project
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.host import LinuxHostRunner
from qualock.run.host_backend import LinuxHostQualificationBackend
from qualock.run.models import PreparedTarget
from qualock.run.schedule import Side


class FakeResolver:
    def __init__(self, agent_name: str = "codex") -> None:
        self.agent_name = agent_name
        self.calls: list[str] = []

    def resolve(self, version: str) -> AgentBinary:
        self.calls.append(version)
        exact = "0.151.0" if version == "latest" else version
        return AgentBinary(
            self.agent_name,
            exact,
            Path(f"/fake/{self.agent_name}/{exact}/agent"),
            f"sha-{exact}",
        )


class FakeBackend:
    def __init__(self, success_versions: set[str] | None = None) -> None:
        self.success_versions = success_versions or {"0.150.0"}
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def prepare(self, canary, qualification_id: str) -> PreparedTarget:
        self.prepared.append(canary.id)
        return PreparedTarget(reference="prepared", digest=f"sha256:{canary.id}")

    def run_attempt(self, *, canary, prepared, binary, side: Side, repetition: int) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=binary.version in self.success_versions,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=1, observed=True),
        )


def setup_project(root: Path, *, agent_name: str = "codex", model_id: str | None = None) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    config_path = ub / "config.yaml"
    write_default_config(config_path)
    if agent_name != "codex" or model_id is not None:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        payload["agent"]["name"] = agent_name
        if model_id is not None:
            payload["model"]["id"] = model_id
            payload["model"]["snapshot"] = None
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    grader = ub / "canaries/grader.patch"
    grader.write_text("patch", encoding="utf-8")
    (ub / "canaries/sample.yaml").write_text(
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


def test_agent_display_name_is_shared_and_fail_closed() -> None:
    assert agent_display_name("codex") == "Codex"
    assert agent_display_name("claude") == "Claude Code"
    with pytest.raises(CommandError, match="unsupported agent"):
        agent_display_name("future-agent")


def test_parse_agent_spec_accepts_codex_and_claude() -> None:
    assert parse_agent_spec("codex@0.150.0") == ("codex", "0.150.0")
    assert parse_agent_spec("claude@2.1.260") == ("claude", "2.1.260")


def test_parse_agent_spec_accepts_antigravity() -> None:
    assert parse_agent_spec("antigravity@1.1.27") == ("antigravity", "1.1.27")


def test_antigravity_display_name() -> None:
    assert agent_display_name("antigravity") == "Antigravity"


def test_default_resolver_antigravity_uses_path_lookup_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUALOCK_ANTIGRAVITY_BIN", raising=False)

    resolver = _default_resolver("antigravity")

    assert isinstance(resolver, AntigravityResolver)
    assert resolver.binary_path is None


def test_default_resolver_antigravity_uses_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "agy"
    monkeypatch.setenv("QUALOCK_ANTIGRAVITY_BIN", str(override))

    resolver = _default_resolver("antigravity")

    assert isinstance(resolver, AntigravityResolver)
    assert resolver.binary_path == override


def test_default_resolver_uses_shared_cache_root_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands_module, "default_agent_cache_root", lambda: tmp_path)

    resolver = _default_resolver("codex")

    assert resolver.cache_root == tmp_path


def test_default_backend_antigravity_uses_linux_host_runner(tmp_path: Path) -> None:
    setup_project(tmp_path, agent_name="antigravity")
    config, _ = load_project(tmp_path)

    backend = _default_backend(tmp_path, config, "antigravity")

    assert isinstance(backend, LinuxHostQualificationBackend)
    assert isinstance(backend.host_runner, LinuxHostRunner)
    assert isinstance(backend.agent_adapter, AntigravityAdapter)
    assert backend.agent_adapter.auth_app_data == Path.home() / ".gemini" / "antigravity-cli"


def test_default_backend_codex_and_claude_still_use_docker(tmp_path: Path) -> None:
    from qualock.run.backend import DockerQualificationBackend

    setup_project(tmp_path, agent_name="codex")
    config, _ = load_project(tmp_path)
    backend = _default_backend(tmp_path, config, "codex")
    assert isinstance(backend, DockerQualificationBackend)


def test_default_claude_backend_requires_explicit_automation_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    config, _ = load_project(tmp_path)
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CommandError, match="Claude qualification requires an automation credential"):
        _default_backend(tmp_path, config, "claude")


def test_default_claude_backend_uses_documented_credential_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    config, _ = load_project(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer")

    backend = _default_backend(tmp_path, config, "claude")

    assert getattr(backend.agent_adapter, "automation_credential", None) == (
        "ANTHROPIC_AUTH_TOKEN",
        "bearer",
    )


def test_baseline_writes_known_good_behavior_lock(tmp_path: Path) -> None:
    setup_project(tmp_path)
    lock = execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=FakeResolver(),
        backend=FakeBackend(),
        qualification_id="baseline-q",
        created_at="2026-08-31T00:00:00Z",
    )
    assert lock.agent.name == "codex"
    assert lock.agent.version == "0.150.0"
    assert lock.canaries["sample"].valid_runs == 3
    assert lock.canaries["sample"].successes == 3
    assert (tmp_path / ".qualock/baseline.lock").is_file()


def test_claude_baseline_pins_claude_agent(tmp_path: Path) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    lock = execute_baseline(
        tmp_path,
        "claude@2.1.260",
        resolver=FakeResolver("claude"),
        backend=FakeBackend({"2.1.260"}),
        qualification_id="claude-baseline",
        created_at="2026-09-04T00:00:00Z",
    )

    assert lock.agent.name == "claude"
    assert lock.agent.version == "2.1.260"
    assert lock.model.id == "sonnet"


def test_baseline_agent_must_match_config_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="config agent codex does not match requested agent claude"):
        execute_baseline(
            tmp_path,
            "claude@2.1.260",
            resolver=resolver,
            backend=FakeBackend({"2.1.260"}),
        )

    assert resolver.calls == []


def test_unstable_baseline_persists_attempt_evidence(tmp_path: Path) -> None:
    setup_project(tmp_path)
    with pytest.raises(BaselineUnstableError):
        execute_baseline(
            tmp_path,
            "codex@0.151.0",
            resolver=FakeResolver(),
            backend=FakeBackend(),
            qualification_id="baseline-fail",
            created_at="2026-08-31T00:00:00Z",
        )

    evidence_path = tmp_path / ".qualock/results/baseline-fail/baseline.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["qualification_id"] == "baseline-fail"
    assert payload["baseline_version"] == "0.151.0"
    assert len(payload["canaries"]["sample"]) == 3
    assert payload["canaries"]["sample"][0]["success"] is False
    assert payload["canaries"]["sample"][0]["valid"] is True
    assert payload["canaries"]["sample"][0]["usage"]["cache_write_input_tokens"] == 0
    assert payload["canaries"]["sample"][0]["usage"]["observed"] is True


def test_check_reruns_pinned_baseline_and_candidate_and_writes_report(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-q",
        created_at="2026-08-31T00:00:00Z",
    )
    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-q",
    )
    assert result.verdict is Verdict.BLOCK
    assert result.baseline_version == "0.150.0"
    assert result.candidate_version == "0.151.0"
    assert (tmp_path / ".qualock/results/check-q/report.json").is_file()


def test_check_forwards_attempt_budget_and_writes_incomplete_report(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    baseline_backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=baseline_backend,
        qualification_id="baseline-budget",
        created_at="2026-09-05T00:00:00Z",
    )
    check_backend = FakeBackend()

    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=check_backend,
        qualification_id="check-budget",
        max_attempts=5,
    )

    assert check_backend.prepared == []
    assert check_backend.calls == []
    assert result.verdict is Verdict.INCOMPLETE
    payload = json.loads(
        (tmp_path / ".qualock/results/check-budget/report.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["verdict"] == "incomplete"
    assert payload["executions"][0]["attempts"] == []
    assert payload["executions"][0]["prepared_image_digest"] == ""
    assert "max_attempts=5" in payload["executions"][0]["reason"]

    artifact_root = tmp_path / ".qualock/results/check-budget"
    assert (artifact_root / "report.md").is_file()
    assert (artifact_root / "report.json").is_file()
    qualification = json.loads(
        (artifact_root / "qualification.json").read_text(encoding="utf-8")
    )
    assert set(qualification) == {
        "qualification_id",
        "baseline_version",
        "candidate_version",
        "run_order",
        "verdict",
        "max_attempts",
        "max_tokens",
        "attempts_used",
        "observed_tokens",
    }
    assert qualification["run_order"] == []
    assert qualification["verdict"] == "incomplete"
    assert qualification["max_attempts"] == 5
    assert qualification["max_tokens"] is None
    assert qualification["attempts_used"] == 0
    assert qualification["observed_tokens"] == 0


def test_check_rejects_nonpositive_attempt_budget_before_resolution(tmp_path: Path) -> None:
    resolver = FakeResolver()
    backend = FakeBackend()

    for bad in (0, -1):
        with pytest.raises(CommandError, match="max attempts must be greater than zero"):
            execute_check(
                tmp_path,
                "codex@0.151.0",
                resolver=resolver,
                backend=backend,
                max_attempts=bad,
            )

    assert resolver.calls == []
    assert backend.prepared == []
    assert backend.calls == []


def test_check_rejects_nonpositive_token_budget_before_resolution(tmp_path: Path) -> None:
    resolver = FakeResolver()
    backend = FakeBackend()

    for bad in (0, -5):
        with pytest.raises(CommandError, match="max tokens must be greater than zero"):
            execute_check(
                tmp_path,
                "codex@0.151.0",
                resolver=resolver,
                backend=backend,
                max_tokens=bad,
            )

    assert resolver.calls == []
    assert backend.prepared == []
    assert backend.calls == []


def test_check_forwards_both_budgets_to_executor_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    baseline_backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=baseline_backend,
        qualification_id="baseline-both",
        created_at="2026-09-05T00:00:00Z",
    )
    check_backend = FakeBackend()

    captured: dict[str, object] = {}
    real_run = commands_module.QualificationExecutor.run

    def fake_run(self, *args, **kwargs):
        captured["max_attempts"] = kwargs.get("max_attempts")
        captured["max_tokens"] = kwargs.get("max_tokens")
        return real_run(self, *args, **kwargs)

    monkeypatch.setattr(commands_module.QualificationExecutor, "run", fake_run)

    execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=check_backend,
        qualification_id="check-both",
        max_attempts=5,
        max_tokens=1000,
    )

    assert captured == {"max_attempts": 5, "max_tokens": 1000}


def test_check_candidate_agent_must_match_config_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="config agent codex does not match requested agent claude"):
        execute_check(tmp_path, "claude@2.1.260", resolver=resolver, backend=FakeBackend())

    assert resolver.calls == []


def test_check_baseline_agent_must_match_candidate_before_resolution(tmp_path: Path) -> None:
    setup_project(tmp_path, agent_name="claude", model_id="sonnet")
    execute_baseline(
        tmp_path,
        "claude@2.1.260",
        resolver=FakeResolver("claude"),
        backend=FakeBackend({"2.1.260"}),
        qualification_id="claude-baseline",
        created_at="2026-09-04T00:00:00Z",
    )
    lock_path = tmp_path / ".qualock/baseline.lock"
    lock = read_baseline_lock(lock_path)
    write_baseline_lock(
        lock_path,
        lock.model_copy(update={"agent": lock.agent.model_copy(update={"name": "codex"})}),
    )
    resolver = FakeResolver("claude")

    with pytest.raises(CommandError, match="baseline agent codex does not match candidate agent claude"):
        execute_check(
            tmp_path,
            "claude@2.1.261",
            resolver=resolver,
            backend=FakeBackend({"2.1.260"}),
        )

    assert resolver.calls == []


def test_execute_check_captures_window_around_run_and_after_canonical_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-pricing-window",
        created_at="2026-09-08T00:00:00Z",
    )

    write_time_holder: dict[str, datetime] = {}
    real_write = commands_module.write_qualification_artifacts

    def spy_write(*args, **kwargs):
        write_time_holder["at"] = commands_module.datetime.now(commands_module.UTC)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(commands_module, "write_qualification_artifacts", spy_write)

    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-pricing-window",
    )

    pricing_path = tmp_path / ".qualock/results/check-pricing-window/pricing.json"
    payload = json.loads(pricing_path.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(payload["run_started_at"])
    finished = datetime.fromisoformat(payload["run_finished_at"])
    assert started <= write_time_holder["at"] <= finished
    assert result.verdict is Verdict.BLOCK


def test_check_pricing_writer_failure_is_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_project(tmp_path)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-pricing-failure",
        created_at="2026-09-08T00:00:00Z",
    )

    def fail_write(_directory: Path, _payload: dict[str, object]) -> Path:
        raise OSError("sensitive writer detail")

    monkeypatch.setattr(commands_module, "write_pricing_sidecar", fail_write)
    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-pricing-failure",
    )
    artifact_root = tmp_path / ".qualock/results/check-pricing-failure"
    assert result.verdict is Verdict.BLOCK
    assert {path.name for path in artifact_root.iterdir()} == {
        "report.md",
        "report.json",
        "qualification.json",
    }


def test_unknown_model_check_writes_unavailable_sidecar(tmp_path: Path) -> None:
    setup_project(tmp_path, model_id="gpt-5.6-turbo")
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-unknown-model",
        created_at="2026-09-08T00:00:00Z",
    )

    execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-unknown-model",
    )

    payload = json.loads(
        (tmp_path / ".qualock/results/check-unknown-model/pricing.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["availability"] == "unavailable"
    assert payload["unavailable_reason"] == "unknown_model"


def test_existing_sidecar_failure_cannot_change_check_result(tmp_path: Path) -> None:
    setup_project(tmp_path)
    config, _ = load_project(tmp_path)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-existing-sidecar",
        created_at="2026-09-08T00:00:00Z",
    )
    result = execute_check(
        tmp_path,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-existing-sidecar-source",
    )

    qualification_dir = tmp_path / ".qualock/results/existing-sidecar-target"
    qualification_dir.mkdir(parents=True)
    existing = qualification_dir / "pricing.json"
    existing.write_bytes(b"pinned")

    commands_module._write_pricing_sidecar_best_effort(
        qualification_dir,
        config,
        result,
        datetime.now(UTC),
        datetime.now(UTC),
    )

    assert existing.read_bytes() == b"pinned"
    assert result.verdict is Verdict.BLOCK


def test_standalone_baseline_does_not_write_pricing_sidecar(tmp_path: Path) -> None:
    setup_project(tmp_path)
    execute_baseline(
        tmp_path,
        "codex@0.150.0",
        resolver=FakeResolver(),
        backend=FakeBackend(),
        qualification_id="baseline-no-pricing",
        created_at="2026-09-08T00:00:00Z",
    )

    baseline_dir = tmp_path / ".qualock/results/baseline-no-pricing"
    assert {path.name for path in baseline_dir.iterdir()} == {"baseline.json"}


def test_execute_history_uses_current_canaries_in_config_order(tmp_path: Path, monkeypatch) -> None:
    setup_project(tmp_path)
    seen: dict[str, object] = {}
    summary = HistorySummary(loaded=(), ignored=())
    expected = HistoryAnalysis(
        loaded_reports=0, ignored_reports=(), ranked=(), not_enough_history=(),
        per_canary_estimates=(), suite_estimate=SuiteEstimate(None, None, (), ()),
    )

    def fake_scan(path: Path) -> HistorySummary:
        seen["path"] = path
        return summary

    def fake_analyze(value: HistorySummary, ids: Sequence[str]) -> HistoryAnalysis:
        seen["summary"] = value
        seen["ids"] = tuple(ids)
        return expected

    monkeypatch.setattr(commands_module, "scan_results", fake_scan)
    monkeypatch.setattr(commands_module, "analyze_history", fake_analyze)
    assert execute_history(tmp_path) is expected
    assert seen["path"] == tmp_path.resolve() / ".qualock/results"
    assert seen["summary"] is summary
    assert seen["ids"] == ("sample",)


def test_execute_history_rejects_empty_canary_suite(tmp_path: Path) -> None:
    ub = tmp_path / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    write_default_config(ub / "config.yaml")

    with pytest.raises(CommandError, match="no canaries found"):
        execute_history(tmp_path)


def test_execute_history_does_not_wrap_config_load_failure(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        execute_history(tmp_path)


def test_execute_history_does_not_wrap_canary_load_failure(tmp_path: Path) -> None:
    ub = tmp_path / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    (ub / "results").mkdir()
    write_default_config(ub / "config.yaml")
    (ub / "canaries/broken.yaml").write_text("not: valid: canary", encoding="utf-8")

    with pytest.raises(CanaryLoadError):
        execute_history(tmp_path)
