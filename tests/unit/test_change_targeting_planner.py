from qualock.change_targeting.canonical import canonical_assessment_bytes
from qualock.change_targeting.models import (
    AssessmentStatus,
    ChangeImpactV0,
    ChangeSignalV0,
    CoverageDeclarationV0,
    IncompleteReason,
    TargetContextV0,
    UncoveredV0,
    UnresolvedV0,
)
from qualock.change_targeting.planner import assess_change


def make_signal(impacts: list[dict]) -> ChangeSignalV0:
    return ChangeSignalV0(
        schema_version=0,
        agent="codex",
        baseline_version="0.149.1",
        candidate_version="0.150.1",
        impacts=tuple(ChangeImpactV0.model_validate(item) for item in impacts),
    )


def make_context(facts: dict) -> TargetContextV0:
    return TargetContextV0(schema_version=0, facts=facts)


def make_declaration(
    source_id: str, contract_id: str, context_requirements: dict | None = None
) -> CoverageDeclarationV0:
    return CoverageDeclarationV0(
        source_id=source_id,
        contract_id=contract_id,
        context_requirements=context_requirements or {},
    )


def test_match_with_compatible_coverage_is_ready() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "linux"})
    declarations = [make_declaration("canary-a", "command.execution")]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.READY
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ("canary-a",)
    assert assessment.uncovered == ()
    assert assessment.unresolved == ()


def test_all_impacts_mismatch_is_not_applicable() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "windows"})

    assessment = assess_change(signal, context, [])

    assert assessment.status is AssessmentStatus.NOT_APPLICABLE
    assert assessment.relevant_contracts == ()
    assert assessment.selected_sources == ()
    assert assessment.uncovered == ()
    assert assessment.unresolved == ()


def test_match_with_no_declaration_is_incomplete_coverage_gap() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "linux"})

    assessment = assess_change(signal, context, [])

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ()
    assert assessment.uncovered == (UncoveredV0(contract_id="command.execution"),)
    assert assessment.unresolved == ()


def test_match_with_only_mismatch_declarations_is_incomplete_coverage_gap() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "linux", "execution.mode": "host"})
    declarations = [
        make_declaration("canary-a", "command.execution", {"execution.mode": "container"})
    ]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.uncovered == (UncoveredV0(contract_id="command.execution"),)
    assert assessment.unresolved == ()


def test_match_with_unknown_declaration_and_no_match_is_incomplete_coverage_context_unknown() -> (
    None
):
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "linux"})
    declarations = [
        make_declaration("canary-a", "command.execution", {"execution.mode": "container"})
    ]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.uncovered == ()
    assert assessment.unresolved == (
        UnresolvedV0(
            contract_id="command.execution",
            reason=IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
            missing_context_keys=("execution.mode",),
            source_ids=("canary-a",),
        ),
    )


def test_match_with_mismatch_and_unknown_declarations_is_coverage_context_unknown() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({"os.family": "linux", "execution.mode": "host"})
    declarations = [
        make_declaration("canary-a", "command.execution", {"execution.mode": "container"}),
        make_declaration("canary-b", "command.execution", {"other.flag": True}),
    ]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.uncovered == ()
    assert assessment.unresolved == (
        UnresolvedV0(
            contract_id="command.execution",
            reason=IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
            missing_context_keys=("other.flag",),
            source_ids=("canary-b",),
        ),
    )


def test_unknown_signal_relevance_is_incomplete_target_context_unknown() -> None:
    signal = make_signal(
        [{"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}}]
    )
    context = make_context({})

    assessment = assess_change(signal, context, [])

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.relevant_contracts == ()
    assert assessment.uncovered == ()
    assert assessment.unresolved == (
        UnresolvedV0(
            contract_id="command.execution",
            reason=IncompleteReason.TARGET_CONTEXT_UNKNOWN,
            missing_context_keys=("os.family",),
        ),
    )


def test_match_sibling_with_unknown_same_contract_stays_incomplete() -> None:
    signal = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {"os.family": "linux"}},
            {
                "contract_id": "command.execution",
                "scope_requirements": {"execution.mode": "container"},
            },
        ]
    )
    context = make_context({"os.family": "linux"})
    declarations = [make_declaration("canary-a", "command.execution")]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.INCOMPLETE
    assert assessment.relevant_contracts == ("command.execution",)
    assert assessment.selected_sources == ()
    assert assessment.uncovered == ()
    assert assessment.unresolved == (
        UnresolvedV0(
            contract_id="command.execution",
            reason=IncompleteReason.TARGET_CONTEXT_UNKNOWN,
            missing_context_keys=("execution.mode",),
        ),
    )


def test_minimum_source_selection_prefers_single_covering_source() -> None:
    signal = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {}},
            {"contract_id": "tool.inventory", "scope_requirements": {}},
        ]
    )
    context = make_context({})
    declarations = [
        make_declaration("canary-a", "command.execution"),
        make_declaration("canary-b", "tool.inventory"),
        make_declaration("canary-c", "command.execution"),
        make_declaration("canary-c", "tool.inventory"),
    ]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.READY
    assert assessment.selected_sources == ("canary-c",)


def test_equal_cardinality_selection_uses_lexical_tie_break() -> None:
    signal = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {}},
            {"contract_id": "tool.inventory", "scope_requirements": {}},
        ]
    )
    context = make_context({})
    declarations = [
        make_declaration("canary-x", "command.execution"),
        make_declaration("canary-y", "tool.inventory"),
        make_declaration("canary-p", "command.execution"),
        make_declaration("canary-q", "tool.inventory"),
    ]

    assessment = assess_change(signal, context, declarations)

    assert assessment.status is AssessmentStatus.READY
    assert assessment.selected_sources == ("canary-p", "canary-q")


def test_canonical_assessment_bytes_are_identical_for_reordered_equivalent_inputs() -> None:
    signal_left = make_signal(
        [
            {"contract_id": "command.execution", "scope_requirements": {"a": True, "b": 1}},
            {"contract_id": "tool.inventory", "scope_requirements": {}},
        ]
    )
    signal_right = make_signal(
        [
            {"contract_id": "tool.inventory", "scope_requirements": {}},
            {"contract_id": "command.execution", "scope_requirements": {"b": 1, "a": True}},
        ]
    )
    context = make_context({"a": True, "b": 1})
    declarations_left = [
        make_declaration("canary-a", "command.execution"),
        make_declaration("canary-b", "tool.inventory"),
    ]
    declarations_right = [
        make_declaration("canary-b", "tool.inventory"),
        make_declaration("canary-a", "command.execution"),
    ]

    left = assess_change(signal_left, context, declarations_left)
    right = assess_change(signal_right, context, declarations_right)

    assert canonical_assessment_bytes(left) == canonical_assessment_bytes(right)


def test_managed_scope_mismatch_yields_not_applicable_with_empty_collections() -> None:
    signal = make_signal(
        [
            {
                "contract_id": "command.execution",
                "scope_requirements": {
                    "codex.managed.shell_tool": True,
                    "codex.managed.unified_exec": False,
                },
            }
        ]
    )
    context = make_context(
        {"codex.managed.shell_tool": True, "codex.managed.unified_exec": True}
    )

    assessment = assess_change(signal, context, [])

    assert assessment.status is AssessmentStatus.NOT_APPLICABLE
    assert assessment.relevant_contracts == ()
    assert assessment.selected_sources == ()
    assert assessment.uncovered == ()
    assert assessment.unresolved == ()
