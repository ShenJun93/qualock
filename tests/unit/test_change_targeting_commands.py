from pathlib import Path
from typing import Any

import pytest

import qualock.commands as qualock_commands
from qualock.canary.models import CanarySpec
from qualock.change_targeting import commands
from qualock.change_targeting.errors import ChangeTargetingInputError
from qualock.change_targeting.models import AssessmentStatus, CoverageDeclarationV0
from qualock.config.io import write_default_config


def make_canary(**overrides: Any) -> CanarySpec:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "id": "canary-a",
        "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix it",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": "grader.patch", "command": ["pytest -q"]},
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    }
    payload.update(overrides)
    return CanarySpec.model_validate(payload)


def test_coverage_declarations_matches_brief_example() -> None:
    canary = make_canary(
        coverage=[
            {
                "contract_id": "command.execution",
                "context_requirements": {"execution.mode": "container"},
            }
        ]
    )

    assert commands.coverage_declarations([canary]) == (
        CoverageDeclarationV0(
            source_id="canary-a",
            source_kind="canary",
            contract_id="command.execution",
            context_requirements={"execution.mode": "container"},
        ),
    )


def test_coverage_declarations_includes_all_declarations_sorted_canonically() -> None:
    canary_b = make_canary(
        id="canary-b",
        coverage=[{"contract_id": "tool.inventory", "context_requirements": {}}],
    )
    canary_a = make_canary(
        id="canary-a",
        coverage=[
            {"contract_id": "command.execution", "context_requirements": {}},
            {"contract_id": "mcp.visibility", "context_requirements": {}},
        ],
    )

    result = commands.coverage_declarations([canary_b, canary_a])

    assert result == (
        CoverageDeclarationV0(source_id="canary-a", contract_id="command.execution"),
        CoverageDeclarationV0(source_id="canary-a", contract_id="mcp.visibility"),
        CoverageDeclarationV0(source_id="canary-b", contract_id="tool.inventory"),
    )


def test_coverage_declarations_with_no_coverage_is_empty() -> None:
    canary = make_canary()
    assert commands.coverage_declarations([canary]) == ()


def test_coverage_declarations_raises_change_targeting_input_error_on_conflict() -> None:
    canary = make_canary(
        coverage=[
            {
                "contract_id": "command.execution",
                "context_requirements": {"execution.mode": "container"},
            },
            {
                "contract_id": "command.execution",
                "context_requirements": {"execution.mode": "linux-host"},
            },
        ]
    )

    with pytest.raises(ChangeTargetingInputError):
        commands.coverage_declarations([canary])


def test_coverage_declarations_conflict_is_not_a_bare_value_error() -> None:
    canary = make_canary(
        coverage=[
            {"contract_id": "command.execution", "context_requirements": {"flag": True}},
            {"contract_id": "command.execution", "context_requirements": {"flag": False}},
        ]
    )

    try:
        commands.coverage_declarations([canary])
        raise AssertionError("expected ChangeTargetingInputError")
    except ChangeTargetingInputError:
        pass
    except ValueError as exc:
        raise AssertionError(
            f"expected ChangeTargetingInputError, got bare {type(exc).__name__}"
        ) from exc


def test_execute_target_change_orchestrates_load_then_assess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Any]] = []
    signal_sentinel = object()
    context_sentinel = object()
    config_sentinel = object()
    canaries_sentinel = ["canary-marker"]
    declarations_sentinel = ("declaration-marker",)
    assessment_sentinel = object()

    root = tmp_path
    signal_path = tmp_path / "signal.yaml"
    context_path = tmp_path / "context.yaml"

    def fake_load_change_signal(path: Path) -> object:
        calls.append(("load_change_signal", path))
        return signal_sentinel

    def fake_load_target_context(path: Path) -> object:
        calls.append(("load_target_context", path))
        return context_sentinel

    def fake_load_project(load_root: Path) -> tuple[object, list[str]]:
        calls.append(("load_project", load_root))
        return config_sentinel, canaries_sentinel

    def fake_coverage_declarations(canaries: list[str]) -> tuple[str, ...]:
        calls.append(("coverage_declarations", canaries))
        return declarations_sentinel

    def fake_assess_change(signal: object, context: object, declarations: object) -> object:
        calls.append(("assess_change", signal, context, declarations))
        return assessment_sentinel

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("target-change must not execute providers or qualification")

    monkeypatch.setattr(commands, "load_change_signal", fake_load_change_signal)
    monkeypatch.setattr(commands, "load_target_context", fake_load_target_context)
    monkeypatch.setattr(commands, "load_project", fake_load_project)
    monkeypatch.setattr(commands, "coverage_declarations", fake_coverage_declarations)
    monkeypatch.setattr(commands, "assess_change", fake_assess_change)
    monkeypatch.setattr(qualock_commands, "execute_check", forbidden)

    result = commands.execute_target_change(root, signal_path, context_path)

    assert result is assessment_sentinel
    assert calls == [
        ("load_change_signal", signal_path),
        ("load_target_context", context_path),
        ("load_project", root),
        ("coverage_declarations", canaries_sentinel),
        ("assess_change", signal_sentinel, context_sentinel, declarations_sentinel),
    ]


def _write_project_with_coverage(root: Path, coverage_yaml: str) -> None:
    project_dir = root / ".qualock"
    (project_dir / "canaries").mkdir(parents=True)
    write_default_config(project_dir / "config.yaml")
    (project_dir / "canaries" / "grader.patch").write_text("patch", encoding="utf-8")
    (project_dir / "canaries" / "sample.yaml").write_text(
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
{coverage_yaml}
""",
        encoding="utf-8",
    )


def test_execute_target_change_end_to_end_ready(tmp_path: Path) -> None:
    _write_project_with_coverage(
        tmp_path,
        "coverage:\n"
        "  - contract_id: command.execution\n"
        "    context_requirements:\n"
        "      execution.mode: container\n",
    )
    signal_path = tmp_path / "signal.yaml"
    signal_path.write_text(
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      os.family: linux
""",
        encoding="utf-8",
    )
    context_path = tmp_path / "context.yaml"
    context_path.write_text(
        """schema_version: 0
facts:
  os.family: linux
  execution.mode: container
""",
        encoding="utf-8",
    )

    assessment = commands.execute_target_change(tmp_path, signal_path, context_path)

    assert assessment.status is AssessmentStatus.READY
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ("sample",)


def test_execute_target_change_end_to_end_incomplete_without_coverage(tmp_path: Path) -> None:
    _write_project_with_coverage(tmp_path, "")
    signal_path = tmp_path / "signal.yaml"
    signal_path.write_text(
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      os.family: linux
""",
        encoding="utf-8",
    )
    context_path = tmp_path / "context.yaml"
    context_path.write_text(
        """schema_version: 0
facts:
  os.family: linux
""",
        encoding="utf-8",
    )

    assessment = commands.execute_target_change(tmp_path, signal_path, context_path)

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.uncovered
