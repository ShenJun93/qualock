import pytest
from pydantic import ValidationError

from qualock.evidence.bundle_models import (
    BUNDLE_FILENAMES,
    CANARIES_FILENAME,
    MAX_CANARY_RECORDS,
    PROVENANCE_FILENAME,
    QUALIFICATION_FILENAME,
    REPORT_FILENAME,
    TEXT_FIELD_MAX_BYTES,
    BundleCompleteness,
    BundleFileRecord,
    EvidenceBundleError,
    EvidenceBundleReason,
    EvidenceManifest,
    ManifestCanaryRecord,
    PublicAgentPin,
    PublicAttempt,
    PublicBaselineLock,
    PublicCanaries,
    PublicCanary,
    PublicCanaryStability,
    PublicExecution,
    PublicModelPin,
    PublicQualification,
    PublicReport,
    PublicUsage,
    VerifiedEvidenceBundle,
    publication_safe_repository_url,
)
from qualock.evidence.provenance import RuntimeAgentIdentity
from qualock.qualification.models import Verdict

_SHA = "a" * 64
_SHA_B = "b" * 64
_SOURCE_SHA = "c" * 40
_PREPARED_DIGEST = "sha256:" + "d" * 64


def _usage_payload() -> dict:
    return {
        "input_tokens": 10,
        "cached_input_tokens": 1,
        "cache_write_input_tokens": 2,
        "output_tokens": 20,
        "reasoning_output_tokens": 3,
        "observed": True,
    }


def _attempt_payload() -> dict:
    return {
        "side": "baseline",
        "repetition": 1,
        "success": True,
        "valid": True,
        "duration_ms": 1200,
        "usage": _usage_payload(),
        "events_sha256": _SHA,
    }


def _execution_payload() -> dict:
    return {
        "canary_id": "sample",
        "critical": True,
        "prepared_image_digest": _PREPARED_DIGEST,
        "attempts": [_attempt_payload()],
        "baseline_valid": 1,
        "baseline_successes": 1,
        "candidate_valid": 1,
        "candidate_successes": 1,
        "verdict": "pass",
        "reason": "ok",
    }


def _completeness_payload() -> dict:
    return {
        "attempts_expected": 2,
        "attempts_used": 2,
        "max_attempts": None,
        "max_tokens": None,
        "observed_tokens": None,
        "all_canaries_complete": True,
    }


def _run_order_payload() -> list:
    return [["sample", "baseline", 1]]


def _report_payload() -> dict:
    return {
        "qualification_id": "check-q",
        "baseline_version": "0.150.0",
        "candidate_version": "0.151.0",
        "verdict": "pass",
        "reasons": [],
        "run_order": _run_order_payload(),
        "executions": [_execution_payload()],
        "completeness": _completeness_payload(),
    }


def _qualification_payload() -> dict:
    return {
        "qualification_id": "check-q",
        "baseline_version": "0.150.0",
        "candidate_version": "0.151.0",
        "verdict": "pass",
        "run_order": _run_order_payload(),
        "completeness": _completeness_payload(),
    }


def _identity_payload(*, name: str = "codex") -> dict:
    return {
        "name": name,
        "version": "0.150.0",
        "binary_sha256": _SHA,
        "support_sha256": None,
    }


def _model_pin_payload() -> dict:
    return {"id": "gpt-5.6-terra", "snapshot": None, "reasoning_effort": "high"}


def _baseline_lock_payload() -> dict:
    return {
        "schema_version": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "agent": {
            "name": "codex",
            "version": "0.150.0",
            "binary_sha256": _SHA,
            "support_sha256": None,
        },
        "model": _model_pin_payload(),
        "qualock_version": "0.0.1-legacy",
        "suite_sha256": _SHA,
        "config_sha256": _SHA,
        "canaries": {"sample": {"valid_runs": 3, "successes": 3}},
    }


def _canary_payload(*, canary_id: str = "sample") -> dict:
    return {
        "canary_id": canary_id,
        "critical": True,
        "repository_url": "https://example.invalid/org/repo.git",
        "repository_url_sha256": _SHA,
        "base_sha": _SOURCE_SHA,
        "canary_fingerprint_sha256": _SHA,
        "prepared_image_digest": _PREPARED_DIGEST,
        "repetitions": 3,
    }


def _canaries_payload() -> dict:
    return {"schema_version": 1, "canaries": [_canary_payload()]}


def _manifest_canary_record_payload() -> dict:
    payload = _canary_payload()
    del payload["canary_id"]
    payload.update(
        baseline_valid=3, baseline_successes=3, candidate_valid=3, candidate_successes=3,
        verdict="pass",
    )
    return payload


def _manifest_payload() -> dict:
    return {
        "schema_version": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "run_qualock_version": "0.9.0",
        "exporter_qualock_version": "0.9.1",
        "qualification_id": "check-q",
        "baseline_version": "0.150.0",
        "candidate_version": "0.151.0",
        "verdict": "pass",
        "baseline_identity": _identity_payload(),
        "candidate_identity": _identity_payload(),
        "model": _model_pin_payload(),
        "baseline_lock_sha256": _SHA,
        "suite_sha256": _SHA,
        "config_sha256": _SHA,
        "run_order_sha256": _SHA,
        "completeness": _completeness_payload(),
        "canaries": {"sample": _manifest_canary_record_payload()},
        "files": {
            REPORT_FILENAME: {"sha256": _SHA, "size_bytes": 10},
            QUALIFICATION_FILENAME: {"sha256": _SHA, "size_bytes": 10},
            "baseline.lock": {"sha256": _SHA, "size_bytes": 10},
            PROVENANCE_FILENAME: {"sha256": _SHA, "size_bytes": 10},
            CANARIES_FILENAME: {"sha256": _SHA, "size_bytes": 10},
        },
    }


# --- PublicUsage -----------------------------------------------------------


def test_public_usage_accepts_exact_canonical_fields() -> None:
    usage = PublicUsage.model_validate(_usage_payload())
    assert usage.model_dump() == _usage_payload()


def test_public_usage_rejects_extra_field() -> None:
    payload = _usage_payload()
    payload["total_tokens"] = 30
    with pytest.raises(ValidationError):
        PublicUsage.model_validate(payload)


@pytest.mark.parametrize("field", list(_usage_payload().keys() - {"observed"}))
def test_public_usage_rejects_bool_for_int_fields(field: str) -> None:
    payload = _usage_payload()
    payload[field] = True
    with pytest.raises(ValidationError):
        PublicUsage.model_validate(payload)


@pytest.mark.parametrize("field", list(_usage_payload().keys() - {"observed"}))
def test_public_usage_rejects_negative_int_fields(field: str) -> None:
    payload = _usage_payload()
    payload[field] = -1
    with pytest.raises(ValidationError):
        PublicUsage.model_validate(payload)


def test_public_usage_is_frozen() -> None:
    usage = PublicUsage.model_validate(_usage_payload())
    with pytest.raises(ValidationError):
        usage.input_tokens = 99


# --- PublicAttempt -----------------------------------------------------------


def test_public_attempt_has_exact_field_set() -> None:
    assert set(PublicAttempt.model_fields) == {
        "side",
        "repetition",
        "success",
        "valid",
        "duration_ms",
        "usage",
        "events_sha256",
    }


def test_public_attempt_accepts_valid_payload() -> None:
    attempt = PublicAttempt.model_validate(_attempt_payload())
    assert attempt.events_sha256 == _SHA


@pytest.mark.parametrize(
    "extra_field",
    ["invalid_reason", "protected_path_violations", "events_jsonl"],
)
def test_public_attempt_rejects_forbidden_extra_fields(extra_field: str) -> None:
    payload = _attempt_payload()
    payload[extra_field] = "forbidden"
    with pytest.raises(ValidationError):
        PublicAttempt.model_validate(payload)


def test_public_attempt_rejects_non_positive_repetition() -> None:
    payload = _attempt_payload()
    payload["repetition"] = 0
    with pytest.raises(ValidationError):
        PublicAttempt.model_validate(payload)


def test_public_attempt_rejects_malformed_events_sha256() -> None:
    payload = _attempt_payload()
    payload["events_sha256"] = "not-a-hash"
    with pytest.raises(ValidationError):
        PublicAttempt.model_validate(payload)


# --- PublicExecution / PublicReport -----------------------------------------


def test_public_execution_rejects_unknown_verdict() -> None:
    payload = _execution_payload()
    payload["verdict"] = "unknown"
    with pytest.raises(ValidationError):
        PublicExecution.model_validate(payload)


def test_public_execution_rejects_over_bound_attempts() -> None:
    payload = _execution_payload()
    payload["attempts"] = [_attempt_payload() for _ in range(8193)]
    with pytest.raises(ValidationError):
        PublicExecution.model_validate(payload)


def test_public_report_round_trips_valid_payload() -> None:
    report = PublicReport.model_validate(_report_payload())
    assert report.verdict is Verdict.PASS
    assert report.executions[0].canary_id == "sample"


def test_public_report_rejects_over_bound_reason_text() -> None:
    payload = _report_payload()
    payload["reasons"] = ["x" * (TEXT_FIELD_MAX_BYTES + 1)]
    with pytest.raises(ValidationError):
        PublicReport.model_validate(payload)


# --- BundleCompleteness / PublicQualification --------------------------------


def test_bundle_completeness_round_trips() -> None:
    completeness = BundleCompleteness.model_validate(_completeness_payload())
    assert completeness.attempts_expected == 2


def test_public_qualification_round_trips_valid_payload() -> None:
    qualification = PublicQualification.model_validate(_qualification_payload())
    assert qualification.qualification_id == "check-q"


def test_public_qualification_rejects_unknown_field() -> None:
    payload = _qualification_payload()
    payload["reasons"] = []
    with pytest.raises(ValidationError):
        PublicQualification.model_validate(payload)


# --- PublicBaselineLock and its mirrors ---------------------------------------


def test_public_baseline_lock_round_trips_valid_payload() -> None:
    lock = PublicBaselineLock.model_validate(_baseline_lock_payload())
    assert lock.canaries["sample"].valid_runs == 3


def test_public_baseline_lock_rejects_nested_agent_extra_field() -> None:
    payload = _baseline_lock_payload()
    payload["agent"]["unexpected"] = "value"
    with pytest.raises(ValidationError):
        PublicBaselineLock.model_validate(payload)


def test_public_baseline_lock_rejects_nested_model_extra_field() -> None:
    payload = _baseline_lock_payload()
    payload["model"]["unexpected"] = "value"
    with pytest.raises(ValidationError):
        PublicBaselineLock.model_validate(payload)


def test_public_baseline_lock_rejects_nested_canary_stability_extra_field() -> None:
    payload = _baseline_lock_payload()
    payload["canaries"]["sample"]["unexpected"] = "value"
    with pytest.raises(ValidationError):
        PublicBaselineLock.model_validate(payload)


def test_public_baseline_lock_rejects_over_bound_canary_collection() -> None:
    payload = _baseline_lock_payload()
    payload["canaries"] = {
        f"canary-{i}": {"valid_runs": 1, "successes": 1} for i in range(MAX_CANARY_RECORDS + 1)
    }
    with pytest.raises(ValidationError):
        PublicBaselineLock.model_validate(payload)


def test_public_agent_pin_rejects_malformed_hash() -> None:
    payload = _baseline_lock_payload()["agent"]
    payload["binary_sha256"] = "short"
    with pytest.raises(ValidationError):
        PublicAgentPin.model_validate(payload)


def test_public_canary_stability_rejects_negative_counter() -> None:
    with pytest.raises(ValidationError):
        PublicCanaryStability.model_validate({"valid_runs": -1, "successes": 0})


def test_public_model_pin_round_trips() -> None:
    pin = PublicModelPin.model_validate(_model_pin_payload())
    assert pin.id == "gpt-5.6-terra"


# --- PublicCanary / PublicCanaries --------------------------------------------


def test_public_canary_round_trips_valid_payload() -> None:
    canary = PublicCanary.model_validate(_canary_payload())
    assert canary.repository_url == "https://example.invalid/org/repo.git"


def test_public_canary_rejects_over_bound_repository_url() -> None:
    payload = _canary_payload()
    payload["repository_url"] = "https://example.invalid/" + ("a" * 2048)
    with pytest.raises(ValidationError):
        PublicCanary.model_validate(payload)


def test_public_canaries_round_trips_valid_payload() -> None:
    canaries = PublicCanaries.model_validate(_canaries_payload())
    assert canaries.canaries[0].canary_id == "sample"


def test_public_canaries_rejects_duplicate_canary_ids() -> None:
    payload = _canaries_payload()
    payload["canaries"] = [_canary_payload(), _canary_payload()]
    with pytest.raises(ValidationError):
        PublicCanaries.model_validate(payload)


def test_public_canaries_rejects_over_bound_collection() -> None:
    payload = {
        "schema_version": 1,
        "canaries": [_canary_payload(canary_id=f"canary-{i}") for i in range(MAX_CANARY_RECORDS + 1)],
    }
    with pytest.raises(ValidationError):
        PublicCanaries.model_validate(payload)


# --- BundleFileRecord / ManifestCanaryRecord ----------------------------------


def test_bundle_file_record_round_trips() -> None:
    record = BundleFileRecord.model_validate({"sha256": _SHA, "size_bytes": 12})
    assert record.size_bytes == 12


def test_bundle_file_record_rejects_negative_size() -> None:
    with pytest.raises(ValidationError):
        BundleFileRecord.model_validate({"sha256": _SHA, "size_bytes": -1})


def test_manifest_canary_record_round_trips() -> None:
    record = ManifestCanaryRecord.model_validate(_manifest_canary_record_payload())
    assert record.verdict is Verdict.PASS


# --- EvidenceManifest ----------------------------------------------------------


def test_evidence_manifest_round_trips_valid_payload() -> None:
    manifest = EvidenceManifest.model_validate(_manifest_payload())
    assert manifest.qualification_id == "check-q"
    assert manifest.baseline_identity == RuntimeAgentIdentity.model_validate(_identity_payload())


def test_evidence_manifest_requires_distinct_run_and_exporter_version() -> None:
    payload = _manifest_payload()
    payload["exporter_qualock_version"] = payload["run_qualock_version"]
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(payload)


def test_evidence_manifest_allows_exporter_version_equal_to_baseline_lock_version() -> None:
    # No equality is implied between exporter_qualock_version and any other
    # version field except that it must differ from run_qualock_version.
    payload = _manifest_payload()
    payload["exporter_qualock_version"] = "0.0.1-legacy"
    manifest = EvidenceManifest.model_validate(payload)
    assert manifest.exporter_qualock_version == "0.0.1-legacy"


def test_evidence_manifest_requires_gemini_support_fingerprint() -> None:
    payload = _manifest_payload()
    payload["baseline_identity"] = _identity_payload(name="gemini")
    payload["candidate_identity"] = _identity_payload(name="gemini")
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(payload)


def test_evidence_manifest_rejects_unknown_top_level_field() -> None:
    payload = _manifest_payload()
    payload["extra"] = "value"
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(payload)


def test_evidence_manifest_rejects_over_bound_files_mapping() -> None:
    payload = _manifest_payload()
    payload["files"]["pricing.json"] = {"sha256": _SHA, "size_bytes": 1}
    payload["files"]["unexpected.json"] = {"sha256": _SHA, "size_bytes": 1}
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(payload)


def test_evidence_manifest_rejects_over_bound_canary_collection() -> None:
    payload = _manifest_payload()
    payload["canaries"] = {
        f"canary-{i}": _manifest_canary_record_payload() for i in range(MAX_CANARY_RECORDS + 1)
    }
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(payload)


# --- VerifiedEvidenceBundle ------------------------------------------------------


def _provenance_payload() -> dict:
    return {
        "schema_version": 1,
        "qualification_id": "check-q",
        "run_qualock_version": "0.9.0",
        "baseline_lock_sha256": _SHA,
        "baseline_identity": _identity_payload(),
        "candidate_identity": _identity_payload(),
        "model": _model_pin_payload(),
        "repetitions": 3,
        "run_order_sha256": _SHA,
        "canaries": [
            {
                "canary_id": "sample",
                "canary_fingerprint_sha256": _SHA,
                "repository_url_sha256": _SHA_B,
                "repository_base_sha": _SOURCE_SHA,
                "prepared_image_digest": _PREPARED_DIGEST,
            }
        ],
    }


def _verified_bundle_payload() -> dict:
    return {
        "manifest": _manifest_payload(),
        "manifest_sha256": _SHA,
        "report": _report_payload(),
        "qualification": _qualification_payload(),
        "baseline_lock": _baseline_lock_payload(),
        "provenance": _provenance_payload(),
        "canaries": _canaries_payload(),
    }


def test_verified_evidence_bundle_round_trips_valid_payload() -> None:
    bundle = VerifiedEvidenceBundle.model_validate(_verified_bundle_payload())
    assert bundle.manifest.qualification_id == "check-q"
    assert bundle.provenance.qualification_id == "check-q"


def test_verified_evidence_bundle_rejects_extra_field() -> None:
    payload = _verified_bundle_payload()
    payload["extra"] = "value"
    with pytest.raises(ValidationError):
        VerifiedEvidenceBundle.model_validate(payload)


# --- Fixed bundle filename/byte-cap constants -----------------------------------


def test_bundle_filenames_are_exact_v1_set() -> None:
    assert BUNDLE_FILENAMES == {
        "manifest.json",
        "report.json",
        "qualification.json",
        "baseline.lock",
        "provenance.json",
        "canaries.json",
        "pricing.json",
    }


# --- Reason/error contract -------------------------------------------------------


def test_evidence_bundle_reason_has_stable_string_values() -> None:
    assert EvidenceBundleReason.UNSAFE_PATH.value == "unsafe_path"
    assert EvidenceBundleReason.UNSAFE_REPOSITORY_URL.value == "unsafe_repository_url"


def test_evidence_bundle_error_carries_reason_and_no_raw_payload() -> None:
    error = EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, "report.json")
    assert error.reason is EvidenceBundleReason.MALFORMED_PAYLOAD
    assert "report.json" in str(error)
    assert isinstance(error, ValueError)


# --- publication_safe_repository_url ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/org/repo.git",
        "http://example.invalid/org/repo.git",
        "HTTPS://Example.invalid/org/repo.git",
    ],
)
def test_publication_safe_repository_url_accepts_and_returns_unchanged(url: str) -> None:
    assert publication_safe_repository_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@example.invalid/org/repo.git",
        "git@example.invalid:org/repo.git",
        "file:///local/path",
        "/local/path",
        "../relative/path",
        "https://user:pass@example.invalid/org/repo.git",
        "https://example.invalid/org/repo.git?token=abc",
        "https://example.invalid/org/repo.git#frag",
        "ftp://example.invalid/org/repo.git",
        "https:///org/repo.git",
        "https://example.invalid/org\x01repo.git",
        "https://" + "a" * 2048 + ".invalid/org/repo.git",
        "",
    ],
)
def test_publication_safe_repository_url_rejects_unsafe_forms(url: str) -> None:
    with pytest.raises(EvidenceBundleError) as exc_info:
        publication_safe_repository_url(url)
    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_REPOSITORY_URL


def test_publication_safe_repository_url_never_sanitizes() -> None:
    url = "https://example.invalid/org/repo.git"
    assert publication_safe_repository_url(url) is url
