"""Manually constructed V1 evidence bundles for offline verifier tests.

Every payload is built directly as a plain dict from the same aggregate
counts fed to the real `qualify_canary`/`qualify_suite` policy functions, so
a freshly built bundle is guaranteed internally self-consistent and verifies
cleanly. Tests tamper with the returned `BuiltBundle` to explore the
verifier's failure paths.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from qualock.evidence.bundle_io import canonical_json_file_bytes
from qualock.evidence.bundle_models import MANIFEST_FILENAME
from qualock.evidence.fingerprint import sha256_canonical
from qualock.qualification.models import CanaryAggregate, CanaryComparison

_UNSET = object()
from qualock.qualification.policy import qualify_canary, qualify_suite

AttemptState = Literal["success", "fail", "invalid", "missing"]


def _hex(seed: str, length: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:length]


def _sha256_hex(seed: str) -> str:
    return _hex(seed, 64)


def _source_sha(seed: str) -> str:
    return _hex(seed, 40)


def all_success(repetitions: int) -> tuple[AttemptState, ...]:
    return tuple("success" for _ in range(repetitions))


@dataclass(frozen=True)
class CanaryScenario:
    canary_id: str
    critical: bool = True
    repetitions: int = 3
    baseline: tuple[AttemptState, ...] = field(default_factory=lambda: all_success(3))
    candidate: tuple[AttemptState, ...] = field(default_factory=lambda: all_success(3))
    reason: str | None = None
    usage_observed: bool = True

    def __post_init__(self) -> None:
        if len(self.baseline) != self.repetitions or len(self.candidate) != self.repetitions:
            raise ValueError("scenario attempt states must match repetitions exactly")


def _usage_payload(seed: int, *, observed: bool = True) -> dict[str, object]:
    return {
        "input_tokens": 10 + seed,
        "cached_input_tokens": 1,
        "cache_write_input_tokens": 0,
        "output_tokens": 20 + seed,
        "reasoning_output_tokens": 0,
        "observed": observed,
    }


def _attempt_payload(
    canary_id: str,
    side: str,
    repetition: int,
    state: AttemptState,
    *,
    usage_observed: bool = True,
) -> dict[str, object]:
    valid = state in ("success", "fail")
    success = state == "success"
    events_seed = f"{canary_id}:{side}:{repetition}"
    return {
        "side": side,
        "repetition": repetition,
        "success": success,
        "valid": valid,
        "duration_ms": 1000 + repetition,
        "usage": _usage_payload(repetition, observed=usage_observed),
        "events_sha256": _sha256_hex(f"events:{events_seed}"),
    }


def _side_attempts(
    canary_id: str,
    side: str,
    states: tuple[AttemptState, ...],
    *,
    usage_observed: bool = True,
) -> list[dict[str, object]]:
    attempts = []
    for repetition, state in enumerate(states, start=1):
        if state == "missing":
            continue
        attempts.append(
            _attempt_payload(canary_id, side, repetition, state, usage_observed=usage_observed)
        )
    return attempts


def attempt_budget_skipped_reason(max_attempts: int, complete_canary_attempts: int) -> str:
    return (
        f"INCOMPLETE: skipped by attempt budget (max_attempts={max_attempts}, "
        f"complete_canary_attempts={complete_canary_attempts})"
    )


def token_budget_skipped_reason(max_tokens: int, observed_tokens: int | None) -> str:
    if observed_tokens is None:
        return (
            "INCOMPLETE: skipped because token usage was unavailable "
            f"for one or more attempts (max_tokens={max_tokens})"
        )
    return (
        f"INCOMPLETE: skipped by token budget "
        f"(max_tokens={max_tokens}, observed_tokens={observed_tokens})"
    )


def _aggregate(states: tuple[AttemptState, ...], expected_runs: int) -> CanaryAggregate:
    valid_runs = sum(1 for state in states if state in ("success", "fail"))
    successes = sum(1 for state in states if state == "success")
    return CanaryAggregate(valid_runs=valid_runs, successes=successes, expected_runs=expected_runs)


@dataclass(frozen=True)
class BuiltBundle:
    root: Path
    manifest: dict
    report: dict
    qualification: dict
    baseline_lock: dict
    provenance: dict
    canaries: dict
    pricing: dict | None


def _write_payload(root: Path, name: str, payload: object) -> tuple[str, int]:
    data = canonical_json_file_bytes(payload)
    (root / name).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return digest, len(data)


def write_manifest(root: Path, manifest_payload: dict) -> None:
    (root / MANIFEST_FILENAME).write_bytes(canonical_json_file_bytes(manifest_payload))


def replace_payload(built: BuiltBundle, filename: str, payload: object) -> None:
    """Rewrite one payload file's bytes and refresh its manifest hash/size in place."""
    digest, size = _write_payload(built.root, filename, payload)
    built.manifest["files"][filename] = {"sha256": digest, "size_bytes": size}
    write_manifest(built.root, built.manifest)


def load_json(root: Path, filename: str) -> dict:
    return json.loads((root / filename).read_text(encoding="utf-8"))


def build_bundle(
    root: Path,
    *,
    scenarios: tuple[CanaryScenario, ...] = (CanaryScenario(canary_id="sample"),),
    qualification_id: str = "check-q",
    baseline_version: str = "1.0.0",
    candidate_version: str = "1.1.0",
    agent_name: str = "codex",
    baseline_support_sha256: str | None = None,
    candidate_support_sha256: str | None = None,
    run_qualock_version: str = "1.2.3",
    exporter_qualock_version: str = "9.9.9",
    baseline_lock_qualock_version: str = "0.0.1-legacy",
    max_attempts: int | None = None,
    max_tokens: int | None = None,
    observed_tokens: int | None | object = _UNSET,
    include_pricing: bool = False,
) -> BuiltBundle:
    root.mkdir(parents=True, exist_ok=True)

    baseline_binary_sha = _sha256_hex("baseline-binary")
    candidate_binary_sha = _sha256_hex("candidate-binary")
    suite_sha = _sha256_hex("suite")
    config_sha = _sha256_hex("config")

    baseline_identity = {
        "name": agent_name,
        "version": baseline_version,
        "binary_sha256": baseline_binary_sha,
        "support_sha256": baseline_support_sha256,
    }
    candidate_identity = {
        "name": agent_name,
        "version": candidate_version,
        "binary_sha256": candidate_binary_sha,
        "support_sha256": candidate_support_sha256,
    }
    model_pin = {"id": "gpt-5.6-terra", "snapshot": None, "reasoning_effort": "high"}

    canary_records: dict[str, dict] = {}
    execution_payloads: list[dict] = []
    manifest_canary_records: dict[str, dict] = {}
    provenance_canaries: list[dict] = []
    comparisons = []
    run_order: list[list[object]] = []

    for scenario in sorted(scenarios, key=lambda item: item.canary_id):
        repository_url = f"https://example.invalid/org/{scenario.canary_id}.git"
        repository_url_sha256 = sha256_canonical(repository_url)
        base_sha = _source_sha(f"base:{scenario.canary_id}")
        canary_fingerprint_sha256 = _sha256_hex(f"fingerprint:{scenario.canary_id}")
        prepared_image_digest = "sha256:" + _sha256_hex(f"image:{scenario.canary_id}")

        baseline_attempts = _side_attempts(
            scenario.canary_id,
            "baseline",
            scenario.baseline,
            usage_observed=scenario.usage_observed,
        )
        candidate_attempts = _side_attempts(
            scenario.canary_id,
            "candidate",
            scenario.candidate,
            usage_observed=scenario.usage_observed,
        )
        for attempt in baseline_attempts:
            run_order.append([scenario.canary_id, "baseline", attempt["repetition"]])
        for attempt in candidate_attempts:
            run_order.append([scenario.canary_id, "candidate", attempt["repetition"]])

        baseline_aggregate = _aggregate(scenario.baseline, scenario.repetitions)
        candidate_aggregate = _aggregate(scenario.candidate, scenario.repetitions)
        comparison = qualify_canary(
            scenario.canary_id, baseline_aggregate, candidate_aggregate, critical=scenario.critical
        )
        if scenario.reason is not None:
            comparison = CanaryComparison(
                canary_id=comparison.canary_id,
                baseline=comparison.baseline,
                candidate=comparison.candidate,
                critical=comparison.critical,
                verdict=comparison.verdict,
                reason=scenario.reason,
                baseline_stable=comparison.baseline_stable,
            )
        comparisons.append(comparison)

        canary_records[scenario.canary_id] = {
            "canary_id": scenario.canary_id,
            "critical": scenario.critical,
            "repository_url": repository_url,
            "repository_url_sha256": repository_url_sha256,
            "base_sha": base_sha,
            "canary_fingerprint_sha256": canary_fingerprint_sha256,
            "prepared_image_digest": prepared_image_digest,
            "repetitions": scenario.repetitions,
        }

        manifest_canary_records[scenario.canary_id] = {
            "critical": scenario.critical,
            "repository_url": repository_url,
            "repository_url_sha256": repository_url_sha256,
            "base_sha": base_sha,
            "canary_fingerprint_sha256": canary_fingerprint_sha256,
            "prepared_image_digest": prepared_image_digest,
            "repetitions": scenario.repetitions,
            "baseline_valid": baseline_aggregate.valid_runs,
            "baseline_successes": baseline_aggregate.successes,
            "candidate_valid": candidate_aggregate.valid_runs,
            "candidate_successes": candidate_aggregate.successes,
            "verdict": comparison.verdict.value,
        }

        provenance_canaries.append(
            {
                "canary_id": scenario.canary_id,
                "canary_fingerprint_sha256": canary_fingerprint_sha256,
                "repository_url_sha256": repository_url_sha256,
                "repository_base_sha": base_sha,
                "prepared_image_digest": prepared_image_digest,
            }
        )

        execution_payloads.append(
            {
                "canary_id": scenario.canary_id,
                "critical": scenario.critical,
                "prepared_image_digest": prepared_image_digest,
                "attempts": baseline_attempts + candidate_attempts,
                "baseline_valid": baseline_aggregate.valid_runs,
                "baseline_successes": baseline_aggregate.successes,
                "candidate_valid": candidate_aggregate.valid_runs,
                "candidate_successes": candidate_aggregate.successes,
                "verdict": comparison.verdict.value,
                "reason": comparison.reason,
            }
        )

    suite_verdict = qualify_suite(comparisons)

    attempts_expected = sum(2 * scenario.repetitions for scenario in scenarios)
    attempts_used = sum(len(payload["attempts"]) for payload in execution_payloads)
    all_canaries_complete = all(
        len(payload["attempts"]) == 2 * canary_records[payload["canary_id"]]["repetitions"]
        for payload in execution_payloads
    )
    if observed_tokens is _UNSET:
        derived_tokens: int | None = 0
        for payload in execution_payloads:
            for attempt in payload["attempts"]:
                if derived_tokens is not None:
                    usage = attempt["usage"]
                    if usage["observed"]:
                        derived_tokens += usage["input_tokens"] + usage["output_tokens"]
                    else:
                        derived_tokens = None
        observed_tokens = derived_tokens

    completeness_payload = {
        "attempts_expected": attempts_expected,
        "attempts_used": attempts_used,
        "max_attempts": max_attempts,
        "max_tokens": max_tokens,
        "observed_tokens": observed_tokens,
        "all_canaries_complete": all_canaries_complete,
    }

    baseline_lock_payload = {
        "schema_version": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "agent": {
            "name": agent_name,
            "version": baseline_version,
            "binary_sha256": baseline_binary_sha,
            "support_sha256": baseline_support_sha256,
        },
        "model": model_pin,
        "qualock_version": baseline_lock_qualock_version,
        "suite_sha256": suite_sha,
        "config_sha256": config_sha,
        "canaries": {
            canary_id: {
                "valid_runs": manifest_canary_records[canary_id]["baseline_valid"],
                "successes": manifest_canary_records[canary_id]["baseline_successes"],
            }
            for canary_id in canary_records
        },
    }
    baseline_lock_sha256 = sha256_canonical(baseline_lock_payload)

    run_order_sha256 = sha256_canonical(run_order)

    repetitions_values = {scenario.repetitions for scenario in scenarios}
    if len(repetitions_values) != 1:
        raise ValueError("fixture scenarios must share one expected repetitions value")
    repetitions = next(iter(repetitions_values))

    provenance_payload = {
        "schema_version": 1,
        "qualification_id": qualification_id,
        "run_qualock_version": run_qualock_version,
        "baseline_lock_sha256": baseline_lock_sha256,
        "baseline_identity": baseline_identity,
        "candidate_identity": candidate_identity,
        "model": model_pin,
        "repetitions": repetitions,
        "run_order_sha256": run_order_sha256,
        "canaries": sorted(provenance_canaries, key=lambda item: item["canary_id"]),
    }

    report_payload = {
        "qualification_id": qualification_id,
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "verdict": suite_verdict.verdict.value,
        "reasons": list(suite_verdict.reasons),
        "run_order": run_order,
        "executions": execution_payloads,
        "completeness": completeness_payload,
    }

    qualification_payload = {
        "qualification_id": qualification_id,
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "verdict": suite_verdict.verdict.value,
        "run_order": run_order,
        "completeness": completeness_payload,
    }

    canaries_payload = {
        "schema_version": 1,
        "canaries": [canary_records[canary_id] for canary_id in sorted(canary_records)],
    }

    manifest_payload = {
        "schema_version": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "run_qualock_version": run_qualock_version,
        "exporter_qualock_version": exporter_qualock_version,
        "qualification_id": qualification_id,
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "verdict": suite_verdict.verdict.value,
        "baseline_identity": baseline_identity,
        "candidate_identity": candidate_identity,
        "model": model_pin,
        "baseline_lock_sha256": baseline_lock_sha256,
        "suite_sha256": suite_sha,
        "config_sha256": config_sha,
        "run_order_sha256": run_order_sha256,
        "completeness": completeness_payload,
        "canaries": manifest_canary_records,
        "files": {},
    }

    files: dict[str, dict] = {}
    for filename, payload in (
        ("report.json", report_payload),
        ("qualification.json", qualification_payload),
        ("baseline.lock", baseline_lock_payload),
        ("provenance.json", provenance_payload),
        ("canaries.json", canaries_payload),
    ):
        digest, size = _write_payload(root, filename, payload)
        files[filename] = {"sha256": digest, "size_bytes": size}

    pricing_payload: dict | None = None
    if include_pricing:
        pricing_payload = _pricing_payload(qualification_id, execution_payloads)
        digest, size = _write_payload(root, "pricing.json", pricing_payload)
        files["pricing.json"] = {"sha256": digest, "size_bytes": size}

    manifest_payload["files"] = files
    write_manifest(root, manifest_payload)

    return BuiltBundle(
        root=root,
        manifest=manifest_payload,
        report=report_payload,
        qualification=qualification_payload,
        baseline_lock=baseline_lock_payload,
        provenance=provenance_payload,
        canaries=canaries_payload,
        pricing=pricing_payload,
    )


def _pricing_payload(qualification_id: str, execution_payloads: list[dict]) -> dict:
    usage_detail_trust = []
    for execution in execution_payloads:
        for attempt in execution["attempts"]:
            usage_detail_trust.append(
                {
                    "canary_id": execution["canary_id"],
                    "side": attempt["side"],
                    "repetition": attempt["repetition"],
                    "cached_input_tokens_trust": "observed",
                    "cache_write_input_tokens_trust": "unobserved",
                }
            )
    return {
        "schema_version": 1,
        "availability": "priced",
        "basis": "api-equivalent-reference",
        "currency": "USD",
        "qualification_id": qualification_id,
        "run_started_at": "2026-09-08T12:00:00+00:00",
        "run_finished_at": "2026-09-08T12:05:00+00:00",
        "agent": "codex",
        "provider": "openai",
        "configured_model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "canonical_model": "gpt-5.6-terra",
        "model_identity_source": "configured_exact",
        "catalog_version": "2026-09-07.1",
        "rate_card_id": "openai:gpt-5.6-terra:standard:2026-09-07",
        "source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
        "source_checked_at": "2026-09-07",
        "effective_from": "2026-09-07",
        "effective_until": None,
        "rates_per_million": {
            "input_uncached": "2.00",
            "input_cached": "0.20",
            "cache_write_lower": "2.50",
            "cache_write_upper": "2.50",
            "output": "12.00",
        },
        "usage_detail_trust": usage_detail_trust,
        "limitations": [],
        "unavailable_reason": None,
    }
