import itertools
from collections.abc import Iterable, Sequence
from typing import Literal

from .canonical import (
    digest_context,
    digest_coverage,
    digest_signal,
    normalize_context,
    normalize_coverage,
    normalize_signal,
)
from .matching import RequirementMatch, match_requirements
from .models import (
    AssessmentStatus,
    ChangeSignalV0,
    CoverageAssessmentV0,
    CoverageDeclarationV0,
    IncompleteReason,
    TargetContextV0,
    UncoveredV0,
    UnresolvedV0,
)

_UnresolvedReason = Literal[
    IncompleteReason.TARGET_CONTEXT_UNKNOWN, IncompleteReason.COVERAGE_CONTEXT_UNKNOWN
]
_UnresolvedKey = tuple[str, _UnresolvedReason]
_UnresolvedAccumulator = dict[_UnresolvedKey, tuple[set[str], set[str]]]


def _record_unresolved(
    accumulator: _UnresolvedAccumulator,
    contract_id: str,
    reason: _UnresolvedReason,
    missing_context_keys: Iterable[str],
    source_ids: Iterable[str] = (),
) -> None:
    missing, sources = accumulator.setdefault((contract_id, reason), (set(), set()))
    missing.update(missing_context_keys)
    sources.update(source_ids)


def _build_unresolved_rows(accumulator: _UnresolvedAccumulator) -> tuple[UnresolvedV0, ...]:
    ordered_keys = sorted(accumulator, key=lambda key: (key[0], key[1].value))
    return tuple(
        UnresolvedV0(
            contract_id=contract_id,
            reason=reason,
            missing_context_keys=tuple(sorted(accumulator[(contract_id, reason)][0])),
            source_ids=tuple(sorted(accumulator[(contract_id, reason)][1])),
        )
        for contract_id, reason in ordered_keys
    )


def _select_minimum_sources(
    relevant_contracts: Sequence[str], coverage_by_contract: dict[str, list[str]]
) -> tuple[str, ...]:
    contracts = sorted(relevant_contracts)
    all_sources = sorted({source for sources in coverage_by_contract.values() for source in sources})
    for size in range(1, len(all_sources) + 1):
        for combo in itertools.combinations(all_sources, size):
            combo_set = set(combo)
            if all(
                any(source in combo_set for source in coverage_by_contract[contract_id])
                for contract_id in contracts
            ):
                return combo
    raise AssertionError("no covering source set found despite validated coverage")


def assess_change(
    signal: ChangeSignalV0,
    target_context: TargetContextV0,
    declarations: Sequence[CoverageDeclarationV0],
) -> CoverageAssessmentV0:
    signal_sha256 = digest_signal(signal)
    target_context_sha256 = digest_context(target_context)
    coverage_sha256 = digest_coverage(declarations)

    normalized_signal = normalize_signal(signal)
    normalized_context = normalize_context(target_context)
    normalized_declarations = normalize_coverage(declarations)

    unresolved: _UnresolvedAccumulator = {}
    relevant_contracts: set[str] = set()

    for impact in normalized_signal.impacts:
        result = match_requirements(impact.scope_requirements, normalized_context)
        if result.status is RequirementMatch.MATCH:
            relevant_contracts.add(impact.contract_id)
        elif result.status is RequirementMatch.UNKNOWN:
            _record_unresolved(
                unresolved,
                impact.contract_id,
                IncompleteReason.TARGET_CONTEXT_UNKNOWN,
                result.missing_context_keys,
            )

    if not relevant_contracts and not unresolved:
        return CoverageAssessmentV0(
            signal_sha256=signal_sha256,
            target_context_sha256=target_context_sha256,
            coverage_sha256=coverage_sha256,
            status=AssessmentStatus.NOT_APPLICABLE,
        )

    declarations_by_contract: dict[str, list[CoverageDeclarationV0]] = {}
    for declaration in normalized_declarations:
        declarations_by_contract.setdefault(declaration.contract_id, []).append(declaration)

    uncovered: set[str] = set()
    coverage_candidates: dict[str, list[str]] = {}

    for contract_id in sorted(relevant_contracts):
        match_sources: set[str] = set()
        unknown_missing: set[str] = set()
        unknown_sources: set[str] = set()
        has_unknown = False
        for declaration in declarations_by_contract.get(contract_id, []):
            result = match_requirements(declaration.context_requirements, normalized_context)
            if result.status is RequirementMatch.MATCH:
                match_sources.add(declaration.source_id)
            elif result.status is RequirementMatch.UNKNOWN:
                has_unknown = True
                unknown_missing.update(result.missing_context_keys)
                unknown_sources.add(declaration.source_id)

        if match_sources:
            coverage_candidates[contract_id] = sorted(match_sources)
        elif has_unknown:
            _record_unresolved(
                unresolved,
                contract_id,
                IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
                unknown_missing,
                unknown_sources,
            )
        else:
            uncovered.add(contract_id)

    unresolved_rows = _build_unresolved_rows(unresolved)
    uncovered_rows = tuple(UncoveredV0(contract_id=contract_id) for contract_id in sorted(uncovered))

    if uncovered_rows or unresolved_rows:
        return CoverageAssessmentV0(
            signal_sha256=signal_sha256,
            target_context_sha256=target_context_sha256,
            coverage_sha256=coverage_sha256,
            status=AssessmentStatus.INCOMPLETE,
            relevant_contracts=tuple(sorted(relevant_contracts)),
            uncovered=uncovered_rows,
            unresolved=unresolved_rows,
        )

    selected_sources = _select_minimum_sources(sorted(relevant_contracts), coverage_candidates)
    return CoverageAssessmentV0(
        signal_sha256=signal_sha256,
        target_context_sha256=target_context_sha256,
        coverage_sha256=coverage_sha256,
        status=AssessmentStatus.READY,
        relevant_contracts=tuple(sorted(relevant_contracts)),
        selected_sources=selected_sources,
    )
