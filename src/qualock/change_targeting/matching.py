from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .models import ScalarValue, TargetContextV0


class RequirementMatch(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RequirementMatchResult:
    status: RequirementMatch
    missing_context_keys: tuple[str, ...] = ()


def _scalar_equal(required: ScalarValue, observed: ScalarValue) -> bool:
    return type(required) is type(observed) and required == observed


def match_requirements(
    requirements: Mapping[str, ScalarValue], context: TargetContextV0
) -> RequirementMatchResult:
    facts = context.facts
    missing_keys: list[str] = []
    has_mismatch = False
    has_unknown = False
    for key, required_value in requirements.items():
        if key not in facts:
            has_unknown = True
            missing_keys.append(key)
            continue
        if not _scalar_equal(required_value, facts[key]):
            has_mismatch = True
    if has_mismatch:
        return RequirementMatchResult(RequirementMatch.MISMATCH)
    if has_unknown:
        return RequirementMatchResult(RequirementMatch.UNKNOWN, tuple(sorted(missing_keys)))
    return RequirementMatchResult(RequirementMatch.MATCH)
