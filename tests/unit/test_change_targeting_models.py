import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.change_targeting.contracts import BEHAVIORAL_CONTRACTS
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeSignalV0,
    CoverageAssessmentV0,
    CoverageDeclarationV0,
    IncompleteReason,
    TargetContextV0,
    UncoveredV0,
    UnresolvedV0,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def valid_signal_data() -> dict:
    return {
        "schema_version": 0,
        "agent": "codex",
        "baseline_version": "0.149.1",
        "candidate_version": "0.150.1",
        "impacts": [{"contract_id": "command.execution", "scope_requirements": {}}],
    }


def test_initial_behavioral_contract_registry_is_small_and_exact() -> None:
    assert BEHAVIORAL_CONTRACTS == frozenset(
        {"command.execution", "tool.inventory", "mcp.visibility", "approval.semantics"}
    )


def test_signal_rejects_unknown_contract() -> None:
    with pytest.raises(ValidationError, match="unknown behavioral contract"):
        ChangeSignalV0.model_validate(
            {
                "schema_version": 0,
                "agent": "codex",
                "baseline_version": "0.149.1",
                "candidate_version": "0.150.1",
                "impacts": [{"contract_id": "made.up", "scope_requirements": {}}],
            }
        )


def test_context_rejects_float_and_nested_values() -> None:
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"x": 1.5}})
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"x": [1]}})
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"x": {"y": 1}}})


def test_signal_rejects_unsupported_schema_version() -> None:
    data = valid_signal_data()
    data["schema_version"] = 1
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)


def test_signal_rejects_unsupported_agent_identity() -> None:
    data = valid_signal_data()
    data["agent"] = "made-up-agent"
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)


def test_signal_rejects_malformed_versions() -> None:
    data = valid_signal_data()
    data["baseline_version"] = "not-a-version"
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)

    data = valid_signal_data()
    data["candidate_version"] = "also not a version"
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)


def test_signal_rejects_empty_impacts() -> None:
    data = valid_signal_data()
    data["impacts"] = []
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)


def test_coverage_declaration_rejects_empty_source_id() -> None:
    with pytest.raises(ValidationError):
        CoverageDeclarationV0.model_validate(
            {
                "source_id": "",
                "source_kind": "canary",
                "contract_id": "command.execution",
                "context_requirements": {},
            }
        )


def test_coverage_declaration_rejects_unknown_contract() -> None:
    with pytest.raises(ValidationError, match="unknown behavioral contract"):
        CoverageDeclarationV0.model_validate(
            {
                "source_id": "canary-a",
                "source_kind": "canary",
                "contract_id": "made.up",
                "context_requirements": {},
            }
        )


def test_context_rejects_empty_key() -> None:
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"": "x"}})


def test_context_rejects_non_string_key() -> None:
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {1: "x"}})


def test_signal_impact_rejects_empty_scope_requirement_key() -> None:
    data = valid_signal_data()
    data["impacts"] = [{"contract_id": "command.execution", "scope_requirements": {"": True}}]
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)


def test_coverage_declaration_rejects_non_string_context_requirement_key() -> None:
    with pytest.raises(ValidationError):
        CoverageDeclarationV0.model_validate(
            {
                "source_id": "canary-a",
                "source_kind": "canary",
                "contract_id": "command.execution",
                "context_requirements": {1: "x"},
            }
        )


def test_assessment_requires_64_lowercase_hex_digests() -> None:
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": "not-hex",
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.NOT_APPLICABLE,
            }
        )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": "A" * 64,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.NOT_APPLICABLE,
            }
        )


def test_not_applicable_requires_all_companion_collections_empty() -> None:
    CoverageAssessmentV0.model_validate(
        {
            "schema_version": 0,
            "signal_sha256": DIGEST_A,
            "target_context_sha256": DIGEST_B,
            "coverage_sha256": DIGEST_C,
            "status": AssessmentStatus.NOT_APPLICABLE,
        }
    )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": DIGEST_A,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.NOT_APPLICABLE,
                "relevant_contracts": ("command.execution",),
            }
        )


def test_ready_requires_non_empty_relevant_and_selected_and_no_gaps() -> None:
    CoverageAssessmentV0.model_validate(
        {
            "schema_version": 0,
            "signal_sha256": DIGEST_A,
            "target_context_sha256": DIGEST_B,
            "coverage_sha256": DIGEST_C,
            "status": AssessmentStatus.READY,
            "relevant_contracts": ("command.execution",),
            "selected_sources": ("canary-c",),
        }
    )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": DIGEST_A,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.READY,
                "relevant_contracts": (),
                "selected_sources": (),
            }
        )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": DIGEST_A,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.READY,
                "relevant_contracts": ("command.execution",),
                "selected_sources": ("canary-c",),
                "uncovered": [{"contract_id": "command.execution"}],
            }
        )


def test_incomplete_requires_empty_selected_and_at_least_one_gap_or_unresolved() -> None:
    CoverageAssessmentV0.model_validate(
        {
            "schema_version": 0,
            "signal_sha256": DIGEST_A,
            "target_context_sha256": DIGEST_B,
            "coverage_sha256": DIGEST_C,
            "status": AssessmentStatus.INCOMPLETE,
            "relevant_contracts": ("command.execution",),
            "uncovered": [{"contract_id": "command.execution"}],
        }
    )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": DIGEST_A,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.INCOMPLETE,
                "selected_sources": ("canary-a",),
                "uncovered": [{"contract_id": "command.execution"}],
            }
        )
    with pytest.raises(ValidationError):
        CoverageAssessmentV0.model_validate(
            {
                "schema_version": 0,
                "signal_sha256": DIGEST_A,
                "target_context_sha256": DIGEST_B,
                "coverage_sha256": DIGEST_C,
                "status": AssessmentStatus.INCOMPLETE,
            }
        )


def test_unresolved_target_context_unknown_requires_empty_source_ids() -> None:
    UnresolvedV0.model_validate(
        {
            "contract_id": "command.execution",
            "reason": IncompleteReason.TARGET_CONTEXT_UNKNOWN,
            "missing_context_keys": ("codex.managed.shell_tool",),
            "source_ids": (),
        }
    )
    with pytest.raises(ValidationError):
        UnresolvedV0.model_validate(
            {
                "contract_id": "command.execution",
                "reason": IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                "missing_context_keys": ("codex.managed.shell_tool",),
                "source_ids": ("canary-a",),
            }
        )


def test_unresolved_coverage_context_unknown_requires_non_empty_source_ids() -> None:
    UnresolvedV0.model_validate(
        {
            "contract_id": "command.execution",
            "reason": IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
            "missing_context_keys": ("os.family",),
            "source_ids": ("canary-a",),
        }
    )
    with pytest.raises(ValidationError):
        UnresolvedV0.model_validate(
            {
                "contract_id": "command.execution",
                "reason": IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
                "missing_context_keys": ("os.family",),
                "source_ids": (),
            }
        )


def test_unresolved_requires_non_empty_missing_context_keys() -> None:
    with pytest.raises(ValidationError):
        UnresolvedV0.model_validate(
            {
                "contract_id": "command.execution",
                "reason": IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                "missing_context_keys": (),
                "source_ids": (),
            }
        )


def test_uncovered_reason_is_fixed_to_coverage_gap() -> None:
    UncoveredV0.model_validate({"contract_id": "command.execution"})
    with pytest.raises(ValidationError):
        UncoveredV0.model_validate(
            {"contract_id": "command.execution", "reason": "TARGET_CONTEXT_UNKNOWN"}
        )


def test_models_forbid_extra_fields() -> None:
    data = valid_signal_data()
    data["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        ChangeSignalV0.model_validate(data)

    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {}, "unexpected": "nope"})

    with pytest.raises(ValidationError):
        CoverageDeclarationV0.model_validate(
            {
                "source_id": "canary-a",
                "source_kind": "canary",
                "contract_id": "command.execution",
                "context_requirements": {},
                "unexpected": "nope",
            }
        )


def test_scalar_values_only_str_bool_int_null() -> None:
    context = TargetContextV0.model_validate(
        {
            "schema_version": 0,
            "facts": {"a": "text", "b": True, "c": 1, "d": None},
        }
    )
    assert context.facts == {"a": "text", "b": True, "c": 1, "d": None}


def test_strict_scalar_bool_and_int_remain_distinct() -> None:
    true_context = TargetContextV0.model_validate({"schema_version": 0, "facts": {"a": True}})
    one_context = TargetContextV0.model_validate({"schema_version": 0, "facts": {"a": 1}})
    assert true_context.facts["a"] is True
    assert one_context.facts["a"] == 1
    assert one_context.facts["a"] is not True
    assert type(true_context.facts["a"]) is bool
    assert type(one_context.facts["a"]) is int


def test_parent_package_stays_inert() -> None:
    src_root = Path(__file__).resolve().parents[2] / "src"
    code = f"""
import sys
sys.path.insert(0, {str(src_root)!r})
import qualock.change_targeting as ct
assert ct.__all__ == ()
assert "qualock.project" not in sys.modules
assert "qualock.canary" not in sys.modules
for forbidden in ("io", "commands", "render", "cli_support"):
    assert not hasattr(ct, forbidden)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_output_rows_reject_unknown_contract_ids() -> None:
    with pytest.raises(ValidationError, match="unknown behavioral contract"):
        UncoveredV0.model_validate({"contract_id": "made.up"})
    with pytest.raises(ValidationError, match="unknown behavioral contract"):
        UnresolvedV0.model_validate(
            {
                "contract_id": "made.up",
                "reason": IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                "missing_context_keys": ("os.family",),
                "source_ids": (),
            }
        )
