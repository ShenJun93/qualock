from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from qualock.change_targeting.models import (
    AssessmentStatus,
    CoverageAssessmentV0,
    IncompleteReason,
    UncoveredV0,
    UnresolvedV0,
)
from qualock.change_targeting.render import render_coverage_assessment
from qualock.cli import app
from qualock.config.io import write_default_config

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
            selected_sources=("managed-shell-probe",),
        )
    if status is AssessmentStatus.NOT_APPLICABLE:
        return CoverageAssessmentV0(**base)
    return CoverageAssessmentV0(
        **base,
        relevant_contracts=("command.execution",),
        uncovered=(UncoveredV0(contract_id="command.execution"),),
    )


def _write_project(root: Path, *, coverage_yaml: str) -> None:
    qdir = root / ".qualock"
    (qdir / "canaries").mkdir(parents=True)
    write_default_config(qdir / "config.yaml")
    (qdir / "canaries" / "grader.patch").write_text("patch", encoding="utf-8")
    (qdir / "canaries" / "sample.yaml").write_text(
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


def _write_signal_context(
    root: Path, *, contract_id: str = "command.execution", source_ref: str | None = None
) -> tuple[Path, Path]:
    source = ""
    if source_ref is not None:
        source = f"\nsource:\n  kind: upstream-issue\n  ref: {source_ref}\n"
    signal = root / "signal.yaml"
    signal.write_text(
        f"""schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: {contract_id}
    scope_requirements:
      os.family: linux
{source}""",
        encoding="utf-8",
    )
    context = root / "context.yaml"
    context.write_text(
        """schema_version: 0
facts:
  os.family: linux
  execution.mode: container
""",
        encoding="utf-8",
    )
    return signal, context


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (AssessmentStatus.READY, 0),
        (AssessmentStatus.NOT_APPLICABLE, 2),
        (AssessmentStatus.INCOMPLETE, 4),
    ],
)
def test_target_change_maps_assessment_status_to_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: AssessmentStatus,
    expected_exit: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_target_change", lambda *_: _assessment(status))

    result = runner.invoke(
        app,
        ["target-change", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == expected_exit
    assert f"Status: {status.value}" in result.stdout


def test_target_change_missing_context_exits_3() -> None:
    result = runner.invoke(app, ["target-change", "signal.yaml"])
    assert result.exit_code == 3


def test_target_change_unknown_option_exits_3() -> None:
    result = runner.invoke(
        app,
        ["target-change", "signal.yaml", "--context", "context.yaml", "--unknown"],
    )
    assert result.exit_code == 3


def test_target_change_invalid_signal_exits_3_before_project_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    signal, context = _write_signal_context(tmp_path, contract_id="made.up")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("project/provider work must not run for invalid signal")

    monkeypatch.setattr("qualock.change_targeting.commands.load_project", forbidden)

    result = runner.invoke(app, ["target-change", str(signal), "--context", str(context)])

    assert result.exit_code == 3
    assert "made.up" in result.stdout


def test_target_change_conflicting_coverage_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        coverage_yaml=(
            "coverage:\n"
            "  - contract_id: command.execution\n"
            "    context_requirements:\n"
            "      execution.mode: container\n"
            "  - contract_id: command.execution\n"
            "    context_requirements:\n"
            "      execution.mode: linux-host\n"
        ),
    )
    signal, context = _write_signal_context(tmp_path)

    result = runner.invoke(app, ["target-change", str(signal), "--context", str(context)])

    assert result.exit_code == 3
    assert "conflicting coverage declaration" in result.stdout


def test_target_change_success_is_read_only_and_never_dereferences_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        coverage_yaml=(
            "coverage:\n"
            "  - contract_id: command.execution\n"
            "    context_requirements:\n"
            "      execution.mode: container\n"
        ),
    )
    signal, context = _write_signal_context(
        tmp_path, source_ref="https://example.invalid/upstream/123"
    )
    before = _snapshot_files(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("target-change must not execute providers, qualification, or network")

    monkeypatch.setattr("qualock.commands.execute_check", forbidden)
    monkeypatch.setattr("qualock.cli.DockerRunner", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)

    result = runner.invoke(app, ["target-change", str(signal), "--context", str(context)])

    assert result.exit_code == 0
    assert "Status: READY" in result.stdout
    assert _snapshot_files(tmp_path) == before
    assert not (tmp_path / ".qualock" / "baseline.lock").exists()
    assert not (tmp_path / ".qualock" / "results").exists()


def test_target_change_unexpected_error_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("qualock.cli.execute_target_change", explode)

    result = runner.invoke(
        app,
        ["target-change", "signal.yaml", "--context", "context.yaml"],
    )

    assert result.exit_code == 1
    assert result.stdout == "change targeting failed\n"
    assert "secret internal detail" not in result.stdout


def test_render_ready_is_exact_plain_text() -> None:
    assert render_coverage_assessment(_assessment(AssessmentStatus.READY)) == (
        "QuaLock Change Targeting\n\n"
        "Status: READY\n"
        f"Signal SHA256: {'a' * 64}\n"
        f"Target context SHA256: {'b' * 64}\n"
        f"Coverage SHA256: {'c' * 64}\n"
        "Relevant contracts: command.execution\n"
        "Selected sources: managed-shell-probe\n"
        "Coverage gaps: none\n"
        "Unresolved: none\n"
    )


def test_render_not_applicable_uses_none_for_companion_collections() -> None:
    text = render_coverage_assessment(_assessment(AssessmentStatus.NOT_APPLICABLE))
    assert "Relevant contracts: none\n" in text
    assert "Selected sources: none\n" in text
    assert "Coverage gaps: none\n" in text
    assert "Unresolved: none\n" in text


def test_render_incomplete_lists_all_rows_deterministically() -> None:
    assessment = CoverageAssessmentV0(
        schema_version=0,
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.INCOMPLETE,
        relevant_contracts=("command.execution", "mcp.visibility", "tool.inventory"),
        uncovered=(UncoveredV0(contract_id="command.execution"),),
        unresolved=(
            UnresolvedV0(
                contract_id="mcp.visibility",
                reason=IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
                missing_context_keys=("mcp.enabled",),
                source_ids=("probe-a", "probe-b"),
            ),
            UnresolvedV0(
                contract_id="tool.inventory",
                reason=IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                missing_context_keys=("tools.mode",),
            ),
        ),
    )

    assert render_coverage_assessment(assessment).endswith(
        "Relevant contracts: command.execution, mcp.visibility, tool.inventory\n"
        "Selected sources: none\n"
        "Coverage gaps:\n"
        "- command.execution: COVERAGE_GAP\n"
        "Unresolved:\n"
        "- mcp.visibility: COVERAGE_CONTEXT_UNKNOWN; missing=mcp.enabled; sources=probe-a,probe-b\n"
        "- tool.inventory: TARGET_CONTEXT_UNKNOWN; missing=tools.mode; sources=none\n"
    )
