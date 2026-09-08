import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qualock.config.models import AgentConfig, ModelConfig, QualockConfig
from qualock.pricing.catalog import CATALOG_VERSION
from qualock.pricing.resolve import build_pricing_payload
from qualock.pricing.sidecar import write_pricing_sidecar
from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Usage,
    Verdict,
)


def configured(agent: str, model: str, effort: str) -> QualockConfig:
    return QualockConfig(
        agent=AgentConfig(name=agent),
        model=ModelConfig(id=model, reasoning_effort=effort),
    )


def one_attempt_result(
    *, qualification_id: str = "q-1", events_jsonl: str = "", side: str = "candidate"
) -> QualificationResult:
    attempt = AttemptResult(
        side=side,
        repetition=1,
        success=True,
        valid=True,
        duration_ms=1,
        usage=Usage(input_tokens=10, output_tokens=1, observed=True),
        events_jsonl=events_jsonl,
    )
    execution = CanaryExecution(
        "canary-a", True, "sha256:test", (attempt,), 0, 1, 0, 1, Verdict.PASS, "test"
    )
    return QualificationResult(
        qualification_id, "1.0.0", "1.0.1", Verdict.PASS, (execution,), (), ()
    )


_SCHEMA_KEYS = {
    "schema_version", "availability", "basis", "currency", "qualification_id",
    "run_started_at", "run_finished_at", "agent", "provider", "configured_model",
    "reasoning_effort", "canonical_model", "model_identity_source", "catalog_version",
    "rate_card_id", "source_url", "source_checked_at", "effective_from",
    "effective_until", "rates_per_million", "usage_detail_trust", "limitations",
    "unavailable_reason",
}


def test_priced_payload_has_exact_schema_and_decimal_strings() -> None:
    config = configured("codex", "gpt-5.6-terra", "high")
    result = one_attempt_result()
    started = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    finished = datetime(2026, 9, 8, 12, 5, tzinfo=UTC)

    payload = build_pricing_payload(config, result, started, finished)

    assert set(payload) == _SCHEMA_KEYS
    assert payload["availability"] == "priced"
    assert payload["schema_version"] == 1
    assert payload["basis"] == "api-equivalent-reference"
    assert payload["currency"] == "USD"
    assert payload["qualification_id"] == "q-1"
    assert payload["agent"] == "codex"
    assert payload["provider"] == "openai"
    assert payload["configured_model"] == "gpt-5.6-terra"
    assert payload["reasoning_effort"] == "high"
    assert payload["canonical_model"] == "gpt-5.6-terra"
    assert payload["model_identity_source"] == "configured_exact"
    assert payload["catalog_version"] == CATALOG_VERSION
    assert payload["rate_card_id"] == "openai:gpt-5.6-terra:standard:2026-09-07"
    assert payload["source_url"] == "https://developers.openai.com/api/docs/models/gpt-5.6-terra"
    assert payload["source_checked_at"] == "2026-09-07"
    assert payload["effective_from"] == "2026-09-07"
    assert payload["effective_until"] is None
    assert payload["unavailable_reason"] is None
    assert set(payload["rates_per_million"]) == {
        "input_uncached", "input_cached", "cache_write_lower", "cache_write_upper", "output",
    }
    assert all(
        value is None or isinstance(value, str)
        for value in payload["rates_per_million"].values()
    )
    assert len(payload["usage_detail_trust"]) == 1
    json.dumps(payload)


def test_unknown_model_payload_is_unavailable_with_complete_trust() -> None:
    config = configured("codex", "gpt-5.6-turbo", "high")
    result = one_attempt_result()
    started = datetime(2026, 9, 8, tzinfo=UTC)
    finished = datetime(2026, 9, 8, 1, tzinfo=UTC)

    payload = build_pricing_payload(config, result, started, finished)

    assert set(payload) == _SCHEMA_KEYS
    assert payload["availability"] == "unavailable"
    assert payload["unavailable_reason"] == "unknown_model"
    assert payload["canonical_model"] is None
    assert payload["model_identity_source"] == "unavailable"
    assert payload["rates_per_million"] is None
    assert payload["rate_card_id"] is None
    assert payload["source_url"] is None
    assert payload["source_checked_at"] is None
    assert payload["effective_from"] is None
    assert payload["effective_until"] is None
    assert payload["limitations"] == []
    assert len(payload["usage_detail_trust"]) == 1


def test_malformed_claude_model_payload_has_fixed_reason_without_raw_text() -> None:
    config = configured("claude", "sonnet", "high")
    result = one_attempt_result(events_jsonl="not json\n")
    started = datetime(2026, 9, 8, tzinfo=UTC)
    finished = datetime(2026, 9, 8, 1, tzinfo=UTC)

    payload = build_pricing_payload(config, result, started, finished)

    assert payload["unavailable_reason"] == "malformed_model_evidence"
    assert payload["canonical_model"] is None
    assert "not json" not in json.dumps(payload)


def test_invalid_capture_time_precedes_model_and_rate_failures() -> None:
    config = configured("codex", "gpt-5.6-turbo", "high")
    result = one_attempt_result()
    naive_started = datetime(2026, 9, 8, 12, 0)  # noqa: DTZ001 - naive-datetime rejection is the test
    finished = datetime(2026, 9, 8, 12, 5, tzinfo=UTC)

    payload = build_pricing_payload(config, result, naive_started, finished)

    assert payload["unavailable_reason"] == "invalid_capture_time"
    assert payload["model_identity_source"] == "unavailable"
    assert payload["canonical_model"] is None

    reversed_payload = build_pricing_payload(
        configured("codex", "gpt-5.6-terra", "high"),
        result,
        datetime(2026, 9, 8, 12, 5, tzinfo=UTC),
        datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )
    assert reversed_payload["unavailable_reason"] == "invalid_capture_time"


def test_temporal_lookup_precedence_for_no_card_boundary_and_same_card() -> None:
    config = configured("antigravity", "gemini-3.8-flash-low", "medium")
    result = one_attempt_result()

    before_floor = datetime(2026, 9, 1, tzinfo=UTC)
    no_card = build_pricing_payload(config, result, before_floor, before_floor)
    assert no_card["unavailable_reason"] == "no_rate_card"
    assert no_card["canonical_model"] == "gemini-3.8-flash"
    assert no_card["model_identity_source"] == "agent_exact_mapping"

    boundary_start = datetime(2026, 12, 31, tzinfo=UTC)
    boundary_finish = datetime(2027, 1, 1, tzinfo=UTC)
    crossed = build_pricing_payload(config, result, boundary_start, boundary_finish)
    assert crossed["unavailable_reason"] == "rate_boundary_crossed"
    assert crossed["canonical_model"] == "gemini-3.8-flash"

    same_start = datetime(2026, 9, 10, tzinfo=UTC)
    same_finish = datetime(2026, 9, 10, 1, tzinfo=UTC)
    priced = build_pricing_payload(config, result, same_start, same_finish)
    assert priced["availability"] == "priced"
    assert priced["rate_card_id"] == "google:gemini-3.8-flash:standard:through-2026-12-31"


def test_overlapping_catalog_runtime_lookup_fails_closed_to_no_rate_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualock.pricing.resolve as resolve_module

    monkeypatch.setattr(
        resolve_module, "resolve_rate_card", lambda provider, model, instant: None
    )

    config = configured("codex", "gpt-5.6-terra", "high")
    result = one_attempt_result()
    started = datetime(2026, 9, 8, tzinfo=UTC)
    finished = datetime(2026, 9, 8, 1, tzinfo=UTC)

    payload = build_pricing_payload(config, result, started, finished)

    assert payload["unavailable_reason"] == "no_rate_card"
    assert payload["canonical_model"] == "gpt-5.6-terra"


def test_rate_boundary_crossed_keeps_resolved_model_source() -> None:
    events = '{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'
    result = one_attempt_result(events_jsonl=events)
    config = configured("claude", "sonnet", "high")

    payload = build_pricing_payload(
        config,
        result,
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 8, tzinfo=UTC),
    )

    assert payload["unavailable_reason"] == "rate_boundary_crossed"
    assert payload["canonical_model"] == "claude-sonnet-5"
    assert payload["model_identity_source"] == "runtime_observed"


def test_payload_normalizes_aware_offsets_to_utc() -> None:
    config = configured("codex", "gpt-5.6-terra", "high")
    result = one_attempt_result()
    tz = timezone(timedelta(hours=9))
    started = datetime(2026, 9, 8, 21, 0, tzinfo=tz)
    finished = datetime(2026, 9, 8, 22, 0, tzinfo=tz)

    payload = build_pricing_payload(config, result, started, finished)

    assert payload["run_started_at"] == "2026-09-08T12:00:00+00:00"
    assert payload["run_finished_at"] == "2026-09-08T13:00:00+00:00"


def test_successful_sidecar_publish_exposes_complete_sorted_bytes(tmp_path: Path) -> None:
    payload = {"b": 1, "a": 2}

    path = write_pricing_sidecar(tmp_path, payload)

    assert path == tmp_path / "pricing.json"
    assert path.read_bytes() == (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    assert not tuple(tmp_path.glob(".pricing.*.tmp"))


def test_existing_pricing_sidecar_is_never_overwritten(tmp_path: Path) -> None:
    existing = tmp_path / "pricing.json"
    existing.write_bytes(b"original")

    with pytest.raises(OSError):
        write_pricing_sidecar(tmp_path, {"new": True})

    assert existing.read_bytes() == b"original"
    assert not tuple(tmp_path.glob(".pricing.*.tmp"))


def test_publish_failure_leaves_no_final_or_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualock.pricing.sidecar as sidecar_module

    def fail_link(_src: object, _dst: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(sidecar_module.os, "link", fail_link)

    with pytest.raises(OSError):
        write_pricing_sidecar(tmp_path, {"a": 1})

    assert not (tmp_path / "pricing.json").exists()
    assert not tuple(tmp_path.glob(".pricing.*.tmp"))


def test_sidecar_capture_does_not_rewrite_existing_artifacts(tmp_path: Path) -> None:
    first_payload = {"schema_version": 1, "value": "first"}
    write_pricing_sidecar(tmp_path, first_payload)
    original_bytes = (tmp_path / "pricing.json").read_bytes()

    with pytest.raises(OSError):
        write_pricing_sidecar(tmp_path, {"schema_version": 1, "value": "second"})

    assert (tmp_path / "pricing.json").read_bytes() == original_bytes
