import inspect
from pathlib import Path
from typing import Any

import pytest

from qualock.baseline.io import BaselineStaleError
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.canary.loader import load_canary
from qualock.canary.models import CanarySpec
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeSignalV0,
    CoverageAssessmentV0,
    IncompleteReason,
    TargetContextV0,
)
from qualock.config.models import QualockConfig
from qualock.project import config_fingerprint, suite_fingerprint
from qualock.targeted_execution import planning
from qualock.targeted_execution.models import TargetedExecutionError
from qualock.targeted_execution.planning import (
    TargetedExecutionNotReady,
    prepare_targeted_execution,
)


def make_canary(canary_id: str, *, critical: bool = False) -> CanarySpec:
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": canary_id,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": "grader.patch", "command": ["pytest -q"]},
            "constraints": {"protected_paths": ["tests/**"]},
            "critical": critical,
        }
    )


def make_signal(
    *,
    agent: str = "codex",
    baseline_version: str = "0.150.0",
) -> ChangeSignalV0:
    return ChangeSignalV0.model_validate(
        {
            "schema_version": 0,
            "agent": agent,
            "baseline_version": baseline_version,
            "candidate_version": "0.151.0",
            "impacts": [{"contract_id": "command.execution"}],
        }
    )


def make_context() -> TargetContextV0:
    return TargetContextV0(schema_version=0, facts={"secret.fact": "must-not-escape"})


def make_lock(
    *,
    agent: str = "codex",
    version: str = "0.150.0",
    suite_sha256: str = "suite-current",
    config_sha256: str = "config-current",
) -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-18T00:00:00Z",
        agent=AgentPin(name=agent, version=version, binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="high"),
        qualock_version="0.1.1",
        suite_sha256=suite_sha256,
        config_sha256=config_sha256,
        canaries={},
    )


def ready_assessment(
    selected_sources: tuple[str, ...] = ("probe-a",),
) -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="1" * 64,
        target_context_sha256="2" * 64,
        coverage_sha256="3" * 64,
        status=AssessmentStatus.READY,
        relevant_contracts=("command.execution",),
        selected_sources=selected_sources,
    )


def incomplete_assessment() -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="1" * 64,
        target_context_sha256="2" * 64,
        coverage_sha256="3" * 64,
        status=AssessmentStatus.INCOMPLETE,
        relevant_contracts=("command.execution",),
        uncovered=(
            {
                "contract_id": "command.execution",
                "reason": IncompleteReason.COVERAGE_GAP,
            },
        ),
    )


def not_applicable_assessment() -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="1" * 64,
        target_context_sha256="2" * 64,
        coverage_sha256="3" * 64,
        status=AssessmentStatus.NOT_APPLICABLE,
    )


def patch_basic_preflight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    signal: ChangeSignalV0 | None = None,
    context: TargetContextV0 | None = None,
    config: QualockConfig | None = None,
    lock: BaselineLock | None = None,
    canaries: tuple[CanarySpec, ...] | None = None,
) -> tuple[ChangeSignalV0, TargetContextV0, QualockConfig, BaselineLock, tuple[CanarySpec, ...]]:
    actual_signal = signal or make_signal()
    actual_context = context or make_context()
    actual_config = config or QualockConfig()
    actual_lock = lock or make_lock()
    actual_canaries = canaries or (make_canary("probe-a"),)

    monkeypatch.setattr(planning, "load_change_signal", lambda path: actual_signal)
    monkeypatch.setattr(planning, "load_target_context", lambda path: actual_context)
    monkeypatch.setattr(
        planning,
        "load_project",
        lambda root: (actual_config, list(actual_canaries)),
    )
    monkeypatch.setattr(planning, "read_baseline_lock", lambda path: actual_lock)
    monkeypatch.setattr(planning, "suite_fingerprint", lambda values: "suite-current")
    monkeypatch.setattr(planning, "config_fingerprint", lambda value: "config-current")
    monkeypatch.setattr(planning, "assert_suite_fresh", lambda *args: None)
    monkeypatch.setattr(planning, "coverage_declarations", lambda values: ("coverage",))
    monkeypatch.setattr(planning, "sha256_canonical", lambda value: "lock-hash")
    return actual_signal, actual_context, actual_config, actual_lock, actual_canaries


def test_prepare_targeted_execution_orders_preflight_before_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    signal = make_signal()
    context = make_context()
    config = QualockConfig()
    lock = make_lock()
    probe_a = make_canary("probe-a")
    probe_b = make_canary("probe-b")
    canaries = [probe_b, probe_a]
    assessment = ready_assessment(("probe-a", "probe-b"))

    monkeypatch.setattr(
        planning,
        "load_change_signal",
        lambda path: calls.append("load signal") or signal,
    )
    monkeypatch.setattr(
        planning,
        "load_target_context",
        lambda path: calls.append("load context") or context,
    )
    monkeypatch.setattr(
        planning,
        "load_project",
        lambda root: calls.append("load project") or (config, canaries),
    )
    monkeypatch.setattr(
        planning,
        "read_baseline_lock",
        lambda path: calls.append("read baseline lock") or lock,
    )

    def fake_suite_fingerprint(values: Any) -> str:
        ids = tuple(item.id for item in values)
        calls.append("full suite fingerprint" if ids == ("probe-b", "probe-a") else "selected suite fingerprint")
        return "suite-current" if ids == ("probe-b", "probe-a") else "selected-suite"

    monkeypatch.setattr(planning, "suite_fingerprint", fake_suite_fingerprint)
    monkeypatch.setattr(
        planning,
        "config_fingerprint",
        lambda value: calls.append("config fingerprint") or "config-current",
    )
    monkeypatch.setattr(
        planning,
        "assert_suite_fresh",
        lambda *args: calls.append("assert freshness"),
    )
    monkeypatch.setattr(
        planning,
        "coverage_declarations",
        lambda values: calls.append("coverage declarations") or ("coverage",),
    )
    monkeypatch.setattr(
        planning,
        "assess_change",
        lambda *args: calls.append("assess change") or assessment,
    )
    monkeypatch.setattr(
        planning,
        "sha256_canonical",
        lambda value: calls.append("baseline lock fingerprint") or "lock-hash",
    )

    result = prepare_targeted_execution(
        tmp_path,
        tmp_path / "signal.yaml",
        tmp_path / "context.yaml",
    )

    assert tuple(canary.id for canary in result.selected_canaries) == ("probe-a", "probe-b")
    assert calls == [
        "load signal",
        "load context",
        "load project",
        "read baseline lock",
        "full suite fingerprint",
        "config fingerprint",
        "assert freshness",
        "coverage declarations",
        "assess change",
        "selected suite fingerprint",
        "baseline lock fingerprint",
    ]


@pytest.mark.parametrize(
    ("config_agent", "lock_agent", "lock_version", "message"),
    [
        ("claude", "codex", "0.150.0", "signal agent"),
        ("codex", "claude", "0.150.0", "signal agent"),
        ("codex", "codex", "0.149.0", "baseline version"),
    ],
)
def test_identity_mismatch_fails_before_freshness_or_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_agent: str,
    lock_agent: str,
    lock_version: str,
    message: str,
) -> None:
    config = QualockConfig.model_validate({"agent": {"name": config_agent}})
    patch_basic_preflight(
        monkeypatch,
        config=config,
        lock=make_lock(agent=lock_agent, version=lock_version),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("identity mismatch must fail before freshness/planner")

    monkeypatch.setattr(planning, "assert_suite_fresh", forbidden)
    monkeypatch.setattr(planning, "assess_change", forbidden)

    with pytest.raises(TargetedExecutionError, match=message):
        prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")


def test_stale_project_fails_before_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_basic_preflight(monkeypatch)

    def stale(*args: object) -> None:
        raise BaselineStaleError("suite fingerprint changed")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("stale preflight must stop before planner/runtime")

    monkeypatch.setattr(planning, "assert_suite_fresh", stale)
    monkeypatch.setattr(planning, "coverage_declarations", forbidden)
    monkeypatch.setattr(planning, "assess_change", forbidden)

    with pytest.raises(BaselineStaleError, match="suite fingerprint changed"):
        prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")


@pytest.mark.parametrize(
    "assessment",
    [incomplete_assessment(), not_applicable_assessment()],
)
def test_non_ready_disposition_raises_typed_exception_with_exact_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    assessment: CoverageAssessmentV0,
) -> None:
    patch_basic_preflight(monkeypatch)
    monkeypatch.setattr(planning, "assess_change", lambda *args: assessment)

    with pytest.raises(TargetedExecutionNotReady) as exc_info:
        prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")

    assert exc_info.value.assessment is assessment


def test_ready_resolves_exact_selected_tuple_and_excludes_unrelated_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canaries = (
        make_canary("critical-z", critical=True),
        make_canary("probe-b"),
        make_canary("probe-a"),
    )
    patch_basic_preflight(monkeypatch, canaries=canaries)
    assessment = ready_assessment(("probe-a", "probe-b"))
    monkeypatch.setattr(planning, "assess_change", lambda *args: assessment)

    result = prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")

    assert tuple(item.id for item in result.selected_canaries) == ("probe-a", "probe-b")
    assert "critical-z" not in {item.id for item in result.selected_canaries}
    assert result.canaries == canaries
    assert result.assessment is assessment
    assert not hasattr(result, "context")
    assert all(not isinstance(value, TargetContextV0) for value in vars(result).values())


def test_missing_selected_source_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_basic_preflight(monkeypatch)
    monkeypatch.setattr(
        planning,
        "assess_change",
        lambda *args: ready_assessment(("missing-probe",)),
    )

    with pytest.raises(
        TargetedExecutionError,
        match="selected source is missing from project: missing-probe",
    ):
        prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")


def test_malformed_ready_with_empty_sources_is_defensively_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_basic_preflight(monkeypatch)
    malformed = CoverageAssessmentV0.model_construct(
        schema_version=0,
        signal_sha256="1" * 64,
        target_context_sha256="2" * 64,
        coverage_sha256="3" * 64,
        status=AssessmentStatus.READY,
        relevant_contracts=("command.execution",),
        selected_sources=(),
        uncovered=(),
        unresolved=(),
    )
    monkeypatch.setattr(planning, "assess_change", lambda *args: malformed)

    with pytest.raises(
        TargetedExecutionError,
        match="READY assessment must select at least one source",
    ):
        prepare_targeted_execution(tmp_path, tmp_path / "signal", tmp_path / "context")


def test_planning_module_has_no_runtime_provider_imports() -> None:
    source = inspect.getsource(planning)
    forbidden = (
        "qualock.run.",
        "qualock.agents.",
        "credential",
        "resolver_factory",
        "backend_factory",
    )
    assert not any(token in source for token in forbidden)


def test_historical_click_sentinel_remains_incomplete_without_managed_shell_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    click = load_canary(repo_root / "benchmarks/oss-smoke/click-sentinel.yaml")
    assert click.coverage == ()

    config = QualockConfig()
    lock = make_lock(
        version="0.149.1",
        suite_sha256=suite_fingerprint((click,)),
        config_sha256=config_fingerprint(config),
    )
    monkeypatch.setattr(planning, "load_project", lambda root: (config, [click]))
    monkeypatch.setattr(planning, "read_baseline_lock", lambda path: lock)

    signal_path = tmp_path / "signal.yaml"
    signal_path.write_text(
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
""",
        encoding="utf-8",
    )
    context_path = tmp_path / "context.yaml"
    context_path.write_text(
        """schema_version: 0
facts:
  codex.managed.shell_tool: true
  codex.managed.unified_exec: false
""",
        encoding="utf-8",
    )

    with pytest.raises(TargetedExecutionNotReady) as exc_info:
        prepare_targeted_execution(tmp_path, signal_path, context_path)

    assessment = exc_info.value.assessment
    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.selected_sources == ()
    assert [(item.contract_id, item.reason) for item in assessment.uncovered] == [
        ("command.execution", IncompleteReason.COVERAGE_GAP)
    ]
    assert assessment.unresolved == ()
