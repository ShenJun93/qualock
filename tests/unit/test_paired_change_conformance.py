"""Task 10 portable golden-vector conformance contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.protocols.paired_change.io import (
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
)
from qualock.protocols.paired_change.models import (
    ClaimClass,
    ConditionStatus,
    ProtocolConditionV1,
)
from qualock.protocols.paired_change.verify import verify_paired_change

FIXTURES = Path(__file__).parents[1] / "fixtures" / "paired_change_v1"
VECTOR_NAMES = (
    "attributable-clean",
    "no-regression-clean",
    "unstable-baseline",
    "missing-codex-support",
    "preparation-unknown",
    "isolation-reuse",
    "order-imbalanced",
    "temporal-gap",
    "unexpected-material-change",
    "opaque-material-dimension",
    "manifest-binding-mismatch",
    "tampered-receipt",
)


def _condition_payload(condition: ProtocolConditionV1) -> dict[str, str]:
    return {
        "type": condition.type.value,
        "status": condition.status.value,
        "reason": condition.reason,
    }


@pytest.mark.parametrize("vector_name", VECTOR_NAMES)
def test_paired_change_golden_vector(vector_name: str) -> None:
    vector = FIXTURES / vector_name
    expected = json.loads((vector / "expected.json").read_text(encoding="utf-8"))
    original_files = {
        path.relative_to(vector): path.read_bytes()
        for path in sorted(vector.rglob("*"))
        if path.is_file()
    }

    # Golden inputs are canonical and portable by construction.
    for path in sorted(vector.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert path.read_bytes() == canonical_json_file_bytes(payload)
        serialized = path.read_text(encoding="utf-8")
        assert "/home/" not in serialized and "\\Users\\" not in serialized
        assert "api_key" not in serialized.lower() and "credential" not in serialized.lower()

    if "error_reason" in expected:
        with pytest.raises(PairedChangeVerificationError) as exc_info:
            verify_paired_change(vector / "bundle", vector / "protocol")
        assert exc_info.value.reason is PairedChangeVerificationReason(expected["error_reason"])
        assert original_files == {
            path.relative_to(vector): path.read_bytes()
            for path in sorted(vector.rglob("*"))
            if path.is_file()
        }
        return

    receipt = verify_paired_change(vector / "bundle", vector / "protocol")
    actual = {
        "qualification_conditions": [
            _condition_payload(item) for item in receipt.qualification_conditions
        ],
        "canaries": [
            {
                "canary_id": item.canary_id,
                "conditions": [_condition_payload(condition) for condition in item.conditions],
                "claim": item.claim.value,
            }
            for item in receipt.canary_claims
        ],
    }
    assert actual == expected
    assert original_files == {
        path.relative_to(vector): path.read_bytes()
        for path in sorted(vector.rglob("*"))
        if path.is_file()
    }

    for canary in receipt.canary_claims:
        if canary.claim is ClaimClass.ATTRIBUTABLE_CHANGESET:
            all_conditions = (*receipt.qualification_conditions, *canary.conditions)
            assert len(all_conditions) == 10
            assert all(item.status is ConditionStatus.TRUE for item in all_conditions)
