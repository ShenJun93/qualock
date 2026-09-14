"""Task 10 deterministic local paired-change lifecycle proof."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from qualock.commands import execute_baseline, execute_check
from qualock.evidence.export import export_evidence_bundle
from qualock.protocols.paired_change.models import ClaimClass, ConditionStatus
from qualock.protocols.paired_change.verify import verify_paired_change
from qualock.run.models import AttemptControlContext, AttemptControlProfiles, AttemptExecution
from tests.unit.test_evidence_export import FakeBackend, FakeResolver, _setup_project


class DeterministicProtocolBackend(FakeBackend):
    def control_profiles(self, canary: object) -> AttemptControlProfiles:
        return AttemptControlProfiles(
            preparation_sha256="1" * 64,
            isolation_sha256="2" * 64,
            resource_sha256="3" * 64,
            runtime_sha256="4" * 64,
        )

    def run_attempt_with_context(self, **kwargs: object) -> AttemptExecution:
        result = replace(super().run_attempt(**kwargs), events_jsonl=(
            f"{kwargs['side'].value}:{kwargs['repetition']}:{kwargs['binary'].version}"
        ))
        identity = hashlib.sha256(
            f"{kwargs['side'].value}:{kwargs['repetition']}".encode()
        ).hexdigest()
        return AttemptExecution(
            result=result,
            context=AttemptControlContext(
                profiles=self.control_profiles(kwargs["canary"]),
                isolation_instance_sha256=identity,
            ),
        )


def _run_lifecycle(tmp_path: Path, candidate_passes: bool):
    project = tmp_path / "project"
    _setup_project(project)
    canary_path = project / ".qualock/canaries/sample.yaml"
    canary = yaml.safe_load(canary_path.read_text(encoding="utf-8"))
    canary["paired_change"] = {
        "material_dimensions": ["AGENT_BINARY"],
        "max_pair_gap_ms": 5000,
    }
    canary_path.write_text(yaml.safe_dump(canary, sort_keys=False), encoding="utf-8")
    resolver = FakeResolver(with_support_binary=True)
    successes = {"0.150.0"} | ({"0.151.0"} if candidate_passes else set())
    backend = DeterministicProtocolBackend(success_versions=successes)
    execute_baseline(
        project,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="base-task-10",
        created_at="2026-09-14T00:00:00Z",
    )
    execute_check(
        project,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id="check-task-10",
    )
    exported = export_evidence_bundle(
        project, "check-task-10", tmp_path / "bundle"
    )
    assert exported.protocol_path is not None
    return verify_paired_change(exported.path, exported.protocol_path)


@pytest.mark.parametrize(
    "candidate_passes,expected_claim",
    [
        (False, ClaimClass.ATTRIBUTABLE_CHANGESET),
        (True, ClaimClass.NO_REGRESSION_OBSERVED),
    ],
    ids=["attributable-changeset", "no-regression-observed"],
)
def test_deterministic_local_paired_change_e2e(
    tmp_path: Path, candidate_passes: bool, expected_claim: ClaimClass
) -> None:
    receipt = _run_lifecycle(tmp_path, candidate_passes)
    assert len(receipt.qualification_conditions) == 3
    assert len(receipt.canary_claims) == 1
    claim = receipt.canary_claims[0]
    assert len(claim.conditions) == 7
    assert all(
        item.status is ConditionStatus.TRUE
        for item in (*receipt.qualification_conditions, *claim.conditions)
    )
    assert claim.claim is expected_claim
