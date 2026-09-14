import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from qualock.evidence.bundle_models import VerifiedEvidenceBundle
from qualock.evidence.export import export_evidence_bundle
from qualock.evidence.verify import verify_evidence_bundle
from qualock.protocols.paired_change.materialize import (
    ProtocolMaterializationError,
    materialize_protocol_evidence,
)
from qualock.protocols.paired_change.models import PairedChangeRunV1
from qualock.protocols.paired_change.run_sidecar import read_paired_change_run
from tests.unit.test_evidence_export import _create_synthetic_qualification


def _export_inputs(tmp_path: Path) -> tuple[PairedChangeRunV1, VerifiedEvidenceBundle]:
    project_root = tmp_path / "project"
    qualification_id = "check-materialize"
    _create_synthetic_qualification(
        project_root,
        qualification_id=qualification_id,
        events_jsonl="materialized-events",
    )
    run = read_paired_change_run(
        project_root
        / ".qualock"
        / "results"
        / qualification_id
        / "paired-change-run-v1.json"
    )
    exported = export_evidence_bundle(
        project_root, qualification_id, tmp_path / "bundle"
    )
    return run, verify_evidence_bundle(exported.path)


def test_materialize_protocol_evidence_binds_exact_verified_bundle(tmp_path: Path) -> None:
    run, bundle = _export_inputs(tmp_path)

    evidence = materialize_protocol_evidence(run, bundle)

    assert evidence.evidence_manifest_sha256 == bundle.manifest_sha256
    assert evidence.model_dump(exclude={"evidence_manifest_sha256"}) == run.model_dump()


Mutator = Callable[[PairedChangeRunV1], PairedChangeRunV1]


def _baseline_identity_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    return run.model_copy(
        update={"baseline_state": run.baseline_state.model_copy(update={"version": "9.9.9"})}
    )


def _candidate_identity_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    return run.model_copy(
        update={
            "candidate_state": run.candidate_state.model_copy(
                update={"binary_sha256": "f" * 64}
            )
        }
    )


def _canary_fingerprint_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    changed = run.canaries[0].model_copy(
        update={"canary_fingerprint_sha256": "f" * 64}
    )
    return run.model_copy(update={"canaries": (changed,)})


def _repetition_layout_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    changed = run.canaries[0].model_copy(update={"pairs": ()})
    return run.model_copy(update={"canaries": (changed,)})


def _events_digest_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    pair = run.canaries[0].pairs[0]
    changed_attempt = pair.attempts[0].model_copy(
        update={"events_sha256": hashlib.sha256(b"different-events").hexdigest()}
    )
    changed_pair = pair.model_copy(update={"attempts": (changed_attempt, pair.attempts[1])})
    changed_canary = run.canaries[0].model_copy(update={"pairs": (changed_pair,)})
    return run.model_copy(update={"canaries": (changed_canary,)})


def _run_order_mismatch(run: PairedChangeRunV1) -> PairedChangeRunV1:
    pair = run.canaries[0].pairs[0]
    changed_pair = pair.model_copy(update={"attempts": tuple(reversed(pair.attempts))})
    changed_canary = run.canaries[0].model_copy(update={"pairs": (changed_pair,)})
    return run.model_copy(update={"canaries": (changed_canary,)})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run: run.model_copy(update={"qualification_id": "other"}),
        _baseline_identity_mismatch,
        _candidate_identity_mismatch,
        _canary_fingerprint_mismatch,
        _repetition_layout_mismatch,
        _events_digest_mismatch,
        _run_order_mismatch,
    ],
    ids=[
        "qualification-id",
        "baseline-identity",
        "candidate-identity",
        "canary-fingerprint",
        "repetition-layout",
        "events-sha256",
        "run-order",
    ],
)
def test_materialize_protocol_evidence_rejects_cross_binding_mismatch(
    tmp_path: Path, mutate: Mutator
) -> None:
    run, bundle = _export_inputs(tmp_path)

    with pytest.raises(ProtocolMaterializationError, match="protocol evidence mismatch"):
        materialize_protocol_evidence(mutate(run), bundle)
