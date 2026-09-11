"""Pure, offline, read-only verification of a standalone V1 evidence bundle.

The verifier never trusts stored aggregates or verdicts: every policy-facing
number is rederived from public attempts and fed through the existing pure
qualification policy functions. It performs no network, subprocess, Docker,
clock, or current-project-state access, and it never writes to the bundle.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from qualock.evidence.bundle_io import (
    inspect_bundle_files,
    read_bounded_regular_file,
)
from qualock.evidence.bundle_models import (
    BASELINE_LOCK_FILENAME,
    CANARIES_FILENAME,
    FILE_MAX_BYTES,
    MANIFEST_FILENAME,
    MANIFEST_MAX_BYTES,
    PRICING_FILENAME,
    PROVENANCE_FILENAME,
    QUALIFICATION_FILENAME,
    REPORT_FILENAME,
    REQUIRED_PAYLOAD_FILENAMES,
    EvidenceBundleError,
    EvidenceBundleReason,
    EvidenceManifest,
    PublicBaselineLock,
    PublicCanaries,
    PublicCanary,
    PublicExecution,
    PublicQualification,
    PublicReport,
    VerifiedEvidenceBundle,
    publication_safe_repository_url,
)
from qualock.evidence.fingerprint import sha256_canonical
from qualock.evidence.provenance import EvidenceProvenance
from qualock.history.models import HistoricalAttempt, HistoricalExecution, LoadedReport
from qualock.pricing.sidecar import PricingSidecarPayloadError, parse_pricing_sidecar_payload
from qualock.qualification.models import CanaryAggregate, CanaryComparison
from qualock.qualification.policy import qualify_canary, qualify_suite

_VALID_SIDES = frozenset({"baseline", "candidate"})

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def verify_evidence_bundle(root: Path) -> VerifiedEvidenceBundle:
    names = inspect_bundle_files(root)

    manifest_bytes = read_bounded_regular_file(root, MANIFEST_FILENAME, max_bytes=MANIFEST_MAX_BYTES)
    manifest = _parse_model(
        EvidenceManifest, manifest_bytes, MANIFEST_FILENAME, EvidenceBundleReason.MALFORMED_MANIFEST
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    _verify_inventory(names, manifest)
    payload_buffers = _verify_payload_hashes(root, manifest)

    report = _parse_model(
        PublicReport,
        payload_buffers[REPORT_FILENAME],
        REPORT_FILENAME,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    qualification = _parse_model(
        PublicQualification,
        payload_buffers[QUALIFICATION_FILENAME],
        QUALIFICATION_FILENAME,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    baseline_lock = _parse_model(
        PublicBaselineLock,
        payload_buffers[BASELINE_LOCK_FILENAME],
        BASELINE_LOCK_FILENAME,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    provenance = _parse_model(
        EvidenceProvenance,
        payload_buffers[PROVENANCE_FILENAME],
        PROVENANCE_FILENAME,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    canaries = _parse_model(
        PublicCanaries,
        payload_buffers[CANARIES_FILENAME],
        CANARIES_FILENAME,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )

    # `baseline.lock`'s agent mirror has no gemini-support validator of its own
    # (unlike EvidenceManifest/EvidenceProvenance), so it is checked explicitly.
    if baseline_lock.agent.name == "gemini" and baseline_lock.agent.support_sha256 is None:
        raise EvidenceBundleError(EvidenceBundleReason.INVALID_GEMINI_SUPPORT, "baseline_lock.agent")

    _verify_global_identities(manifest, report, qualification, baseline_lock, provenance)

    canaries_by_id: dict[str, PublicCanary] = {canary.canary_id: canary for canary in canaries.canaries}
    _verify_canary_bindings(manifest, provenance, canaries_by_id)
    aggregates = _verify_report_executions(manifest, report, canaries_by_id)
    _verify_policy(manifest, report, qualification, canaries_by_id, aggregates)
    _verify_completeness(report, canaries_by_id)

    if PRICING_FILENAME in manifest.files:
        _verify_pricing(payload_buffers[PRICING_FILENAME], root, report)

    return VerifiedEvidenceBundle(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        report=report,
        qualification=qualification,
        baseline_lock=baseline_lock,
        provenance=provenance,
        canaries=canaries,
    )


def _parse_model(
    model_cls: type[_ModelT], data: bytes, filename: str, reason: EvidenceBundleReason
) -> _ModelT:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceBundleError(reason, filename) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceBundleError(reason, filename) from exc
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise EvidenceBundleError(reason, filename) from exc


def _verify_inventory(names: tuple[str, ...], manifest: EvidenceManifest) -> None:
    actual_payloads = frozenset(names) - {MANIFEST_FILENAME}
    manifest_files = frozenset(manifest.files)
    if actual_payloads != manifest_files:
        raise EvidenceBundleError(EvidenceBundleReason.INVENTORY_MISMATCH, "files")
    if not frozenset(REQUIRED_PAYLOAD_FILENAMES).issubset(manifest_files):
        raise EvidenceBundleError(EvidenceBundleReason.INVENTORY_MISMATCH, "files")


def _verify_payload_hashes(root: Path, manifest: EvidenceManifest) -> dict[str, bytes]:
    buffers: dict[str, bytes] = {}
    for name, record in manifest.files.items():
        max_bytes = FILE_MAX_BYTES.get(name)
        if max_bytes is None:
            raise EvidenceBundleError(EvidenceBundleReason.INVENTORY_MISMATCH, "files")
        data = read_bounded_regular_file(root, name, max_bytes=max_bytes)
        digest = hashlib.sha256(data).hexdigest()
        if digest != record.sha256 or len(data) != record.size_bytes:
            raise EvidenceBundleError(EvidenceBundleReason.DIGEST_MISMATCH, name)
        buffers[name] = data
    return buffers


def _require_all_equal(values: Sequence[object], reason: EvidenceBundleReason, label: str) -> None:
    first = values[0]
    if any(value != first for value in values[1:]):
        raise EvidenceBundleError(reason, label)


def _verify_global_identities(
    manifest: EvidenceManifest,
    report: PublicReport,
    qualification: PublicQualification,
    baseline_lock: PublicBaselineLock,
    provenance: EvidenceProvenance,
) -> None:
    _require_all_equal(
        [manifest.qualification_id, report.qualification_id, qualification.qualification_id, provenance.qualification_id],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "qualification_id",
    )
    _require_all_equal(
        [
            manifest.baseline_version,
            report.baseline_version,
            qualification.baseline_version,
            manifest.baseline_identity.version,
        ],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "baseline_version",
    )
    _require_all_equal(
        [
            manifest.candidate_version,
            report.candidate_version,
            qualification.candidate_version,
            manifest.candidate_identity.version,
        ],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "candidate_version",
    )
    # exporter_qualock_version is validated structurally by EvidenceManifest
    # only; it is never required to equal run_qualock_version or the lock.
    _require_all_equal(
        [manifest.run_qualock_version, provenance.run_qualock_version],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "run_qualock_version",
    )

    recomputed_lock_sha = sha256_canonical(baseline_lock.model_dump(mode="json"))
    _require_all_equal(
        [recomputed_lock_sha, manifest.baseline_lock_sha256, provenance.baseline_lock_sha256],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "baseline_lock_sha256",
    )
    _require_all_equal(
        [manifest.suite_sha256, baseline_lock.suite_sha256],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "suite_sha256",
    )
    _require_all_equal(
        [manifest.config_sha256, baseline_lock.config_sha256],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "config_sha256",
    )

    model_tuples = [
        (manifest.model.id, manifest.model.snapshot, manifest.model.reasoning_effort),
        (baseline_lock.model.id, baseline_lock.model.snapshot, baseline_lock.model.reasoning_effort),
        (provenance.model.id, provenance.model.snapshot, provenance.model.reasoning_effort),
    ]
    _require_all_equal(model_tuples, EvidenceBundleReason.IDENTITY_MISMATCH, "model")

    if manifest.baseline_identity != provenance.baseline_identity:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "baseline_identity")
    if manifest.candidate_identity != provenance.candidate_identity:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "candidate_identity")

    baseline_identity_tuple = (
        manifest.baseline_identity.name,
        manifest.baseline_identity.version,
        manifest.baseline_identity.binary_sha256,
        manifest.baseline_identity.support_sha256,
    )
    baseline_lock_agent_tuple = (
        baseline_lock.agent.name,
        baseline_lock.agent.version,
        baseline_lock.agent.binary_sha256,
        baseline_lock.agent.support_sha256,
    )
    if baseline_identity_tuple != baseline_lock_agent_tuple:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "baseline_identity")

    recomputed_run_order_sha = sha256_canonical(report.run_order)
    _require_all_equal(
        [recomputed_run_order_sha, manifest.run_order_sha256, provenance.run_order_sha256],
        EvidenceBundleReason.IDENTITY_MISMATCH,
        "run_order",
    )
    if report.run_order != qualification.run_order:
        raise EvidenceBundleError(EvidenceBundleReason.IDENTITY_MISMATCH, "run_order")

    if report.completeness != qualification.completeness or report.completeness != manifest.completeness:
        raise EvidenceBundleError(EvidenceBundleReason.COMPLETENESS_MISMATCH, "completeness")


def _verify_canary_bindings(
    manifest: EvidenceManifest,
    provenance: EvidenceProvenance,
    canaries_by_id: dict[str, PublicCanary],
) -> None:
    manifest_ids = frozenset(manifest.canaries)
    provenance_by_id = {canary.canary_id: canary for canary in provenance.canaries}
    if len(provenance_by_id) != len(provenance.canaries):
        raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")
    canary_ids = frozenset(canaries_by_id)
    if not (manifest_ids == frozenset(provenance_by_id) == canary_ids):
        raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")

    for canary_id, canary in canaries_by_id.items():
        manifest_record = manifest.canaries[canary_id]
        provenance_record = provenance_by_id[canary_id]

        publication_safe_repository_url(canary.repository_url)
        if canary.repository_url != manifest_record.repository_url:
            raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")

        recomputed_repo_sha = sha256_canonical(canary.repository_url)
        _require_all_equal(
            [
                recomputed_repo_sha,
                canary.repository_url_sha256,
                manifest_record.repository_url_sha256,
                provenance_record.repository_url_sha256,
            ],
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH,
            "canaries",
        )
        _require_all_equal(
            [canary.base_sha, manifest_record.base_sha, provenance_record.repository_base_sha],
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH,
            "canaries",
        )
        _require_all_equal(
            [canary.critical, manifest_record.critical],
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH,
            "canaries",
        )
        _require_all_equal(
            [
                canary.canary_fingerprint_sha256,
                manifest_record.canary_fingerprint_sha256,
                provenance_record.canary_fingerprint_sha256,
            ],
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH,
            "canaries",
        )
        _require_all_equal(
            [
                canary.prepared_image_digest,
                manifest_record.prepared_image_digest,
                provenance_record.prepared_image_digest,
            ],
            EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH,
            "canaries",
        )
        if canary.repetitions != manifest_record.repetitions:
            raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")
        if canary.repetitions != provenance.repetitions:
            raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")


def _derive_execution_aggregates(
    execution: PublicExecution, expected_repetitions: int
) -> tuple[CanaryAggregate, CanaryAggregate]:
    slots: set[tuple[str, int]] = set()
    baseline_valid = baseline_successes = 0
    candidate_valid = candidate_successes = 0
    for attempt in execution.attempts:
        if attempt.side not in _VALID_SIDES:
            raise EvidenceBundleError(EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH, "attempts")
        if not (1 <= attempt.repetition <= expected_repetitions):
            raise EvidenceBundleError(EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH, "attempts")
        slot = (attempt.side, attempt.repetition)
        if slot in slots:
            raise EvidenceBundleError(EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH, "attempts")
        slots.add(slot)

        if attempt.side == "baseline":
            if attempt.valid:
                baseline_valid += 1
                if attempt.success:
                    baseline_successes += 1
        else:
            if attempt.valid:
                candidate_valid += 1
                if attempt.success:
                    candidate_successes += 1

    baseline = CanaryAggregate(
        valid_runs=baseline_valid, successes=baseline_successes, expected_runs=expected_repetitions
    )
    candidate = CanaryAggregate(
        valid_runs=candidate_valid, successes=candidate_successes, expected_runs=expected_repetitions
    )
    return baseline, candidate


def _verify_report_executions(
    manifest: EvidenceManifest,
    report: PublicReport,
    canaries_by_id: dict[str, PublicCanary],
) -> dict[str, tuple[CanaryAggregate, CanaryAggregate]]:
    execution_ids = [execution.canary_id for execution in report.executions]
    if len(set(execution_ids)) != len(execution_ids):
        raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")
    if frozenset(execution_ids) != frozenset(canaries_by_id):
        raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")

    aggregates: dict[str, tuple[CanaryAggregate, CanaryAggregate]] = {}
    for execution in report.executions:
        canary = canaries_by_id[execution.canary_id]
        manifest_record = manifest.canaries[execution.canary_id]

        if execution.critical != canary.critical or execution.critical != manifest_record.critical:
            raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")
        if (
            execution.prepared_image_digest != canary.prepared_image_digest
            or execution.prepared_image_digest != manifest_record.prepared_image_digest
        ):
            raise EvidenceBundleError(EvidenceBundleReason.CANARY_PROVENANCE_MISMATCH, "canaries")

        baseline_aggregate, candidate_aggregate = _derive_execution_aggregates(execution, canary.repetitions)
        if (
            execution.baseline_valid != baseline_aggregate.valid_runs
            or execution.baseline_successes != baseline_aggregate.successes
            or execution.candidate_valid != candidate_aggregate.valid_runs
            or execution.candidate_successes != candidate_aggregate.successes
        ):
            raise EvidenceBundleError(EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH, "aggregate")
        if (
            manifest_record.baseline_valid != baseline_aggregate.valid_runs
            or manifest_record.baseline_successes != baseline_aggregate.successes
            or manifest_record.candidate_valid != candidate_aggregate.valid_runs
            or manifest_record.candidate_successes != candidate_aggregate.successes
        ):
            raise EvidenceBundleError(EvidenceBundleReason.ATTEMPT_LAYOUT_MISMATCH, "aggregate")

        aggregates[execution.canary_id] = (baseline_aggregate, candidate_aggregate)
    return aggregates


def _budget_skip_reasons(
    report: PublicReport, canary: PublicCanary, attempts_expected: int
) -> tuple[str, ...]:
    reasons: list[str] = []
    completeness = report.completeness
    if completeness.max_attempts is not None and completeness.max_attempts < attempts_expected:
        reasons.append(
            "INCOMPLETE: skipped by attempt budget "
            f"(max_attempts={completeness.max_attempts}, complete_canary_attempts={2 * canary.repetitions})"
        )
    if completeness.max_tokens is not None:
        if completeness.observed_tokens is None:
            reasons.append(
                "INCOMPLETE: skipped because token usage was unavailable "
                f"for one or more attempts (max_tokens={completeness.max_tokens})"
            )
        elif completeness.observed_tokens >= completeness.max_tokens:
            reasons.append(
                "INCOMPLETE: skipped by token budget "
                f"(max_tokens={completeness.max_tokens}, observed_tokens={completeness.observed_tokens})"
            )
    return tuple(reasons)


def _verify_policy(
    manifest: EvidenceManifest,
    report: PublicReport,
    qualification: PublicQualification,
    canaries_by_id: dict[str, PublicCanary],
    aggregates: dict[str, tuple[CanaryAggregate, CanaryAggregate]],
) -> None:
    attempts_expected = sum(2 * canary.repetitions for canary in canaries_by_id.values())
    comparisons = []
    for execution in report.executions:
        canary = canaries_by_id[execution.canary_id]
        baseline_aggregate, candidate_aggregate = aggregates[execution.canary_id]
        comparison = qualify_canary(
            execution.canary_id, baseline_aggregate, candidate_aggregate, critical=canary.critical
        )

        if execution.verdict != comparison.verdict:
            raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "verdict")

        allowed_reasons: tuple[str, ...] = (comparison.reason,)
        if not execution.attempts:
            allowed_reasons += _budget_skip_reasons(report, canary, attempts_expected)

        if execution.reason not in allowed_reasons:
            raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "verdict")

        if manifest.canaries[execution.canary_id].verdict != comparison.verdict:
            raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "verdict")

        if execution.reason != comparison.reason:
            comparison = CanaryComparison(
                canary_id=comparison.canary_id,
                baseline=comparison.baseline,
                candidate=comparison.candidate,
                critical=comparison.critical,
                verdict=comparison.verdict,
                reason=execution.reason,
                baseline_stable=comparison.baseline_stable,
            )
        comparisons.append(comparison)

    suite_verdict = qualify_suite(comparisons)
    if (
        report.verdict != suite_verdict.verdict
        or report.reasons != suite_verdict.reasons
        or qualification.verdict != suite_verdict.verdict
        or manifest.verdict != suite_verdict.verdict
    ):
        raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "verdict")


def _verify_completeness(report: PublicReport, canaries_by_id: dict[str, PublicCanary]) -> None:
    attempts_expected = sum(2 * canary.repetitions for canary in canaries_by_id.values())
    attempts_used = sum(len(execution.attempts) for execution in report.executions)
    all_complete = all(
        len(execution.attempts) == 2 * canaries_by_id[execution.canary_id].repetitions
        for execution in report.executions
    )

    recomputed_observed_tokens: int | None = 0
    for execution in report.executions:
        for attempt in execution.attempts:
            if recomputed_observed_tokens is not None:
                if attempt.usage.observed:
                    recomputed_observed_tokens += (
                        attempt.usage.input_tokens + attempt.usage.output_tokens
                    )
                else:
                    recomputed_observed_tokens = None

    stored = report.completeness
    if (
        stored.attempts_expected != attempts_expected
        or stored.attempts_used != attempts_used
        or stored.all_canaries_complete != all_complete
        or stored.observed_tokens != recomputed_observed_tokens
    ):
        raise EvidenceBundleError(EvidenceBundleReason.COMPLETENESS_MISMATCH, "completeness")

    if stored.max_attempts is not None and stored.attempts_used > stored.max_attempts:
        raise EvidenceBundleError(EvidenceBundleReason.COMPLETENESS_MISMATCH, "completeness")

    if not all_complete:
        if stored.max_attempts is None and stored.max_tokens is None:
            raise EvidenceBundleError(EvidenceBundleReason.COMPLETENESS_MISMATCH, "completeness")

        attempt_budget_stopping = (
            stored.max_attempts is not None and stored.max_attempts < attempts_expected
        )
        token_budget_stopping = stored.max_tokens is not None and (
            stored.observed_tokens is None or stored.observed_tokens >= stored.max_tokens
        )
        if not (attempt_budget_stopping or token_budget_stopping):
            raise EvidenceBundleError(EvidenceBundleReason.COMPLETENESS_MISMATCH, "completeness")


def _verify_pricing(pricing_bytes: bytes, root: Path, report: PublicReport) -> None:
    try:
        pricing_payload = json.loads(pricing_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, PRICING_FILENAME) from exc

    loaded_report = LoadedReport(
        qualification_id=report.qualification_id,
        qualification_dir=root,
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
            for execution in report.executions
        ),
    )
    try:
        parse_pricing_sidecar_payload(loaded_report, pricing_payload)
    except PricingSidecarPayloadError as exc:
        raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, PRICING_FILENAME) from exc
