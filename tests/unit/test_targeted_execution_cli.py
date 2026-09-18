from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest
from typer.testing import CliRunner

import qualock.commands as commands_module
from qualock.change_targeting.errors import ChangeTargetingInputError
from qualock.change_targeting.models import (
    AssessmentStatus,
    CoverageAssessmentV0,
    IncompleteReason,
    UncoveredV0,
)
from qualock.cli import app
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.qualification.models import CanaryExecution, QualificationResult, Verdict
from qualock.targeted_execution.commands import TargetedExecutionOutcome
from qualock.targeted_execution.models import TargetedRunV1
from qualock.targeted_execution.planning import TargetedExecutionNotReady
from qualock.targeted_execution.render import (
    render_targeted_execution_not_started,
    render_targeted_qualification,
)
from tests.unit.test_commands import FakeBackend, FakeResolver, setup_project

runner = CliRunner()


def _assessment(status: AssessmentStatus) -> CoverageAssessmentV0:
    base = {
        "schema_version": 0,
        "signal_sha256": "a" * 64,
        "target_context_sha256": "b" * 64,
        "coverage_sha256": "c" * 64,
        "status": status,
    }
    if status is AssessmentStatus.READY:
        return CoverageAssessmentV0(
            **base,
            relevant_contracts=("command.execution",),
            selected_sources=("probe-a",),
        )
    if status is AssessmentStatus.NOT_APPLICABLE:
        return CoverageAssessmentV0(**base)
    return CoverageAssessmentV0(
        **base,
        relevant_contracts=("command.execution",),
        uncovered=(
            UncoveredV0(
                contract_id="command.execution",
                reason=IncompleteReason.COVERAGE_GAP,
            ),
        ),
    )


def _outcome(tmp_path: Path, verdict: Verdict) -> TargetedExecutionOutcome:
    execution = CanaryExecution(
        canary_id="probe-a",
        critical=False,
        prepared_image_digest="sha256:" + "d" * 64,
        attempts=(),
        baseline_successes=3,
        candidate_successes=3 if verdict is not Verdict.BLOCK else 0,
        baseline_valid=3,
        candidate_valid=3,
        verdict=verdict,
        reason="synthetic result",
    )
    result = QualificationResult(
        qualification_id="target-check-synthetic",
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        verdict=verdict,
        executions=(execution,),
        reasons=(),
        run_order=(),
        attempts_used=6,
        observed_tokens=12,
    )
    return TargetedExecutionOutcome(
        agent_name="codex",
        assessment=_assessment(AssessmentStatus.READY),
        result=result,
        result_dir=tmp_path / ".qualock/results/targeted/target-check-synthetic",
        receipt=cast(TargetedRunV1, object()),
    )


def test_target_check_missing_context_exits_3() -> None:
    result = runner.invoke(app, ["target-check", "signal.yaml"])
    assert result.exit_code == 3


def test_target_check_unknown_option_exits_3() -> None:
    result = runner.invoke(
        app,
        ["target-check", "signal.yaml", "--context", "context.yaml", "--unknown"],
    )
    assert result.exit_code == 3


@pytest.mark.parametrize(
    "exc",
    [
        ChangeTargetingInputError("invalid signal"),
        ConfigError("invalid config"),
        CommandError("invalid execution preflight"),
    ],
)
def test_target_check_known_preflight_errors_exit_3_with_not_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr("qualock.cli.execute_targeted_check", fail)
    result = runner.invoke(
        app,
        ["target-check", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == 3
    assert str(exc) in result.stdout
    assert result.stdout.rstrip().endswith("Targeted execution: NOT STARTED")


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (AssessmentStatus.INCOMPLETE, 4),
        (AssessmentStatus.NOT_APPLICABLE, 5),
    ],
)
def test_target_check_maps_non_ready_planner_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: AssessmentStatus,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    assessment = _assessment(status)

    def not_ready(*args: object, **kwargs: object) -> None:
        raise TargetedExecutionNotReady(assessment)

    monkeypatch.setattr("qualock.cli.execute_targeted_check", not_ready)
    result = runner.invoke(
        app,
        ["target-check", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == expected_exit
    assert f"Status: {status.value}" in result.stdout
    assert result.stdout.rstrip().endswith("Targeted execution: NOT STARTED")


@pytest.mark.parametrize(
    ("verdict", "expected_exit"),
    [
        (Verdict.PASS, 0),
        (Verdict.WARN, 0),
        (Verdict.BLOCK, 2),
        (Verdict.INCOMPLETE, 6),
    ],
)
def test_target_check_maps_completed_verdicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: Verdict,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    outcome = _outcome(tmp_path, verdict)
    monkeypatch.setattr("qualock.cli.execute_targeted_check", lambda *args, **kwargs: outcome)

    result = runner.invoke(
        app,
        ["target-check", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == expected_exit
    assert "QuaLock Targeted Qualification" in result.stdout
    assert "Scope: selected sources only; not a full-suite update-safety verdict." in result.stdout
    assert "Selected sources: probe-a" in result.stdout


def test_target_check_unexpected_error_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("qualock.cli.execute_targeted_check", explode)
    result = runner.invoke(
        app,
        ["target-check", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == 1
    assert result.stdout == "targeted execution failed\n"
    assert "secret internal detail" not in result.stdout


@pytest.mark.parametrize("option", ["--max-attempts", "--max-tokens"])
def test_target_check_rejects_nonpositive_budget_before_orchestration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("orchestration must not start")

    monkeypatch.setattr("qualock.cli.execute_targeted_check", forbidden)
    result = runner.invoke(
        app,
        [
            "target-check",
            "signal.yaml",
            "--context",
            "context.yaml",
            option,
            "0",
        ],
    )

    assert result.exit_code == 3
    assert "greater than zero" in result.stdout
    assert result.stdout.rstrip().endswith("Targeted execution: NOT STARTED")


def test_targeted_render_is_scoped_technical_and_privacy_safe(tmp_path: Path) -> None:
    text = render_targeted_qualification(
        _outcome(tmp_path, Verdict.PASS),
        agent_display_name="Codex",
    )

    assert "QuaLock Targeted Qualification" in text
    assert "Scope: selected sources only; not a full-suite update-safety verdict." in text
    assert "Selected sources: probe-a" in text
    assert "Result directory:" in text
    assert "probe-a" in text
    assert "SAFE TO UPDATE" not in text
    assert "Protected workflows" not in text
    assert "Recommendation:" not in text
    assert "DO-NOT-PERSIST-RAW-CONTEXT" not in text


def test_not_started_renderer_ends_with_exact_banner() -> None:
    text = render_targeted_execution_not_started(
        _assessment(AssessmentStatus.INCOMPLETE)
    )
    assert "Status: INCOMPLETE" in text
    assert text.endswith("Targeted execution: NOT STARTED\n")


def _setup_baseline_project(root: Path) -> None:
    setup_project(root)
    commands_module.execute_baseline(
        root,
        "codex@0.150.0",
        resolver=FakeResolver(),
        backend=FakeBackend({"0.150.0"}),
        qualification_id="baseline-cli-targeted",
        created_at="2026-09-18T00:00:00Z",
    )


def _write_signal_context(
    root: Path,
    *,
    applicable: bool,
) -> tuple[Path, Path]:
    required_os = "linux" if applicable else "windows"
    signal = root / "signal.yaml"
    signal.write_text(
        f"""schema_version: 0
agent: codex
baseline_version: "0.150.0"
candidate_version: "0.151.0"
source:
  kind: upstream-issue
  ref: https://example.invalid/never-fetch-this
impacts:
  - contract_id: command.execution
    scope_requirements:
      os.family: {required_os}
""",
        encoding="utf-8",
    )
    context = root / "context.yaml"
    context.write_text(
        """schema_version: 0
facts:
  os.family: linux
""",
        encoding="utf-8",
    )
    return signal, context


@pytest.mark.parametrize(
    ("applicable", "expected_exit", "expected_status"),
    [
        (True, 4, "INCOMPLETE"),
        (False, 5, "NOT_APPLICABLE"),
    ],
)
def test_target_check_non_ready_never_crosses_runtime_or_network_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    applicable: bool,
    expected_exit: int,
    expected_status: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    _setup_baseline_project(tmp_path)
    signal, context = _write_signal_context(tmp_path, applicable=applicable)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("execution boundary crossed")

    monkeypatch.setattr(commands_module, "_default_resolver", forbidden)
    monkeypatch.setattr(commands_module, "_default_backend", forbidden)
    monkeypatch.setattr(commands_module, "DockerRunner", forbidden)
    monkeypatch.setattr(commands_module, "LinuxHostRunner", forbidden)
    monkeypatch.setattr(
        commands_module, "select_claude_automation_credential", forbidden
    )
    monkeypatch.setattr(
        commands_module, "select_gemini_automation_credential", forbidden
    )
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)

    result = runner.invoke(
        app,
        ["target-check", str(signal), "--context", str(context)],
    )

    assert result.exit_code == expected_exit
    assert f"Status: {expected_status}" in result.stdout
    assert result.stdout.rstrip().endswith("Targeted execution: NOT STARTED")
    assert not (tmp_path / ".qualock/results/targeted").exists()
