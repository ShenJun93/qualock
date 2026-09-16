from collections.abc import Sequence

from qualock.evidence.fingerprint import canonical_json, sha256_canonical

from .errors import ChangeTargetingInputError
from .models import (
    ChangeImpactV0,
    ChangeSignalV0,
    CoverageAssessmentV0,
    CoverageDeclarationV0,
    ScalarValue,
    TargetContextV0,
)


def normalize_signal(signal: ChangeSignalV0) -> ChangeSignalV0:
    by_key: dict[tuple[str, bytes], ChangeImpactV0] = {}
    for impact in signal.impacts:
        requirements = dict(sorted(impact.scope_requirements.items()))
        normalized = impact.model_copy(update={"scope_requirements": requirements})
        key = (normalized.contract_id, canonical_json(requirements))
        by_key.setdefault(key, normalized)
    return signal.model_copy(update={"impacts": tuple(by_key[key] for key in sorted(by_key))})


def normalize_context(context: TargetContextV0) -> TargetContextV0:
    return context.model_copy(update={"facts": dict(sorted(context.facts.items()))})


def normalize_coverage(
    declarations: Sequence[CoverageDeclarationV0],
) -> tuple[CoverageDeclarationV0, ...]:
    asserted: dict[tuple[str, str, str, str], ScalarValue] = {}
    by_key: dict[tuple[str, str, str, bytes], CoverageDeclarationV0] = {}
    for declaration in declarations:
        requirements = dict(sorted(declaration.context_requirements.items()))
        normalized = declaration.model_copy(update={"context_requirements": requirements})
        for context_key, value in requirements.items():
            assertion_key = (
                normalized.source_kind,
                normalized.source_id,
                normalized.contract_id,
                context_key,
            )
            if (
                assertion_key in asserted
                and canonical_json(asserted[assertion_key]) != canonical_json(value)
            ):
                raise ChangeTargetingInputError("conflicting coverage declaration")
            asserted[assertion_key] = value
        key = (
            normalized.source_kind,
            normalized.source_id,
            normalized.contract_id,
            canonical_json(requirements),
        )
        by_key.setdefault(key, normalized)
    return tuple(by_key[key] for key in sorted(by_key))


def digest_signal(signal: ChangeSignalV0) -> str:
    return sha256_canonical(normalize_signal(signal).model_dump(mode="json"))


def digest_context(context: TargetContextV0) -> str:
    return sha256_canonical(normalize_context(context).model_dump(mode="json"))


def digest_coverage(declarations: Sequence[CoverageDeclarationV0]) -> str:
    payload = [item.model_dump(mode="json") for item in normalize_coverage(declarations)]
    return sha256_canonical(payload)


def canonical_assessment_bytes(assessment: CoverageAssessmentV0) -> bytes:
    return canonical_json(assessment.model_dump(mode="json"))
