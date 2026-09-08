import json
from pathlib import Path

from qualock.history.models import (
    HistoricalAttempt,
    HistoricalExecution,
    HistorySummary,
    LoadedReport,
)
from qualock.pricing.models import (
    PricingHistory,
    PricingLoadFailure,
)
from qualock.pricing.sidecar import scan_pricing


def priced_payload(qualification_id: str = "q-1") -> dict[str, object]:
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
        "usage_detail_trust": [
            {
                "canary_id": "canary-a",
                "side": "baseline",
                "repetition": 1,
                "cached_input_tokens_trust": "observed",
                "cache_write_input_tokens_trust": "unobserved",
            }
        ],
        "limitations": ["standard limitation text"],
        "unavailable_reason": None,
    }


def unavailable_payload(
    qualification_id: str = "q-1", reason: str = "unknown_model"
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "availability": "unavailable",
        "basis": "api-equivalent-reference",
        "currency": "USD",
        "qualification_id": qualification_id,
        "run_started_at": "2026-09-08T12:00:00+00:00",
        "run_finished_at": "2026-09-08T12:05:00+00:00",
        "agent": "codex",
        "provider": "openai",
        "configured_model": "gpt-5.6-turbo",
        "reasoning_effort": "high",
        "canonical_model": None,
        "model_identity_source": "unavailable",
        "catalog_version": "2026-09-07.1",
        "rate_card_id": None,
        "source_url": None,
        "source_checked_at": None,
        "effective_from": None,
        "effective_until": None,
        "rates_per_million": None,
        "usage_detail_trust": [
            {
                "canary_id": "canary-a",
                "side": "baseline",
                "repetition": 1,
                "cached_input_tokens_trust": "unobserved",
                "cache_write_input_tokens_trust": "unobserved",
            }
        ],
        "limitations": [],
        "unavailable_reason": reason,
    }


def loaded_report(
    tmp_path: Path, qualification_id: str = "q-1", *, name: str | None = None
) -> LoadedReport:
    qualification_dir = tmp_path / (name or qualification_id)
    qualification_dir.mkdir()
    execution = HistoricalExecution(
        canary_id="canary-a",
        attempts=(
            HistoricalAttempt(
                side="baseline",
                repetition=1,
                success=True,
                valid=True,
                duration_ms=100,
                input_tokens=10,
                output_tokens=5,
                usage_observed=True,
            ),
        ),
    )
    return LoadedReport(
        qualification_id=qualification_id,
        qualification_dir=qualification_dir,
        executions=(execution,),
    )


def loaded_summary(tmp_path: Path, qualification_id: str = "q-1") -> HistorySummary:
    return HistorySummary(loaded=(loaded_report(tmp_path, qualification_id),), ignored=())


def write_pricing_text(qualification_dir: Path, text: str) -> None:
    (qualification_dir / "pricing.json").write_text(text, encoding="utf-8")


def write_pricing(qualification_dir: Path, payload: dict[str, object]) -> None:
    write_pricing_text(qualification_dir, json.dumps(payload))


def test_missing_pricing_sidecar_is_older_unpinned_not_failure(tmp_path: Path) -> None:
    summary = loaded_summary(tmp_path, "q-1")

    assert scan_pricing(summary) == PricingHistory(
        records=(),
        older_unpinned_qualification_ids=("q-1",),
        failures=(),
    )


def test_invalid_pricing_json_is_fixed_failure_and_sibling_loads(tmp_path: Path) -> None:
    broken = loaded_report(tmp_path, "q-1")
    sibling = loaded_report(tmp_path, "q-2")
    write_pricing_text(broken.qualification_dir, "{not JSON")
    summary = HistorySummary(loaded=(broken, sibling), ignored=())

    history = scan_pricing(summary)

    assert history.failures == (
        PricingLoadFailure(broken.qualification_id, broken.qualification_dir, "invalid pricing JSON"),
    )
    assert history.older_unpinned_qualification_ids == ("q-2",)
    assert history.records == ()


def test_non_utf8_sidecar_is_unreadable_and_sibling_loads(tmp_path: Path) -> None:
    broken = loaded_report(tmp_path, "q-1")
    sibling = loaded_report(tmp_path, "q-2")
    (broken.qualification_dir / "pricing.json").write_bytes(b"\xff\xfe\x00not utf-8")
    write_pricing(sibling.qualification_dir, priced_payload("q-2"))
    summary = HistorySummary(loaded=(broken, sibling), ignored=())

    history = scan_pricing(summary)

    assert history.failures == (
        PricingLoadFailure(
            broken.qualification_id, broken.qualification_dir, "unreadable pricing sidecar"
        ),
    )
    assert len(history.records) == 1
    assert history.records[0].qualification_id == "q-2"


def test_non_object_and_unsupported_schema_have_fixed_reasons(tmp_path: Path) -> None:
    non_object = loaded_report(tmp_path, "q-1")
    write_pricing_text(non_object.qualification_dir, json.dumps([1, 2, 3]))
    summary = HistorySummary(loaded=(non_object,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure(
            "q-1", non_object.qualification_dir, "pricing sidecar is not a JSON object"
        ),
    )

    bad_schema = loaded_report(tmp_path, "q-2")
    payload = priced_payload("q-2")
    payload["schema_version"] = 2
    write_pricing(bad_schema.qualification_dir, payload)
    summary = HistorySummary(loaded=(bad_schema,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-2", bad_schema.qualification_dir, "unsupported pricing schema"),
    )


def test_qualification_id_mismatch_cannot_rebind_report(tmp_path: Path) -> None:
    report = loaded_report(tmp_path, "q-1")
    write_pricing(report.qualification_dir, priced_payload("q-other"))
    summary = HistorySummary(loaded=(report,), ignored=())

    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", report.qualification_dir, "pricing qualification_id mismatch"),
    )


def test_valid_unavailable_sidecar_loads_normally(tmp_path: Path) -> None:
    report = loaded_report(tmp_path, "q-1")
    write_pricing(report.qualification_dir, unavailable_payload("q-1"))
    summary = HistorySummary(loaded=(report,), ignored=())

    history = scan_pricing(summary)

    assert history.failures == ()
    assert history.older_unpinned_qualification_ids == ()
    assert len(history.records) == 1
    record = history.records[0]
    assert record.availability == "unavailable"
    assert record.unavailable_reason == "unknown_model"
    assert record.canonical_model is None
    assert record.rates is None


def test_malformed_typed_fields_collapse_to_fixed_reason(tmp_path: Path) -> None:
    report = loaded_report(tmp_path, "q-1")
    payload = priced_payload("q-1")
    payload["currency"] = "EUR"
    write_pricing(report.qualification_dir, payload)
    summary = HistorySummary(loaded=(report,), ignored=())

    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
    )


def test_raw_exception_path_and_payload_never_enter_failure_reason(tmp_path: Path) -> None:
    report = loaded_report(tmp_path, "q-1")
    payload = priced_payload("q-1")
    payload["rates_per_million"]["input_uncached"] = "SECRET_TOKEN_XYZ"
    write_pricing(report.qualification_dir, payload)
    summary = HistorySummary(loaded=(report,), ignored=())

    history = scan_pricing(summary)

    assert len(history.failures) == 1
    reason = history.failures[0].reason
    assert reason == "malformed pricing sidecar"
    assert "SECRET_TOKEN_XYZ" not in reason
    assert str(report.qualification_dir) not in reason


def test_scanner_uses_only_successfully_loaded_reports(tmp_path: Path) -> None:
    stray_dir = tmp_path / "stray-not-loaded"
    stray_dir.mkdir()
    write_pricing(stray_dir, priced_payload("q-stray"))
    summary = HistorySummary(loaded=(), ignored=())

    assert scan_pricing(summary) == PricingHistory(
        records=(), older_unpinned_qualification_ids=(), failures=()
    )


def test_sidecar_requires_exact_basis_currency_and_agent_provider_map(tmp_path: Path) -> None:
    for field, value in (("basis", "other-basis"), ("currency", "EUR"), ("provider", "anthropic")):
        report = loaded_report(tmp_path, "q-1", name=f"dir-{field}")
        payload = priced_payload("q-1")
        payload[field] = value
        write_pricing(report.qualification_dir, payload)
        summary = HistorySummary(loaded=(report,), ignored=())

        assert scan_pricing(summary).failures == (
            PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
        )


def test_sidecar_model_source_reason_combinations_are_closed(tmp_path: Path) -> None:
    unknown_source = loaded_report(tmp_path, "q-1", name="dir-unknown-source")
    payload = priced_payload("q-1")
    payload["model_identity_source"] = "not-a-real-source"
    write_pricing(unknown_source.qualification_dir, payload)
    summary = HistorySummary(loaded=(unknown_source,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", unknown_source.qualification_dir, "malformed pricing sidecar"),
    )

    wrong_coupling = loaded_report(tmp_path, "q-2", name="dir-wrong-coupling")
    payload = unavailable_payload("q-2", reason="unknown_model")
    payload["canonical_model"] = "gpt-5.6-terra"
    payload["model_identity_source"] = "configured_exact"
    write_pricing(wrong_coupling.qualification_dir, payload)
    summary = HistorySummary(loaded=(wrong_coupling,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-2", wrong_coupling.qualification_dir, "malformed pricing sidecar"),
    )

    no_rate_card = loaded_report(tmp_path, "q-3", name="dir-no-rate-card")
    payload = unavailable_payload("q-3", reason="no_rate_card")
    payload["canonical_model"] = None
    payload["model_identity_source"] = "unavailable"
    write_pricing(no_rate_card.qualification_dir, payload)
    summary = HistorySummary(loaded=(no_rate_card,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-3", no_rate_card.qualification_dir, "malformed pricing sidecar"),
    )


def test_priced_and_unavailable_nullability_are_mutually_exclusive(tmp_path: Path) -> None:
    priced_with_reason = loaded_report(tmp_path, "q-1", name="dir-priced-reason")
    payload = priced_payload("q-1")
    payload["unavailable_reason"] = "no_rate_card"
    write_pricing(priced_with_reason.qualification_dir, payload)
    summary = HistorySummary(loaded=(priced_with_reason,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure(
            "q-1", priced_with_reason.qualification_dir, "malformed pricing sidecar"
        ),
    )

    unavailable_with_card = loaded_report(tmp_path, "q-2", name="dir-unavailable-card")
    payload = unavailable_payload("q-2")
    payload["rate_card_id"] = "openai:gpt-5.6-terra:standard:2026-09-07"
    write_pricing(unavailable_with_card.qualification_dir, payload)
    summary = HistorySummary(loaded=(unavailable_with_card,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure(
            "q-2", unavailable_with_card.qualification_dir, "malformed pricing sidecar"
        ),
    )


def test_catalog_version_must_be_nonempty_string(tmp_path: Path) -> None:
    for bad_value in ("", 7, None):
        report = loaded_report(tmp_path, "q-1", name=f"dir-{bad_value}")
        payload = priced_payload("q-1")
        payload["catalog_version"] = bad_value
        write_pricing(report.qualification_dir, payload)
        summary = HistorySummary(loaded=(report,), ignored=())

        assert scan_pricing(summary).failures == (
            PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
        )


def test_sidecar_rates_reject_non_strings_nan_infinity_negative_and_reversed_range(
    tmp_path: Path,
) -> None:
    bad_rates = [
        {"input_uncached": 2.0},
        {"input_uncached": "NaN"},
        {"input_uncached": "Infinity"},
        {"input_uncached": "-1.00"},
        {"cache_write_lower": "5.00", "cache_write_upper": "1.00"},
    ]
    for index, overrides in enumerate(bad_rates):
        report = loaded_report(tmp_path, "q-1", name=f"dir-rate-{index}")
        payload = priced_payload("q-1")
        payload["rates_per_million"].update(overrides)
        write_pricing(report.qualification_dir, payload)
        summary = HistorySummary(loaded=(report,), ignored=())

        assert scan_pricing(summary).failures == (
            PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
        ), f"expected failure for override {overrides}"


def test_sidecar_timestamps_require_aware_ordered_instants_and_normalize_utc(
    tmp_path: Path,
) -> None:
    naive = loaded_report(tmp_path, "q-1", name="dir-naive")
    payload = priced_payload("q-1")
    payload["run_started_at"] = "2026-09-08T12:00:00"
    write_pricing(naive.qualification_dir, payload)
    summary = HistorySummary(loaded=(naive,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", naive.qualification_dir, "malformed pricing sidecar"),
    )

    reversed_window = loaded_report(tmp_path, "q-2", name="dir-reversed")
    payload = priced_payload("q-2")
    payload["run_started_at"] = "2026-09-08T12:05:00+00:00"
    payload["run_finished_at"] = "2026-09-08T12:00:00+00:00"
    write_pricing(reversed_window.qualification_dir, payload)
    summary = HistorySummary(loaded=(reversed_window,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-2", reversed_window.qualification_dir, "malformed pricing sidecar"),
    )

    offset = loaded_report(tmp_path, "q-3", name="dir-offset")
    payload = priced_payload("q-3")
    payload["run_started_at"] = "2026-09-08T21:00:00+09:00"
    payload["run_finished_at"] = "2026-09-08T21:05:00+09:00"
    write_pricing(offset.qualification_dir, payload)
    summary = HistorySummary(loaded=(offset,), ignored=())
    record = scan_pricing(summary).records[0]
    assert record.run_started_at.isoformat() == "2026-09-08T12:00:00+00:00"
    assert record.run_finished_at.isoformat() == "2026-09-08T12:05:00+00:00"


def test_sidecar_effective_interval_must_be_ordered(tmp_path: Path) -> None:
    report = loaded_report(tmp_path, "q-1")
    payload = priced_payload("q-1")
    payload["effective_from"] = "2026-12-31"
    payload["effective_until"] = "2026-09-07"
    write_pricing(report.qualification_dir, payload)
    summary = HistorySummary(loaded=(report,), ignored=())

    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
    )


def test_missing_duplicate_or_extra_trust_identity_is_malformed(tmp_path: Path) -> None:
    missing = loaded_report(tmp_path, "q-1", name="dir-missing")
    payload = priced_payload("q-1")
    payload["usage_detail_trust"] = []
    write_pricing(missing.qualification_dir, payload)
    summary = HistorySummary(loaded=(missing,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-1", missing.qualification_dir, "malformed pricing sidecar"),
    )

    duplicate = loaded_report(tmp_path, "q-2", name="dir-duplicate")
    payload = priced_payload("q-2")
    payload["usage_detail_trust"] = [
        payload["usage_detail_trust"][0],
        dict(payload["usage_detail_trust"][0]),
    ]
    write_pricing(duplicate.qualification_dir, payload)
    summary = HistorySummary(loaded=(duplicate,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-2", duplicate.qualification_dir, "malformed pricing sidecar"),
    )

    extra = loaded_report(tmp_path, "q-3", name="dir-extra")
    payload = priced_payload("q-3")
    payload["usage_detail_trust"].append(
        {
            "canary_id": "canary-b",
            "side": "candidate",
            "repetition": 1,
            "cached_input_tokens_trust": "observed",
            "cache_write_input_tokens_trust": "unobserved",
        }
    )
    write_pricing(extra.qualification_dir, payload)
    summary = HistorySummary(loaded=(extra,), ignored=())
    assert scan_pricing(summary).failures == (
        PricingLoadFailure("q-3", extra.qualification_dir, "malformed pricing sidecar"),
    )


def test_trust_repetition_is_positive_and_matches_one_based_attempt(tmp_path: Path) -> None:
    for bad_repetition in (0, -1, True):
        report = loaded_report(tmp_path, "q-1", name=f"dir-rep-{bad_repetition}")
        payload = priced_payload("q-1")
        payload["usage_detail_trust"][0]["repetition"] = bad_repetition
        write_pricing(report.qualification_dir, payload)
        summary = HistorySummary(loaded=(report,), ignored=())

        assert scan_pricing(summary).failures == (
            PricingLoadFailure("q-1", report.qualification_dir, "malformed pricing sidecar"),
        )


def test_same_rate_card_id_equal_snapshots_all_remain_records(tmp_path: Path) -> None:
    first = loaded_report(tmp_path, "q-1", name="dir-first")
    second = loaded_report(tmp_path, "q-2", name="dir-second")
    write_pricing(first.qualification_dir, priced_payload("q-1"))
    write_pricing(second.qualification_dir, priced_payload("q-2"))
    summary = HistorySummary(loaded=(first, second), ignored=())

    history = scan_pricing(summary)

    assert history.failures == ()
    assert {record.qualification_id for record in history.records} == {"q-1", "q-2"}


def test_same_rate_card_id_different_snapshot_invalidates_every_owner(tmp_path: Path) -> None:
    first = loaded_report(tmp_path, "q-1", name="dir-first")
    second = loaded_report(tmp_path, "q-2", name="dir-second")
    first_payload = priced_payload("q-1")
    second_payload = priced_payload("q-2")
    second_payload["limitations"] = ["standard limitation text", "an extra limitation"]
    write_pricing(first.qualification_dir, first_payload)
    write_pricing(second.qualification_dir, second_payload)
    summary = HistorySummary(loaded=(first, second), ignored=())

    history = scan_pricing(summary)

    assert history.records == ()
    assert set(history.failures) == {
        PricingLoadFailure("q-1", first.qualification_dir, "rate-card snapshot mismatch"),
        PricingLoadFailure("q-2", second.qualification_dir, "rate-card snapshot mismatch"),
    }
