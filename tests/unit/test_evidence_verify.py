import socket
import subprocess
import sys
from pathlib import Path

import pytest

from qualock.evidence.bundle_models import (
    BASELINE_LOCK_FILENAME,
    CANARIES_FILENAME,
    MANIFEST_FILENAME,
    PRICING_FILENAME,
    PROVENANCE_FILENAME,
    QUALIFICATION_FILENAME,
    REPORT_FILENAME,
    EvidenceBundleError,
    EvidenceBundleReason,
    VerifiedEvidenceBundle,
)
from qualock.evidence.verify import verify_evidence_bundle
from qualock.pricing.sidecar import PricingSidecarPayloadError, parse_pricing_sidecar_payload
from qualock.qualification.models import Verdict
from tests.unit.evidence_bundle_fixtures import (
    BuiltBundle,
    CanaryScenario,
    all_success,
    attempt_budget_skipped_reason,
    build_bundle,
    load_json,
    replace_payload,
    token_budget_skipped_reason,
    write_manifest,
)

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only filesystem shape")

_REQUIRED_PAYLOAD_FILENAMES = (
    REPORT_FILENAME,
    QUALIFICATION_FILENAME,
    BASELINE_LOCK_FILENAME,
    PROVENANCE_FILENAME,
    CANARIES_FILENAME,
)


def _build(tmp_path: Path, **kwargs) -> BuiltBundle:
    return build_bundle(tmp_path / "bundle", **kwargs)


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        entry.name: (entry.read_bytes(), entry.stat().st_mtime_ns)
        for entry in sorted(root.iterdir())
    }


# --- Step 3.1 — happy path, inventory, and cross-file identity -----------------


def test_verify_evidence_bundle_accepts_manually_built_valid_bundle(tmp_path: Path) -> None:
    built = _build(tmp_path)

    bundle = verify_evidence_bundle(built.root)

    assert isinstance(bundle, VerifiedEvidenceBundle)
    assert bundle.manifest.qualification_id == "check-q"
    assert bundle.report.verdict is Verdict.PASS
    assert bundle.manifest_sha256 == __import__("hashlib").sha256(
        (built.root / MANIFEST_FILENAME).read_bytes()
    ).hexdigest()


def test_verify_evidence_bundle_accepts_multi_canary_mixed_verdicts(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=True,
            baseline=all_success(3),
            candidate=("fail", "fail", "fail"),
        ),
        CanaryScenario(
            canary_id="gamma",
            critical=False,
            baseline=all_success(3),
            candidate=("success", "fail", "success"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios)

    bundle = verify_evidence_bundle(built.root)

    assert bundle.report.verdict is Verdict.BLOCK
    by_id = {execution.canary_id: execution for execution in bundle.report.executions}
    assert by_id["alpha"].verdict is Verdict.PASS
    assert by_id["beta"].verdict is Verdict.BLOCK
    assert by_id["gamma"].verdict is Verdict.WARN


def test_verify_evidence_bundle_never_writes_bundle_bytes(tmp_path: Path) -> None:
    built = _build(tmp_path)
    before = _snapshot(built.root)

    verify_evidence_bundle(built.root)

    after = _snapshot(built.root)
    assert before == after


@_POSIX_ONLY
def test_verify_evidence_bundle_rejects_symlink_payload(tmp_path: Path) -> None:
    built = _build(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (built.root / REPORT_FILENAME).unlink()
    (built.root / REPORT_FILENAME).symlink_to(outside)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_verify_evidence_bundle_rejects_extra_file(tmp_path: Path) -> None:
    built = _build(tmp_path)
    (built.root / "unexpected.json").write_bytes(b"{}")

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH


def test_verify_evidence_bundle_rejects_missing_required_file(tmp_path: Path) -> None:
    built = _build(tmp_path)
    (built.root / CANARIES_FILENAME).unlink()

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.INVENTORY_MISMATCH


def test_verify_evidence_bundle_rejects_manifest_missing_file_entry(tmp_path: Path) -> None:
    built = _build(tmp_path)
    del built.manifest["files"][CANARIES_FILENAME]
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.INVENTORY_MISMATCH


def test_verify_evidence_bundle_rejects_manifest_extra_file_entry(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["files"]["pricing.json"] = {"sha256": "a" * 64, "size_bytes": 2}
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.INVENTORY_MISMATCH


@pytest.mark.parametrize("filename", [MANIFEST_FILENAME, *_REQUIRED_PAYLOAD_FILENAMES])
def test_verify_evidence_bundle_rejects_malformed_json(tmp_path: Path, filename: str) -> None:
    built = _build(tmp_path)
    (built.root / filename).write_bytes(b"{not json")
    if filename != MANIFEST_FILENAME:
        digest, size = _rehash(built.root, filename)
        built.manifest["files"][filename] = {"sha256": digest, "size_bytes": size}
        write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    expected = (
        EvidenceBundleReason.MALFORMED_MANIFEST
        if filename == MANIFEST_FILENAME
        else EvidenceBundleReason.MALFORMED_PAYLOAD
    )
    assert exc_info.value.reason is expected


def _rehash(root: Path, filename: str) -> tuple[str, int]:
    import hashlib

    data = (root / filename).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


@pytest.mark.parametrize("filename", _REQUIRED_PAYLOAD_FILENAMES)
def test_verify_evidence_bundle_detects_single_byte_tamper_via_digest(
    tmp_path: Path, filename: str
) -> None:
    built = _build(tmp_path)
    data = bytearray((built.root / filename).read_bytes())
    data[-2] ^= 0xFF
    (built.root / filename).write_bytes(bytes(data))

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.DIGEST_MISMATCH


# --- Gemini identity ------------------------------------------------------------


def test_verify_evidence_bundle_accepts_gemini_with_support_sha(tmp_path: Path) -> None:
    built = _build(
        tmp_path,
        agent_name="gemini",
        baseline_support_sha256="b" * 64,
        candidate_support_sha256="c" * 64,
    )

    bundle = verify_evidence_bundle(built.root)
    assert bundle.manifest.baseline_identity.name == "gemini"


def test_verify_evidence_bundle_rejects_gemini_missing_baseline_lock_support(tmp_path: Path) -> None:
    built = _build(
        tmp_path,
        agent_name="gemini",
        baseline_support_sha256="b" * 64,
        candidate_support_sha256="c" * 64,
    )
    baseline_lock = load_json(built.root, BASELINE_LOCK_FILENAME)
    baseline_lock["agent"]["support_sha256"] = None
    replace_payload(built, BASELINE_LOCK_FILENAME, baseline_lock)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.INVALID_GEMINI_SUPPORT


def test_verify_evidence_bundle_rejects_gemini_missing_manifest_support(tmp_path: Path) -> None:
    built = _build(
        tmp_path,
        agent_name="gemini",
        baseline_support_sha256="b" * 64,
        candidate_support_sha256="c" * 64,
    )
    built.manifest["baseline_identity"]["support_sha256"] = None
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.MALFORMED_MANIFEST


def test_verify_evidence_bundle_accepts_non_gemini_null_support(tmp_path: Path) -> None:
    built = _build(tmp_path, agent_name="codex")
    bundle = verify_evidence_bundle(built.root)
    assert bundle.manifest.baseline_identity.support_sha256 is None


# --- Cross-file identity mismatches ---------------------------------------------


def test_verify_evidence_bundle_rejects_qualification_id_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["qualification_id"] = "other-q"
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_baseline_version_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    qualification = load_json(built.root, QUALIFICATION_FILENAME)
    qualification["baseline_version"] = "9.9.9"
    replace_payload(built, QUALIFICATION_FILENAME, qualification)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_run_qualock_version_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    provenance = load_json(built.root, PROVENANCE_FILENAME)
    provenance["run_qualock_version"] = "0.0.0"
    replace_payload(built, PROVENANCE_FILENAME, provenance)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_allows_exporter_version_independent_of_run_and_lock(
    tmp_path: Path,
) -> None:
    built = _build(
        tmp_path,
        run_qualock_version="1.2.3",
        exporter_qualock_version="1.2.3-different",
        baseline_lock_qualock_version="0.0.1-legacy",
    )
    bundle = verify_evidence_bundle(built.root)
    assert bundle.manifest.exporter_qualock_version == "1.2.3-different"


def test_verify_evidence_bundle_rejects_baseline_lock_sha_tamper(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["baseline_lock_sha256"] = "f" * 64
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_suite_sha_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    baseline_lock = load_json(built.root, BASELINE_LOCK_FILENAME)
    baseline_lock["suite_sha256"] = "f" * 64
    replace_payload(built, BASELINE_LOCK_FILENAME, baseline_lock)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_model_pin_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["model"]["reasoning_effort"] = "low"
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_baseline_identity_rebind_vs_provenance(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["baseline_identity"]["version"] = "9.9.9"
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_baseline_identity_rebind_vs_lock(tmp_path: Path) -> None:
    built = _build(tmp_path)
    provenance = load_json(built.root, PROVENANCE_FILENAME)
    provenance["baseline_identity"]["binary_sha256"] = "d" * 64
    replace_payload(built, PROVENANCE_FILENAME, provenance)
    built.manifest["baseline_identity"]["binary_sha256"] = "d" * 64
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_run_order_digest_tamper(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["run_order_sha256"] = "e" * 64
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_run_order_value_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    qualification = load_json(built.root, QUALIFICATION_FILENAME)
    qualification["run_order"] = list(reversed(qualification["run_order"]))
    replace_payload(built, QUALIFICATION_FILENAME, qualification)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH


def test_verify_evidence_bundle_rejects_completeness_struct_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    qualification = load_json(built.root, QUALIFICATION_FILENAME)
    qualification["completeness"]["attempts_used"] += 1
    replace_payload(built, QUALIFICATION_FILENAME, qualification)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


# --- Canary provenance cross-binding ---------------------------------------------


def test_verify_evidence_bundle_rejects_repository_url_sha_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["repository_url_sha256"] = "f" * 64
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_unsafe_repository_url(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    unsafe_url = "ssh://git@example.invalid/org/repo.git"
    canaries["canaries"][0]["repository_url"] = unsafe_url
    from qualock.evidence.fingerprint import sha256_canonical

    canaries["canaries"][0]["repository_url_sha256"] = sha256_canonical(unsafe_url)
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_REPOSITORY_URL


def test_verify_evidence_bundle_rejects_base_sha_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["base_sha"] = "9" * 40
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_critical_flag_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["critical"] = not canaries["canaries"][0]["critical"]
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_canary_fingerprint_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["canary_fingerprint_sha256"] = "1" * 64
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_prepared_image_digest_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["prepared_image_digest"] = "sha256:" + "2" * 64
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_repetitions_rebind(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canaries = load_json(built.root, CANARIES_FILENAME)
    canaries["canaries"][0]["repetitions"] = 99
    replace_payload(built, CANARIES_FILENAME, canaries)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


def test_verify_evidence_bundle_rejects_canary_id_set_mismatch(tmp_path: Path) -> None:
    built = _build(tmp_path)
    provenance = load_json(built.root, PROVENANCE_FILENAME)
    provenance["canaries"][0]["canary_id"] = "renamed"
    replace_payload(built, PROVENANCE_FILENAME, provenance)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH


# --- Step 3.2 — attempt layout and policy recomputation --------------------------


def test_verify_evidence_bundle_rejects_duplicate_attempt_slot(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    execution = report["executions"][0]
    duplicate = dict(execution["attempts"][0])
    execution["attempts"].append(duplicate)
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH


def test_verify_evidence_bundle_rejects_invalid_side_value(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["executions"][0]["attempts"][0]["side"] = "referee"
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH


def test_verify_evidence_bundle_rejects_out_of_range_repetition(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["executions"][0]["attempts"][0]["repetition"] = 99
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_execution_aggregate(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["executions"][0]["baseline_successes"] = 0
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_manifest_aggregate(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canary_id = next(iter(built.manifest["canaries"]))
    built.manifest["canaries"][canary_id]["baseline_successes"] = 0
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_execution_verdict(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["executions"][0]["verdict"] = "block"
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_execution_reason(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["executions"][0]["reason"] = "fabricated reason text"
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_manifest_canary_verdict(tmp_path: Path) -> None:
    built = _build(tmp_path)
    canary_id = next(iter(built.manifest["canaries"]))
    built.manifest["canaries"][canary_id]["verdict"] = "block"
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_overall_verdict(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["verdict"] = "block"
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_overall_reasons(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(
            canary_id="beta",
            critical=True,
            baseline=("success", "fail", "success"),
            candidate=("fail", "fail", "fail"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios)
    report = load_json(built.root, REPORT_FILENAME)
    report["reasons"] = ["fabricated"]
    replace_payload(built, REPORT_FILENAME, report)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


# --- Step 3.3 — completeness and optional pricing --------------------------------


def test_verify_evidence_bundle_accepts_budget_stopped_incomplete_evidence(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=6)

    bundle = verify_evidence_bundle(built.root)

    assert bundle.report.verdict is Verdict.INCOMPLETE
    assert bundle.report.completeness.all_canaries_complete is False


def test_verify_evidence_bundle_rejects_incomplete_without_budget_reason(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=None, max_tokens=None)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_rejects_incomplete_presented_as_pass(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=6)
    for filename in (REPORT_FILENAME, QUALIFICATION_FILENAME):
        payload = load_json(built.root, filename)
        payload["verdict"] = "pass"
        replace_payload(built, filename, payload)
    built.manifest["verdict"] = "pass"
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_attempts_expected(tmp_path: Path) -> None:
    built = _build(tmp_path)
    report = load_json(built.root, REPORT_FILENAME)
    report["completeness"]["attempts_expected"] += 1
    replace_payload(built, REPORT_FILENAME, report)
    qualification = load_json(built.root, QUALIFICATION_FILENAME)
    qualification["completeness"]["attempts_expected"] += 1
    replace_payload(built, QUALIFICATION_FILENAME, qualification)
    built.manifest["completeness"]["attempts_expected"] += 1
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_rejects_tampered_all_canaries_complete_flag(tmp_path: Path) -> None:
    built = _build(tmp_path)
    for filename in (REPORT_FILENAME, QUALIFICATION_FILENAME):
        payload = load_json(built.root, filename)
        payload["completeness"]["all_canaries_complete"] = False
        replace_payload(built, filename, payload)
    built.manifest["completeness"]["all_canaries_complete"] = False
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_accepts_valid_pricing_sidecar(tmp_path: Path) -> None:
    built = _build(tmp_path, include_pricing=True)
    assert built.pricing is not None

    bundle = verify_evidence_bundle(built.root)
    assert bundle.report.verdict is Verdict.PASS


def test_verify_evidence_bundle_detects_pricing_byte_tamper(tmp_path: Path) -> None:
    built = _build(tmp_path, include_pricing=True)
    data = bytearray((built.root / "pricing.json").read_bytes())
    data[-2] ^= 0xFF
    (built.root / "pricing.json").write_bytes(bytes(data))

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.DIGEST_MISMATCH


def test_verify_evidence_bundle_rejects_malformed_pricing_schema(tmp_path: Path) -> None:
    built = _build(tmp_path, include_pricing=True)
    pricing = load_json(built.root, "pricing.json")
    pricing["currency"] = "EUR"
    replace_payload(built, "pricing.json", pricing)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.MALFORMED_PAYLOAD


def test_verify_evidence_bundle_rejects_pricing_qualification_id_mismatch(tmp_path: Path) -> None:
    built = _build(tmp_path, include_pricing=True)
    pricing = load_json(built.root, "pricing.json")
    pricing["qualification_id"] = "other-q"
    replace_payload(built, "pricing.json", pricing)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.MALFORMED_PAYLOAD


def test_verify_evidence_bundle_verdict_unaffected_by_pricing_rate_change(tmp_path: Path) -> None:
    built = _build(tmp_path, include_pricing=True)
    pricing = load_json(built.root, "pricing.json")
    pricing["rates_per_million"]["output"] = "999.99"
    replace_payload(built, "pricing.json", pricing)

    bundle = verify_evidence_bundle(built.root)
    assert bundle.report.verdict is Verdict.PASS


# --- Pure/offline behavior --------------------------------------------------------


def test_verify_evidence_bundle_performs_no_network_process_or_docker_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("verify_evidence_bundle must not perform this call")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(subprocess, "run", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)

    import qualock.run.docker as docker_module
    import qualock.run.process as process_module

    monkeypatch.setattr(process_module, "run_process", _fail)
    monkeypatch.setattr(docker_module.DockerRunner, "__init__", _fail)

    built = _build(tmp_path)
    bundle = verify_evidence_bundle(built.root)
    assert bundle.report.verdict is Verdict.PASS


# --- parse_pricing_sidecar_payload direct contract --------------------------------


def test_parse_pricing_sidecar_payload_rejects_non_object() -> None:
    from qualock.history.models import LoadedReport

    loaded = LoadedReport(qualification_id="q-1", qualification_dir=Path("/nonexistent"), executions=())
    with pytest.raises(PricingSidecarPayloadError) as exc_info:
        parse_pricing_sidecar_payload(loaded, [1, 2, 3])
    assert exc_info.value.reason == "pricing sidecar is not a JSON object"


def test_parse_pricing_sidecar_payload_rejects_qualification_id_mismatch() -> None:
    from qualock.history.models import LoadedReport
    from tests.unit.evidence_bundle_fixtures import _pricing_payload

    loaded = LoadedReport(qualification_id="q-1", qualification_dir=Path("/nonexistent"), executions=())
    payload = _pricing_payload("q-other", [])
    with pytest.raises(PricingSidecarPayloadError) as exc_info:
        parse_pricing_sidecar_payload(loaded, payload)
    assert exc_info.value.reason == "pricing qualification_id mismatch"


# --- Fix round 1: I1 TOCTOU single-read tests -------------------------------------


def test_verify_evidence_bundle_opens_each_payload_file_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.evidence import bundle_io

    built = _build(tmp_path)
    open_counts: dict[str, int] = {}
    original_open = bundle_io._open_validated_regular_file

    def spy_open(root: Path, name: str):
        open_counts[name] = open_counts.get(name, 0) + 1
        return original_open(root, name)

    monkeypatch.setattr(bundle_io, "_open_validated_regular_file", spy_open)
    verify_evidence_bundle(built.root)

    for name in _REQUIRED_PAYLOAD_FILENAMES:
        assert open_counts.get(name, 0) == 1, f"payload {name} was opened {open_counts.get(name, 0)} times"
    assert open_counts.get(MANIFEST_FILENAME, 0) == 1


def test_verify_evidence_bundle_pricing_opened_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.evidence import bundle_io

    built = _build(tmp_path, include_pricing=True)
    open_counts: dict[str, int] = {}
    original_open = bundle_io._open_validated_regular_file

    def spy_open(root: Path, name: str):
        open_counts[name] = open_counts.get(name, 0) + 1
        return original_open(root, name)

    monkeypatch.setattr(bundle_io, "_open_validated_regular_file", spy_open)
    verify_evidence_bundle(built.root)

    assert open_counts.get(PRICING_FILENAME, 0) == 1, f"pricing opened {open_counts.get(PRICING_FILENAME, 0)} times"


def test_verify_evidence_bundle_prevents_toctou_double_read_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.evidence import bundle_io

    built = _build(tmp_path)
    open_counts: dict[str, int] = {}
    original_open = bundle_io._open_validated_regular_file

    def fail_on_second_open(root: Path, name: str):
        open_counts[name] = open_counts.get(name, 0) + 1
        if open_counts[name] > 1:
            raise AssertionError(f"payload {name} was opened or read a second time!")
        return original_open(root, name)

    monkeypatch.setattr(bundle_io, "_open_validated_regular_file", fail_on_second_open)
    bundle = verify_evidence_bundle(built.root)
    assert bundle.report.verdict is Verdict.PASS


# --- Fix round 1: I2 Genuine executor budget-skip reason compatibility tests ------


def test_verify_evidence_bundle_accepts_literal_attempt_budget_skipped_reason(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
            reason=attempt_budget_skipped_reason(max_attempts=6, complete_canary_attempts=6),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=6)

    bundle = verify_evidence_bundle(built.root)

    assert bundle.report.verdict is Verdict.INCOMPLETE
    assert bundle.report.completeness.all_canaries_complete is False
    assert bundle.report.executions[1].reason == attempt_budget_skipped_reason(
        max_attempts=6, complete_canary_attempts=6
    )
    assert bundle.report.reasons == (
        attempt_budget_skipped_reason(max_attempts=6, complete_canary_attempts=6),
    )


def test_verify_evidence_bundle_accepts_literal_token_budget_skipped_reason(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
            reason=token_budget_skipped_reason(max_tokens=100, observed_tokens=204),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_tokens=100)

    bundle = verify_evidence_bundle(built.root)

    assert bundle.report.verdict is Verdict.INCOMPLETE
    assert bundle.report.executions[1].reason == token_budget_skipped_reason(
        max_tokens=100, observed_tokens=204
    )
    assert bundle.report.reasons == (
        token_budget_skipped_reason(max_tokens=100, observed_tokens=204),
    )


def test_verify_evidence_bundle_accepts_literal_token_usage_unavailable_skipped_reason(
    tmp_path: Path,
) -> None:
    scenarios = (
        CanaryScenario(
            canary_id="alpha",
            critical=True,
            baseline=all_success(3),
            candidate=all_success(3),
            usage_observed=False,
        ),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
            reason=token_budget_skipped_reason(max_tokens=100, observed_tokens=None),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_tokens=100)

    bundle = verify_evidence_bundle(built.root)

    assert bundle.report.verdict is Verdict.INCOMPLETE
    assert bundle.report.completeness.observed_tokens is None
    assert bundle.report.executions[1].reason == token_budget_skipped_reason(
        max_tokens=100, observed_tokens=None
    )


def test_verify_evidence_bundle_rejects_unauthorized_skipped_reason(tmp_path: Path) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
            reason="INCOMPLETE: skipped by arbitrary reason",
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=6)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH


# --- Fix round 1: I3 Budget/completeness compatibility tests -----------------------


def test_verify_evidence_bundle_rejects_tampered_observed_tokens(tmp_path: Path) -> None:
    built = _build(tmp_path)
    for filename in (REPORT_FILENAME, QUALIFICATION_FILENAME):
        payload = load_json(built.root, filename)
        payload["completeness"]["observed_tokens"] = 99999
        replace_payload(built, filename, payload)
    built.manifest["completeness"]["observed_tokens"] = 99999
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_rejects_observed_tokens_non_none_when_usage_unobserved(
    tmp_path: Path,
) -> None:
    scenarios = (
        CanaryScenario(
            canary_id="sample",
            critical=True,
            baseline=all_success(3),
            candidate=all_success(3),
            usage_observed=False,
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, observed_tokens=1234)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_rejects_attempts_used_exceeding_max_attempts(tmp_path: Path) -> None:
    built = _build(tmp_path, max_attempts=2)
    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_rejects_impossible_huge_max_attempts_on_incomplete_evidence(
    tmp_path: Path,
) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=1_000_000_000)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.COMPLETENESS_MISMATCH


def test_verify_evidence_bundle_accepts_incomplete_evidence_when_token_budget_stopping_with_huge_max_attempts(
    tmp_path: Path,
) -> None:
    scenarios = (
        CanaryScenario(canary_id="alpha", critical=True, baseline=all_success(3), candidate=all_success(3)),
        CanaryScenario(
            canary_id="beta",
            critical=False,
            baseline=("missing", "missing", "missing"),
            candidate=("missing", "missing", "missing"),
            reason=token_budget_skipped_reason(max_tokens=100, observed_tokens=204),
        ),
    )
    built = _build(tmp_path, scenarios=scenarios, max_attempts=1_000_000_000, max_tokens=100)

    bundle = verify_evidence_bundle(built.root)
    assert bundle.report.verdict is Verdict.INCOMPLETE


# --- Fix round 1: I4 Version binding to RuntimeAgentIdentity.version tests ---------


def test_verify_evidence_bundle_rejects_baseline_version_mismatch_with_identity(tmp_path: Path) -> None:
    from qualock.evidence.fingerprint import sha256_canonical

    built = _build(tmp_path)
    built.manifest["baseline_identity"]["version"] = "9.9.9"
    baseline_lock = load_json(built.root, BASELINE_LOCK_FILENAME)
    baseline_lock["agent"]["version"] = "9.9.9"
    replace_payload(built, BASELINE_LOCK_FILENAME, baseline_lock)

    new_lock_sha = sha256_canonical(baseline_lock)
    built.manifest["baseline_lock_sha256"] = new_lock_sha

    provenance = load_json(built.root, PROVENANCE_FILENAME)
    provenance["baseline_identity"]["version"] = "9.9.9"
    provenance["baseline_lock_sha256"] = new_lock_sha
    replace_payload(built, PROVENANCE_FILENAME, provenance)
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH
    assert exc_info.value.label == "baseline_version"


def test_verify_evidence_bundle_rejects_candidate_version_mismatch_with_identity(tmp_path: Path) -> None:
    built = _build(tmp_path)
    built.manifest["candidate_identity"]["version"] = "9.9.9"
    provenance = load_json(built.root, PROVENANCE_FILENAME)
    provenance["candidate_identity"]["version"] = "9.9.9"
    replace_payload(built, PROVENANCE_FILENAME, provenance)
    write_manifest(built.root, built.manifest)

    with pytest.raises(EvidenceBundleError) as exc_info:
        verify_evidence_bundle(built.root)
    assert exc_info.value.reason is EvidenceBundleReason.IDENTITY_MISMATCH
    assert exc_info.value.label == "candidate_version"
