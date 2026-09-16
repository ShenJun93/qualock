import json

import pytest

from qualock.change_targeting.canonical import (
    canonical_assessment_bytes,
    digest_context,
    digest_coverage,
    digest_signal,
    normalize_coverage,
    normalize_signal,
)
from qualock.change_targeting.errors import ChangeTargetingInputError
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeImpactV0,
    ChangeSignalV0,
    CoverageAssessmentV0,
    CoverageDeclarationV0,
    IncompleteReason,
    SourceProvenanceV0,
    TargetContextV0,
    UncoveredV0,
    UnresolvedV0,
)


def make_signal(impacts: list[dict], source_ref: str | None = None) -> ChangeSignalV0:
    return ChangeSignalV0(
        schema_version=0,
        agent="codex",
        baseline_version="0.149.1",
        candidate_version="0.150.1",
        impacts=tuple(ChangeImpactV0.model_validate(item) for item in impacts),
        source=SourceProvenanceV0(kind="upstream-issue", ref=source_ref) if source_ref else None,
    )


def test_normalize_signal_removes_exact_duplicate_impacts_and_sorts() -> None:
    with_duplicates = make_signal(
        [
            {"contract_id": "tool.inventory", "scope_requirements": {"b": 1}},
            {"contract_id": "command.execution", "scope_requirements": {"a": True}},
            {"contract_id": "command.execution", "scope_requirements": {"a": True}},
        ]
    )
    without_duplicates = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {"a": True}},
            {"contract_id": "tool.inventory", "scope_requirements": {"b": 1}},
        ]
    )
    assert normalize_signal(with_duplicates) == normalize_signal(without_duplicates)


def test_digest_signal_is_stable_under_impact_reordering() -> None:
    left = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {"a": True, "b": 1}},
            {"contract_id": "tool.inventory", "scope_requirements": {}},
        ]
    )
    right = make_signal(
        [
            {"contract_id": "tool.inventory", "scope_requirements": {}},
            {"contract_id": "command.execution", "scope_requirements": {"b": 1, "a": True}},
        ]
    )
    assert digest_signal(left) == digest_signal(right)


def test_digest_signal_changes_when_inert_source_ref_changes() -> None:
    left = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {}}],
        source_ref="openai/codex#1",
    )
    right = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {}}],
        source_ref="openai/codex#2",
    )
    assert digest_signal(left) != digest_signal(right)


def test_digest_context_is_stable_under_fact_reordering() -> None:
    context_a = TargetContextV0(
        schema_version=0, facts={"os.family": "linux", "execution.mode": "container"}
    )
    context_b = TargetContextV0(
        schema_version=0, facts={"execution.mode": "container", "os.family": "linux"}
    )
    assert digest_context(context_a) == digest_context(context_b)


def test_digest_coverage_is_stable_under_reordering_and_exact_duplicates() -> None:
    declaration_a = CoverageDeclarationV0(
        source_id="canary-a",
        contract_id="command.execution",
        context_requirements={"execution.mode": "container"},
    )
    declaration_b = CoverageDeclarationV0(
        source_id="canary-b",
        contract_id="tool.inventory",
        context_requirements={},
    )
    left = (declaration_a, declaration_b)
    right = (declaration_b, declaration_a, declaration_a)
    assert digest_coverage(left) == digest_coverage(right)


def test_normalize_coverage_raises_on_conflicting_declarations() -> None:
    declaration_a = CoverageDeclarationV0(
        source_id="canary-a",
        contract_id="command.execution",
        context_requirements={"execution.mode": "container"},
    )
    declaration_b = CoverageDeclarationV0(
        source_id="canary-a",
        contract_id="command.execution",
        context_requirements={"execution.mode": "linux-host"},
    )
    with pytest.raises(ChangeTargetingInputError):
        normalize_coverage((declaration_a, declaration_b))


def test_normalize_coverage_allows_distinct_declarations_for_same_contract() -> None:
    declaration_a = CoverageDeclarationV0(
        source_id="canary-a",
        contract_id="command.execution",
        context_requirements={"execution.mode": "container"},
    )
    declaration_b = CoverageDeclarationV0(
        source_id="canary-b",
        contract_id="command.execution",
        context_requirements={"execution.mode": "container"},
    )
    result = normalize_coverage((declaration_a, declaration_b))
    assert len(result) == 2


def make_assessment() -> CoverageAssessmentV0:
    return CoverageAssessmentV0(
        signal_sha256="a" * 64,
        target_context_sha256="b" * 64,
        coverage_sha256="c" * 64,
        status=AssessmentStatus.INCOMPLETE,
        relevant_contracts=("command.execution", "tool.inventory"),
        uncovered=(UncoveredV0(contract_id="command.execution"),),
        unresolved=(
            UnresolvedV0(
                contract_id="tool.inventory",
                reason=IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                missing_context_keys=("os.family",),
            ),
        ),
    )


def test_canonical_assessment_bytes_is_compact_sorted_and_reproducible() -> None:
    first = canonical_assessment_bytes(make_assessment())
    second = canonical_assessment_bytes(make_assessment())
    assert first == second
    assert not first.endswith(b"\n")
    text = first.decode("utf-8")
    assert " " not in text
    assert "\n" not in text

    parsed = json.loads(text)
    keys = list(parsed.keys())
    assert keys == sorted(keys)
    for value in parsed.values():
        if isinstance(value, dict):
            assert list(value.keys()) == sorted(value.keys())
