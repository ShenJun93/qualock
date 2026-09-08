import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from qualock.history.models import HistorySummary, LoadedReport
from qualock.pricing.catalog import parse_rate_components
from qualock.pricing.models import (
    AttemptUsageTrust,
    PricingHistory,
    PricingLoadFailure,
    PricingSidecar,
)


def write_pricing_sidecar(qualification_dir: Path, payload: dict[str, object]) -> Path:
    final_path = qualification_dir / "pricing.json"
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")

    fd, temp_name = tempfile.mkstemp(
        prefix=".pricing.", suffix=".tmp", dir=qualification_dir
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return final_path


_UNREADABLE = "unreadable pricing sidecar"
_INVALID_JSON = "invalid pricing JSON"
_NOT_OBJECT = "pricing sidecar is not a JSON object"
_UNSUPPORTED_SCHEMA = "unsupported pricing schema"
_ID_MISMATCH = "pricing qualification_id mismatch"
_MALFORMED = "malformed pricing sidecar"
_SNAPSHOT_MISMATCH = "rate-card snapshot mismatch"

_AGENT_PROVIDERS = {"codex": "openai", "claude": "anthropic", "antigravity": "google"}
_MODEL_SOURCES = frozenset(
    {"runtime_observed", "configured_exact", "documented_alias", "agent_exact_mapping", "unavailable"}
)
_SUCCESS_MODEL_SOURCES = frozenset(
    {"runtime_observed", "configured_exact", "documented_alias", "agent_exact_mapping"}
)
_MODEL_UNAVAILABLE_REASONS = frozenset(
    {
        "unknown_model",
        "missing_observed_model",
        "inconsistent_observed_model",
        "malformed_model_evidence",
        "invalid_capture_time",
    }
)
_RATE_UNAVAILABLE_REASONS = frozenset({"no_rate_card", "rate_boundary_crossed"})
_UNAVAILABLE_REASONS = _MODEL_UNAVAILABLE_REASONS | _RATE_UNAVAILABLE_REASONS
_TRUST_SIDES = frozenset({"baseline", "candidate"})
_TRUST_LEVELS = frozenset({"observed", "known_zero", "unobserved"})
_TRUST_KEYS = frozenset(
    {
        "canary_id",
        "side",
        "repetition",
        "cached_input_tokens_trust",
        "cache_write_input_tokens_trust",
    }
)


class _SidecarError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _nonempty_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError  # noqa: TRY004
    return value


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError  # noqa: TRY004
    return date.fromisoformat(value)


def _parse_aware_utc_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError  # noqa: TRY004
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _validate_availability_semantics(
    *,
    availability: str,
    canonical_model: str | None,
    model_identity_source: str,
    rate_card_id: str | None,
    source_url: str | None,
    source_checked_at: date | None,
    effective_from: date | None,
    effective_until: date | None,
    rates: object,
    limitations: tuple[str, ...],
    unavailable_reason: str | None,
) -> None:
    if availability == "priced":
        if unavailable_reason is not None:
            raise ValueError
        if canonical_model is None or rate_card_id is None or source_url is None:
            raise ValueError
        if source_checked_at is None or rates is None:
            raise ValueError
        if model_identity_source not in _SUCCESS_MODEL_SOURCES:
            raise ValueError
        return

    if unavailable_reason is None or unavailable_reason not in _UNAVAILABLE_REASONS:
        raise ValueError
    if rate_card_id is not None or source_url is not None or source_checked_at is not None:
        raise ValueError
    if effective_from is not None or effective_until is not None:
        raise ValueError
    if rates is not None:
        raise ValueError
    if limitations != ():
        raise ValueError
    if unavailable_reason in _MODEL_UNAVAILABLE_REASONS:
        if canonical_model is not None or model_identity_source != "unavailable":
            raise ValueError
    else:
        if canonical_model is None or model_identity_source not in _SUCCESS_MODEL_SOURCES:
            raise ValueError


def _parse_usage_detail_trust(
    raw: object, loaded: LoadedReport
) -> tuple[AttemptUsageTrust, ...]:
    if not isinstance(raw, list):
        raise ValueError  # noqa: TRY004

    records: list[AttemptUsageTrust] = []
    identities: set[tuple[str, str, int]] = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry.keys()) != _TRUST_KEYS:
            raise ValueError

        canary_id = entry["canary_id"]
        side = entry["side"]
        repetition = entry["repetition"]
        cache_read = entry["cached_input_tokens_trust"]
        cache_write = entry["cache_write_input_tokens_trust"]

        if not isinstance(canary_id, str) or not canary_id:
            raise ValueError
        if side not in _TRUST_SIDES:
            raise ValueError
        if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition <= 0:
            raise ValueError
        if cache_read not in _TRUST_LEVELS or cache_write not in _TRUST_LEVELS:
            raise ValueError

        identity = (canary_id, side, repetition)
        if identity in identities:
            raise ValueError
        identities.add(identity)
        records.append(AttemptUsageTrust(canary_id, side, repetition, cache_read, cache_write))

    expected = {
        (execution.canary_id, attempt.side, attempt.repetition)
        for execution in loaded.executions
        for attempt in execution.attempts
    }
    if identities != expected:
        raise ValueError

    return tuple(records)


def _parse_body(loaded: LoadedReport, payload: dict[str, object]) -> PricingSidecar:
    availability = payload["availability"]
    if availability not in ("priced", "unavailable"):
        raise ValueError
    if payload["basis"] != "api-equivalent-reference":
        raise ValueError
    if payload["currency"] != "USD":
        raise ValueError

    agent = payload["agent"]
    provider = payload["provider"]
    if _AGENT_PROVIDERS.get(agent) != provider:
        raise ValueError

    configured_model = _nonempty_str(payload["configured_model"])
    reasoning_effort = _nonempty_str(payload["reasoning_effort"])
    catalog_version = _nonempty_str(payload["catalog_version"])

    canonical_model = _optional_str(payload["canonical_model"])
    model_identity_source = payload["model_identity_source"]
    if model_identity_source not in _MODEL_SOURCES:
        raise ValueError

    run_started_at = _parse_aware_utc_datetime(payload["run_started_at"])
    run_finished_at = _parse_aware_utc_datetime(payload["run_finished_at"])
    if run_started_at > run_finished_at:
        raise ValueError

    rate_card_id = _optional_str(payload["rate_card_id"])
    source_url = _optional_str(payload["source_url"])
    source_checked_at = _optional_date(payload["source_checked_at"])
    effective_from = _optional_date(payload["effective_from"])
    effective_until = _optional_date(payload["effective_until"])
    if (
        effective_from is not None
        and effective_until is not None
        and effective_from > effective_until
    ):
        raise ValueError

    rates_raw = payload["rates_per_million"]
    rates = parse_rate_components(rates_raw) if rates_raw is not None else None

    limitations_raw = payload["limitations"]
    if not isinstance(limitations_raw, list) or not all(
        isinstance(item, str) for item in limitations_raw
    ):
        raise ValueError
    limitations = tuple(limitations_raw)

    unavailable_reason = _optional_str(payload["unavailable_reason"])

    _validate_availability_semantics(
        availability=availability,
        canonical_model=canonical_model,
        model_identity_source=model_identity_source,
        rate_card_id=rate_card_id,
        source_url=source_url,
        source_checked_at=source_checked_at,
        effective_from=effective_from,
        effective_until=effective_until,
        rates=rates,
        limitations=limitations,
        unavailable_reason=unavailable_reason,
    )

    usage_detail_trust = _parse_usage_detail_trust(payload["usage_detail_trust"], loaded)

    return PricingSidecar(
        qualification_id=loaded.qualification_id,
        qualification_dir=loaded.qualification_dir,
        availability=availability,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        agent=agent,
        provider=provider,
        configured_model=configured_model,
        reasoning_effort=reasoning_effort,
        canonical_model=canonical_model,
        model_identity_source=model_identity_source,
        catalog_version=catalog_version,
        rate_card_id=rate_card_id,
        source_url=source_url,
        source_checked_at=source_checked_at,
        effective_from=effective_from,
        effective_until=effective_until,
        rates=rates,
        usage_detail_trust=usage_detail_trust,
        limitations=limitations,
        unavailable_reason=unavailable_reason,
    )


def _load_sidecar(loaded: LoadedReport, pricing_path: Path) -> PricingSidecar:
    try:
        text = pricing_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise _SidecarError(_UNREADABLE) from None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise _SidecarError(_INVALID_JSON) from None

    if not isinstance(payload, dict):
        raise _SidecarError(_NOT_OBJECT)

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise _SidecarError(_UNSUPPORTED_SCHEMA)

    qualification_id = payload.get("qualification_id")
    if not isinstance(qualification_id, str) or qualification_id != loaded.qualification_id:
        raise _SidecarError(_ID_MISMATCH)

    try:
        return _parse_body(loaded, payload)
    except _SidecarError:
        raise
    except Exception:  # noqa: BLE001 - any unexpected defect collapses to one fixed reason
        raise _SidecarError(_MALFORMED) from None


def _snapshot(sidecar: PricingSidecar) -> tuple[object, ...]:
    return (
        sidecar.provider,
        sidecar.canonical_model,
        sidecar.source_url,
        sidecar.source_checked_at,
        sidecar.effective_from,
        sidecar.effective_until,
        sidecar.rates,
        sidecar.limitations,
    )


def _resolve_snapshot_conflicts(
    records: list[PricingSidecar],
) -> tuple[tuple[PricingSidecar, ...], tuple[PricingLoadFailure, ...]]:
    groups: dict[str, list[PricingSidecar]] = {}
    for record in records:
        if record.rate_card_id is not None:
            groups.setdefault(record.rate_card_id, []).append(record)

    conflicted_ids = {
        rate_card_id
        for rate_card_id, group in groups.items()
        if any(_snapshot(member) != _snapshot(group[0]) for member in group[1:])
    }

    if not conflicted_ids:
        return tuple(records), ()

    kept: list[PricingSidecar] = []
    failures: list[PricingLoadFailure] = []
    for record in records:
        if record.rate_card_id in conflicted_ids:
            failures.append(
                PricingLoadFailure(
                    record.qualification_id, record.qualification_dir, _SNAPSHOT_MISMATCH
                )
            )
        else:
            kept.append(record)
    return tuple(kept), tuple(failures)


def scan_pricing(summary: HistorySummary) -> PricingHistory:
    records: list[PricingSidecar] = []
    older_unpinned: list[str] = []
    failures: list[PricingLoadFailure] = []

    for loaded in summary.loaded:
        pricing_path = loaded.qualification_dir / "pricing.json"
        if not pricing_path.is_file():
            older_unpinned.append(loaded.qualification_id)
            continue

        try:
            records.append(_load_sidecar(loaded, pricing_path))
        except _SidecarError as exc:
            failures.append(
                PricingLoadFailure(loaded.qualification_id, loaded.qualification_dir, exc.reason)
            )

    kept_records, conflict_failures = _resolve_snapshot_conflicts(records)
    failures.extend(conflict_failures)

    return PricingHistory(
        records=kept_records,
        older_unpinned_qualification_ids=tuple(older_unpinned),
        failures=tuple(failures),
    )
