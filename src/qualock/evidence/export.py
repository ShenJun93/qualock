"""Deterministic, portable evidence bundle export.

Converts a local completed qualification result into a standalone, safe-to-publish
flat V1 directory that can be verified offline by a third party without access
to the source project.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import qualock
from qualock.baseline.io import (
    BaselineStaleError,
    assert_suite_fresh,
    read_baseline_lock,
)
from qualock.evidence.bundle_io import canonical_json_file_bytes
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
    EvidenceManifest,
    PublicBaselineLock,
    PublicCanaries,
    PublicQualification,
    PublicReport,
    publication_safe_repository_url,
)
from qualock.evidence.fingerprint import sha256_canonical
from qualock.evidence.provenance import (
    EvidenceProvenance,
    EvidenceProvenanceError,
    read_evidence_provenance,
)
from qualock.evidence.verify import verify_evidence_bundle
from qualock.history.models import HistoricalAttempt, HistoricalExecution, LoadedReport
from qualock.pricing.sidecar import PricingSidecarPayloadError, parse_pricing_sidecar_payload
from qualock.project import (
    canary_fingerprint,
    config_fingerprint,
    load_project,
    project_dir,
    suite_fingerprint,
)
from qualock.qualification.models import CanaryAggregate, CanaryComparison
from qualock.qualification.policy import qualify_canary, qualify_suite


@dataclass(frozen=True)
class ExportedEvidenceBundle:
    path: Path
    qualification_id: str
    manifest_sha256: str


def _budget_skip_reasons(
    max_attempts: int | None,
    max_tokens: int | None,
    observed_tokens: int | None,
    repetitions: int,
    attempts_expected: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if max_attempts is not None and max_attempts < attempts_expected:
        reasons.append(
            "INCOMPLETE: skipped by attempt budget "
            f"(max_attempts={max_attempts}, complete_canary_attempts={2 * repetitions})"
        )
    if max_tokens is not None:
        if observed_tokens is None:
            reasons.append(
                "INCOMPLETE: skipped because token usage was unavailable "
                f"for one or more attempts (max_tokens={max_tokens})"
            )
        elif observed_tokens >= max_tokens:
            reasons.append(
                "INCOMPLETE: skipped by token budget "
                f"(max_tokens={max_tokens}, observed_tokens={observed_tokens})"
            )
    return tuple(reasons)


def export_evidence_bundle(
    root: Path,
    qualification_id: str,
    destination: Path,
    *,
    created_at: datetime | None = None,
) -> ExportedEvidenceBundle:
    # 1. Validate qualification_id as one safe directory name, not traversal
    if (
        not qualification_id
        or Path(qualification_id).name != qualification_id
        or qualification_id in {".", ".."}
        or "/" in qualification_id
        or "\\" in qualification_id
    ):
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "qualification_id")

    root = root.resolve()
    source_dir = (project_dir(root) / "results" / qualification_id).resolve()
    if not source_dir.is_dir():
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "qualification_id")

    # Refuse an existing destination and reject a destination inside source qualification dir
    dest = destination.resolve()
    if dest.exists():
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "destination")

    try:
        dest.relative_to(source_dir)
        is_inside = True
    except ValueError:
        is_inside = False
    if is_inside:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "destination")

    # 2. Require report.json, qualification.json, and evidence-provenance.json
    report_path = source_dir / REPORT_FILENAME
    qual_path = source_dir / QUALIFICATION_FILENAME
    prov_path = source_dir / "evidence-provenance.json"

    for path, filename in (
        (report_path, REPORT_FILENAME),
        (qual_path, QUALIFICATION_FILENAME),
        (prov_path, PROVENANCE_FILENAME),
    ):
        if not path.is_file():
            raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, filename)

    try:
        raw_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, REPORT_FILENAME) from exc

    try:
        raw_qualification = json.loads(qual_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, QUALIFICATION_FILENAME
        ) from exc

    try:
        provenance = read_evidence_provenance(prov_path)
    except EvidenceProvenanceError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, PROVENANCE_FILENAME
        ) from exc

    # 3. Load current project config, canaries, and baseline lock
    lock_path = project_dir(root) / BASELINE_LOCK_FILENAME
    if not lock_path.is_file():
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, BASELINE_LOCK_FILENAME
        )

    try:
        config, canaries = load_project(root)
        lock = read_baseline_lock(lock_path)
    except Exception as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, BASELINE_LOCK_FILENAME
        ) from exc

    # Require exact suite/config freshness against baseline.lock
    suite_sha = suite_fingerprint(canaries)
    config_sha = config_fingerprint(config)
    try:
        assert_suite_fresh(lock, suite_sha, config_sha)
    except BaselineStaleError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "suite_sha256") from exc

    # Require equality between baseline lock and persisted provenance
    lock_dump = lock.model_dump(mode="json")
    recomputed_lock_sha = sha256_canonical(lock_dump)
    if provenance.baseline_lock_sha256 != recomputed_lock_sha:
        raise EvidenceBundleError(
            EvidenceBundleReason.IDENTITY_MISMATCH, "baseline_lock_sha256"
        )
    if (
        provenance.baseline_identity.name != lock.agent.name
        or provenance.baseline_identity.version != lock.agent.version
        or provenance.baseline_identity.binary_sha256 != lock.agent.binary_sha256
        or provenance.baseline_identity.support_sha256 != lock.agent.support_sha256
    ):
        raise EvidenceBundleError(
            EvidenceBundleReason.IDENTITY_MISMATCH, "baseline_identity"
        )
    if (
        provenance.model.id != lock.model.id
        or provenance.model.snapshot != lock.model.snapshot
        or provenance.model.reasoning_effort != lock.model.reasoning_effort
    ):
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "model")
    if provenance.repetitions != config.qualification.repetitions:
        raise EvidenceBundleError(
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "repetitions"
        )

    # Cross-check qualification_id and versions across raw files and provenance
    if (
        provenance.qualification_id != qualification_id
        or raw_report.get("qualification_id") != qualification_id
        or raw_qualification.get("qualification_id") != qualification_id
    ):
        raise EvidenceBundleError(
            EvidenceBundleReason.IDENTITY_MISMATCH, "qualification_id"
        )

    if (
        raw_report.get("baseline_version") != provenance.baseline_identity.version
        or raw_qualification.get("baseline_version") != provenance.baseline_identity.version
    ):
        raise EvidenceBundleError(
            EvidenceBundleReason.IDENTITY_MISMATCH, "baseline_version"
        )

    if (
        raw_report.get("candidate_version") != provenance.candidate_identity.version
        or raw_qualification.get("candidate_version") != provenance.candidate_identity.version
    ):
        raise EvidenceBundleError(
            EvidenceBundleReason.IDENTITY_MISMATCH, "candidate_version"
        )

    report_run_order = [tuple(item) for item in raw_report.get("run_order", [])]
    qual_run_order = [tuple(item) for item in raw_qualification.get("run_order", [])]
    if report_run_order != qual_run_order:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "run_order")
    if sha256_canonical(report_run_order) != provenance.run_order_sha256:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "run_order")

    # 4. Construct canaries.json from current loaded canaries + persisted provenance
    prov_canaries_by_id = {c.canary_id: c for c in provenance.canaries}
    canaries_by_id = {c.id: c for c in canaries}

    if frozenset(canaries_by_id) != frozenset(prov_canaries_by_id):
        raise EvidenceBundleError(
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries"
        )

    public_canaries_list: list[dict[str, Any]] = []
    for canary_id in sorted(canaries_by_id):
        canary = canaries_by_id[canary_id]
        prov_canary = prov_canaries_by_id[canary_id]

        if canary_fingerprint(canary) != prov_canary.canary_fingerprint_sha256:
            raise EvidenceBundleError(
                EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries"
            )

        repo_url = canary.repository.url
        repo_url_sha = sha256_canonical(repo_url)
        if repo_url_sha != prov_canary.repository_url_sha256:
            raise EvidenceBundleError(
                EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "repository_url"
            )

        # Validate publication safe repository URL; fails closed if unsafe before dest creation
        safe_url = publication_safe_repository_url(repo_url)

        if canary.repository.base_sha != prov_canary.repository_base_sha:
            raise EvidenceBundleError(
                EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries"
            )

        public_canaries_list.append(
            {
                "canary_id": canary.id,
                "critical": canary.critical,
                "repository_url": safe_url,
                "repository_url_sha256": repo_url_sha,
                "base_sha": canary.repository.base_sha,
                "canary_fingerprint_sha256": prov_canary.canary_fingerprint_sha256,
                "prepared_image_digest": prov_canary.prepared_image_digest,
                "repetitions": config.qualification.repetitions,
            }
        )

    try:
        public_canaries = PublicCanaries.model_validate(
            {"schema_version": 1, "canaries": public_canaries_list}
        )
    except ValidationError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, CANARIES_FILENAME
        ) from exc

    # 5. Project report.json field-by-field from public attempts and qualification policy
    raw_executions = {
        execution["canary_id"]: execution
        for execution in raw_report.get("executions", [])
    }
    if frozenset(raw_executions) != frozenset(canaries_by_id):
        raise EvidenceBundleError(
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries"
        )

    attempts_expected = sum(2 * config.qualification.repetitions for _ in canaries)
    max_attempts = raw_report.get("max_attempts")
    if max_attempts is None and "completeness" in raw_report:
        max_attempts = raw_report["completeness"].get("max_attempts")
    max_tokens = raw_report.get("max_tokens")
    if max_tokens is None and "completeness" in raw_report:
        max_tokens = raw_report["completeness"].get("max_tokens")

    public_executions_list: list[dict[str, Any]] = []
    comparisons: list[CanaryComparison] = []
    manifest_canary_records: dict[str, Any] = {}

    all_public_attempts: list[dict[str, Any]] = []

    for canary_id in sorted(canaries_by_id):
        canary = canaries_by_id[canary_id]
        prov_canary = prov_canaries_by_id[canary_id]
        raw_execution = raw_executions[canary_id]

        public_attempts: list[dict[str, Any]] = []
        for raw_attempt in raw_execution.get("attempts", []):
            raw_usage = raw_attempt.get("usage", {})
            public_usage = {
                "input_tokens": int(raw_usage.get("input_tokens", 0)),
                "cached_input_tokens": int(raw_usage.get("cached_input_tokens", 0)),
                "cache_write_input_tokens": int(raw_usage.get("cache_write_input_tokens", 0)),
                "output_tokens": int(raw_usage.get("output_tokens", 0)),
                "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens", 0)),
                "observed": bool(raw_usage.get("observed", False)),
            }
            raw_events = raw_attempt.get("events_jsonl", "")
            events_sha = hashlib.sha256(raw_events.encode("utf-8")).hexdigest()
            attempt_dict = {
                "side": str(raw_attempt["side"]),
                "repetition": int(raw_attempt["repetition"]),
                "success": bool(raw_attempt["success"]),
                "valid": bool(raw_attempt["valid"]),
                "duration_ms": int(raw_attempt.get("duration_ms", 0)),
                "usage": public_usage,
                "events_sha256": events_sha,
            }
            public_attempts.append(attempt_dict)
            all_public_attempts.append(attempt_dict)

        # Derive aggregates directly from public attempts
        b_valid = sum(1 for a in public_attempts if a["side"] == "baseline" and a["valid"])
        b_success = sum(
            1 for a in public_attempts if a["side"] == "baseline" and a["valid"] and a["success"]
        )
        c_valid = sum(1 for a in public_attempts if a["side"] == "candidate" and a["valid"])
        c_success = sum(
            1 for a in public_attempts if a["side"] == "candidate" and a["valid"] and a["success"]
        )

        expected_rep = config.qualification.repetitions
        b_agg = CanaryAggregate(valid_runs=b_valid, successes=b_success, expected_runs=expected_rep)
        c_agg = CanaryAggregate(valid_runs=c_valid, successes=c_success, expected_runs=expected_rep)
        comp = qualify_canary(canary_id, b_agg, c_agg, critical=canary.critical)

        chosen_reason = comp.reason
        if not public_attempts:
            # Recompute allowed budget skip reasons
            # Calculate preliminary observed tokens
            prelim_tokens: int | None = 0
            for a in all_public_attempts:
                if prelim_tokens is not None:
                    if a["usage"]["observed"]:
                        prelim_tokens += a["usage"]["input_tokens"] + a["usage"]["output_tokens"]
                    else:
                        prelim_tokens = None
            allowed_reasons = _budget_skip_reasons(
                max_attempts, max_tokens, prelim_tokens, config.qualification.repetitions, attempts_expected
            )
            raw_reason = raw_execution.get("reason", "")
            if raw_reason in allowed_reasons:
                chosen_reason = raw_reason

        comp = CanaryComparison(
            canary_id=comp.canary_id,
            baseline=comp.baseline,
            candidate=comp.candidate,
            critical=comp.critical,
            verdict=comp.verdict,
            reason=chosen_reason,
            baseline_stable=comp.baseline_stable,
        )
        comparisons.append(comp)

        exec_dict = {
            "canary_id": canary_id,
            "critical": canary.critical,
            "prepared_image_digest": prov_canary.prepared_image_digest,
            "attempts": public_attempts,
            "baseline_valid": b_valid,
            "baseline_successes": b_success,
            "candidate_valid": c_valid,
            "candidate_successes": c_success,
            "verdict": comp.verdict.value,
            "reason": chosen_reason,
        }
        public_executions_list.append(exec_dict)

        manifest_canary_records[canary_id] = {
            "critical": canary.critical,
            "repository_url": canary.repository.url,
            "repository_url_sha256": prov_canary.repository_url_sha256,
            "base_sha": canary.repository.base_sha,
            "canary_fingerprint_sha256": prov_canary.canary_fingerprint_sha256,
            "prepared_image_digest": prov_canary.prepared_image_digest,
            "repetitions": config.qualification.repetitions,
            "baseline_valid": b_valid,
            "baseline_successes": b_success,
            "candidate_valid": c_valid,
            "candidate_successes": c_success,
            "verdict": comp.verdict.value,
        }

    suite_verdict = qualify_suite(comparisons)

    # Derive completeness
    attempts_used = sum(len(e["attempts"]) for e in public_executions_list)
    all_canaries_complete = all(
        len(e["attempts"]) == 2 * config.qualification.repetitions
        for e in public_executions_list
    )

    observed_tokens: int | None = 0
    for a in all_public_attempts:
        if observed_tokens is not None:
            if a["usage"]["observed"]:
                observed_tokens += a["usage"]["input_tokens"] + a["usage"]["output_tokens"]
            else:
                observed_tokens = None

    completeness_payload = {
        "attempts_expected": attempts_expected,
        "attempts_used": attempts_used,
        "max_attempts": max_attempts,
        "max_tokens": max_tokens,
        "observed_tokens": observed_tokens,
        "all_canaries_complete": all_canaries_complete,
    }

    report_payload = {
        "qualification_id": qualification_id,
        "baseline_version": provenance.baseline_identity.version,
        "candidate_version": provenance.candidate_identity.version,
        "verdict": suite_verdict.verdict.value,
        "reasons": list(suite_verdict.reasons),
        "run_order": report_run_order,
        "executions": public_executions_list,
        "completeness": completeness_payload,
    }
    try:
        public_report = PublicReport.model_validate(report_payload)
    except ValidationError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, REPORT_FILENAME
        ) from exc

    # 6. Construct qualification.json payload
    qual_payload = {
        "qualification_id": qualification_id,
        "baseline_version": provenance.baseline_identity.version,
        "candidate_version": provenance.candidate_identity.version,
        "verdict": suite_verdict.verdict.value,
        "run_order": report_run_order,
        "completeness": completeness_payload,
    }
    try:
        public_qualification = PublicQualification.model_validate(qual_payload)
    except ValidationError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, QUALIFICATION_FILENAME
        ) from exc

    # 7. Normalize baseline.lock and provenance.json
    try:
        public_baseline_lock = PublicBaselineLock.model_validate(lock_dump)
    except ValidationError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, BASELINE_LOCK_FILENAME
        ) from exc

    try:
        public_provenance = EvidenceProvenance.model_validate(
            provenance.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.MALFORMED_PAYLOAD, PROVENANCE_FILENAME
        ) from exc

    # 8. Check for optional pricing.json
    pricing_payload: dict[str, Any] | None = None
    source_pricing_path = source_dir / PRICING_FILENAME
    if source_pricing_path.is_file():
        try:
            raw_pricing = json.loads(source_pricing_path.read_text(encoding="utf-8"))
            loaded_report = LoadedReport(
                qualification_id=public_report.qualification_id,
                qualification_dir=source_dir,
                executions=tuple(
                    HistoricalExecution(
                        canary_id=execution.canary_id,
                        attempts=tuple(
                            HistoricalAttempt(
                                side=attempt.side,
                                repetition=attempt.repetition,
                                success=attempt.success,
                                valid=attempt.valid,
                                duration_ms=attempt.duration_ms,
                                input_tokens=attempt.usage.input_tokens,
                                output_tokens=attempt.usage.output_tokens,
                                usage_observed=attempt.usage.observed,
                                cached_input_tokens=attempt.usage.cached_input_tokens,
                                cache_write_input_tokens=attempt.usage.cache_write_input_tokens,
                                reasoning_output_tokens=attempt.usage.reasoning_output_tokens,
                            )
                            for attempt in execution.attempts
                        ),
                    )
                    for execution in public_report.executions
                ),
            )
            parse_pricing_sidecar_payload(loaded_report, raw_pricing)
            pricing_payload = raw_pricing
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PricingSidecarPayloadError):
            # Malformed pricing is omitted so quality evidence export is not blocked
            pricing_payload = None

    # 9. Write payload files to a sibling temporary directory
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".export-tmp-", dir=dest.parent))

    try:
        files: dict[str, dict[str, Any]] = {}
        payload_items = [
            (REPORT_FILENAME, public_report.model_dump(mode="json")),
            (QUALIFICATION_FILENAME, public_qualification.model_dump(mode="json")),
            (BASELINE_LOCK_FILENAME, public_baseline_lock.model_dump(mode="json")),
            (PROVENANCE_FILENAME, public_provenance.model_dump(mode="json")),
            (CANARIES_FILENAME, public_canaries.model_dump(mode="json")),
        ]
        if pricing_payload is not None:
            payload_items.append((PRICING_FILENAME, pricing_payload))

        for filename, payload in payload_items:
            payload_bytes = canonical_json_file_bytes(payload)
            (temp_dir / filename).write_bytes(payload_bytes)
            files[filename] = {
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "size_bytes": len(payload_bytes),
            }

        # 10. Construct manifest.json
        if created_at is None:
            created_at_dt = datetime.now(UTC)
        elif created_at.tzinfo is not None:
            created_at_dt = created_at.astimezone(UTC)
        else:
            created_at_dt = created_at.replace(tzinfo=UTC)

        manifest_payload = {
            "schema_version": 1,
            "created_at": created_at_dt.isoformat(),
            "run_qualock_version": provenance.run_qualock_version,
            "exporter_qualock_version": qualock.__version__,
            "qualification_id": qualification_id,
            "baseline_version": provenance.baseline_identity.version,
            "candidate_version": provenance.candidate_identity.version,
            "verdict": suite_verdict.verdict.value,
            "baseline_identity": provenance.baseline_identity.model_dump(mode="json"),
            "candidate_identity": provenance.candidate_identity.model_dump(mode="json"),
            "model": lock.model.model_dump(mode="json"),
            "baseline_lock_sha256": provenance.baseline_lock_sha256,
            "suite_sha256": lock.suite_sha256,
            "config_sha256": lock.config_sha256,
            "run_order_sha256": provenance.run_order_sha256,
            "completeness": completeness_payload,
            "canaries": {k: manifest_canary_records[k] for k in sorted(manifest_canary_records)},
            "files": {k: files[k] for k in sorted(files)},
        }

        try:
            manifest_model = EvidenceManifest.model_validate(manifest_payload)
        except ValidationError as exc:
            raise EvidenceBundleError(
                EvidenceBundleReason.MALFORMED_MANIFEST, MANIFEST_FILENAME
            ) from exc

        manifest_bytes = canonical_json_file_bytes(manifest_model.model_dump(mode="json"))
        (temp_dir / MANIFEST_FILENAME).write_bytes(manifest_bytes)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        # 11. Self-verify temporary bundle BEFORE publication
        verify_evidence_bundle(temp_dir)

        # 12. Atomic same-filesystem publish
        os.replace(temp_dir, dest)

        return ExportedEvidenceBundle(
            path=dest,
            qualification_id=qualification_id,
            manifest_sha256=manifest_sha256,
        )

    except Exception:
        # Cleanup owned temporary directory on any failure; leave destination absent
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
