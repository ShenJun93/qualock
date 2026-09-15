"""Portable golden-vector conformance for first-bad/v1."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from qualock.protocols.first_bad.io import FirstBadVerificationError, FirstBadVerificationReason
from qualock.protocols.first_bad.models import FirstBadClaimClass
from qualock.protocols.first_bad.verify import verify_first_bad
from qualock.protocols.paired_change.models import ConditionStatus

FIXTURES = Path(__file__).parents[1] / "fixtures" / "first_bad_v1"
VECTORS_PATH = FIXTURES / "vectors.json"
VECTOR_NAMES = (
    "no-bad-full-range",
    "first-bad-first-edge",
    "first-bad-middle",
    "multiple-canaries-one-attributable",
    "unresolved-prefix",
    "unresolved-boundary",
    "missing-edge",
    "skipped-catalog-release",
    "reordered-or-duplicate-catalog",
    "cross-edge-binary-identity-mismatch",
    "support-identity-mismatch",
    "suite-config-design-drift",
    "tampered-child-bundle",
    "tampered-paired-change-evidence",
    "tampered-chain-receipt",
    "unexpected-inventory-or-symlink",
)


def _canonical_file_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def _vectors() -> dict[str, dict[str, object]]:
    payload = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return {item["name"]: item for item in payload["vectors"]}


def _snapshot_regular_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(_canonical_file_bytes(payload))


def _chain_sha256(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "chain_sha256"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _apply_chain_field_drift(
    package: Path, field: str, value: object
) -> None:
    path = package / "chain-evidence.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    payload["chain_sha256"] = _chain_sha256(payload)
    _write_json(path, payload)


def _apply_mutation(package: Path, tmp_path: Path, mutation: dict[str, object]) -> None:
    op = mutation["op"]
    if op == "protocol-preparation-unknown":
        edge = int(mutation["edge"])
        path = package / "edges" / f"{edge:06d}" / "protocol" / "protocol-evidence.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["canaries"][0]["pairs"][0]["attempts"][1]["preparation_sha256"] = None
        _write_json(path, payload)
        return
    if op == "delete-edge":
        edge = int(mutation["edge"])
        shutil.rmtree(package / "edges" / f"{edge:06d}")
        return
    if op == "append-byte":
        path = package / str(mutation["path"])
        path.write_bytes(path.read_bytes() + str(mutation["text"]).encode("utf-8"))
        return
    if op == "protocol-qualification-id-tamper":
        edge = int(mutation["edge"])
        path = package / "edges" / f"{edge:06d}" / "protocol" / "protocol-evidence.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["qualification_id"] += "-tampered"
        _write_json(path, payload)
        return
    if op == "write-chain-receipt":
        _write_json(package / "chain-receipt.json", mutation["payload"])
        return
    if op == "symlink-bundle":
        edge = int(mutation["edge"])
        bundle = package / "edges" / f"{edge:06d}" / "bundle"
        target = tmp_path / f"symlink-target-{edge:06d}"
        shutil.move(str(bundle), str(target))
        try:
            os.symlink(target, bundle, target_is_directory=True)
        except OSError as exc:
            shutil.move(str(target), str(bundle))
            pytest.skip(f"host cannot create directory symlink: {exc}")
        return
    raise AssertionError(f"unknown test mutation: {op}")


def _assemble_vector(tmp_path: Path, vector: dict[str, object]) -> Path:
    package = tmp_path / "package"
    edges = package / "edges"
    edges.mkdir(parents=True)
    for index, seed in enumerate(vector["seeds"]):
        shutil.copytree(FIXTURES / "edges" / str(seed), edges / f"{index:06d}")
    _write_json(package / "chain-evidence.json", vector["chain_evidence"])
    for mutation in vector["mutations"]:
        _apply_mutation(package, tmp_path, mutation)
    return package


def test_suite_config_design_drift_vector_exercises_each_dimension() -> None:
    vector = _vectors()["suite-config-design-drift"]
    cases = vector["drift_cases"]
    assert [item["field"] for item in cases] == [
        "suite_sha256",
        "config_sha256",
        "protocol_design_sha256",
    ]
    assert all(item["expected_error_reason"] == "edge_binding_mismatch" for item in cases)


def test_first_bad_vector_set_and_fixture_portability() -> None:
    vectors = _vectors()
    assert tuple(vectors) == VECTOR_NAMES
    assert VECTORS_PATH.read_bytes() == _canonical_file_bytes(json.loads(VECTORS_PATH.read_text()))
    seed_dirs = sorted(path.name for path in (FIXTURES / "edges").iterdir() if path.is_dir())
    assert seed_dirs == ["seed-a", "seed-b", "seed-c"]
    for path in sorted(FIXTURES.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert path.read_bytes() == _canonical_file_bytes(payload)
        text = path.read_text(encoding="utf-8")
        assert "/home/" not in text and r"\\Users\\" not in text
        assert "api_key" not in text.lower() and "credential" not in text.lower()


@pytest.mark.parametrize("vector_name", VECTOR_NAMES)
def test_first_bad_golden_vector(tmp_path: Path, vector_name: str) -> None:
    vector = _vectors()[vector_name]
    if "drift_cases" in vector:
        for case in vector["drift_cases"]:
            case_tmp = tmp_path / str(case["field"])
            case_tmp.mkdir()
            package = _assemble_vector(case_tmp, vector)
            _apply_chain_field_drift(package, str(case["field"]), case["value"])
            before = _snapshot_regular_files(package)
            with pytest.raises(FirstBadVerificationError) as exc_info:
                verify_first_bad(package)
            assert exc_info.value.reason is FirstBadVerificationReason(
                case["expected_error_reason"]
            )
            assert _snapshot_regular_files(package) == before
        return

    package = _assemble_vector(tmp_path, vector)
    before = _snapshot_regular_files(package)
    expected = vector["expected"]

    if "error_reason" in expected:
        with pytest.raises(FirstBadVerificationError) as exc_info:
            verify_first_bad(package)
        assert exc_info.value.reason is FirstBadVerificationReason(expected["error_reason"])
        assert _snapshot_regular_files(package) == before
        return

    receipt = verify_first_bad(package)
    expected_receipt = expected["receipt"]
    actual_payload = receipt.model_dump(mode="json")
    assert actual_payload == expected_receipt
    assert _canonical_file_bytes(actual_payload) == _canonical_file_bytes(expected_receipt)
    assert _snapshot_regular_files(package) == before

    if receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD:
        assert len(receipt.conditions) == 10
        assert all(item.status is ConditionStatus.TRUE for item in receipt.conditions)
