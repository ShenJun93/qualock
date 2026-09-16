import pytest

from qualock.change_targeting.matching import (
    RequirementMatch,
    RequirementMatchResult,
    match_requirements,
)
from qualock.change_targeting.models import TargetContextV0


@pytest.mark.parametrize(
    ("requirements", "facts", "status", "missing"),
    [
        ({}, {}, RequirementMatch.MATCH, ()),
        ({"a": True}, {"a": True}, RequirementMatch.MATCH, ()),
        ({"a": True}, {"a": False}, RequirementMatch.MISMATCH, ()),
        ({"a": True}, {}, RequirementMatch.UNKNOWN, ("a",)),
        (
            {"a": True, "b": 1},
            {"a": False},
            RequirementMatch.MISMATCH,
            (),
        ),
        (
            {"a": True, "b": 1},
            {"a": True},
            RequirementMatch.UNKNOWN,
            ("b",),
        ),
        ({"a": True}, {"a": 1}, RequirementMatch.MISMATCH, ()),
        ({"a": 1}, {"a": True}, RequirementMatch.MISMATCH, ()),
    ],
)
def test_requirement_matching(requirements, facts, status, missing):
    context = TargetContextV0(schema_version=0, facts=facts)
    result = match_requirements(requirements, context)
    assert result.status is status
    assert result.missing_context_keys == missing


def test_requirement_match_result_defaults_missing_keys_to_empty_tuple() -> None:
    assert RequirementMatchResult(RequirementMatch.MATCH).missing_context_keys == ()


def test_requirement_match_enum_has_exactly_three_states() -> None:
    assert {member.value for member in RequirementMatch} == {"MATCH", "MISMATCH", "UNKNOWN"}


def test_requirement_matching_missing_keys_are_sorted_regardless_of_input_order() -> None:
    context = TargetContextV0(schema_version=0, facts={})
    result = match_requirements({"z": True, "a": False}, context)
    assert result.status is RequirementMatch.UNKNOWN
    assert result.missing_context_keys == ("a", "z")
