from pathlib import Path

from qualock.canary.loader import load_canary
from qualock.change_targeting.commands import coverage_declarations
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeSignalV0,
    CoverageDeclarationV0,
    IncompleteReason,
    TargetContextV0,
)
from qualock.change_targeting.planner import assess_change


def _historical_signal() -> ChangeSignalV0:
    return ChangeSignalV0.model_validate(
        {
            "schema_version": 0,
            "agent": "codex",
            "baseline_version": "0.149.1",
            "candidate_version": "0.150.1",
            "impacts": [
                {
                    "contract_id": "command.execution",
                    "scope_requirements": {
                        "codex.managed.shell_tool": True,
                        "codex.managed.unified_exec": False,
                    },
                }
            ],
        }
    )


def _historical_context() -> TargetContextV0:
    return TargetContextV0.model_validate(
        {
            "schema_version": 0,
            "facts": {
                "codex.managed.shell_tool": True,
                "codex.managed.unified_exec": False,
            },
        }
    )


def test_legacy_click_sentinel_cannot_claim_managed_shell_coverage() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    click_path = repo_root / "benchmarks/oss-smoke/click-sentinel.yaml"
    canary = load_canary(click_path)
    assert canary.coverage == ()

    assessment = assess_change(
        _historical_signal(),
        _historical_context(),
        coverage_declarations([canary]),
    )

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ()
    assert [(item.contract_id, item.reason) for item in assessment.uncovered] == [
        ("command.execution", IncompleteReason.COVERAGE_GAP)
    ]
    assert assessment.unresolved == ()


def test_explicit_managed_shell_probe_makes_same_change_ready() -> None:
    declaration = CoverageDeclarationV0(
        source_id="managed-shell-registration-probe",
        source_kind="canary",
        contract_id="command.execution",
        context_requirements={
            "codex.managed.shell_tool": True,
            "codex.managed.unified_exec": False,
        },
    )

    assessment = assess_change(
        _historical_signal(),
        _historical_context(),
        (declaration,),
    )

    assert assessment.status is AssessmentStatus.READY
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ("managed-shell-registration-probe",)
    assert assessment.uncovered == ()
    assert assessment.unresolved == ()
