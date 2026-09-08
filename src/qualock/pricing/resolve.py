import json
from collections.abc import Iterator
from datetime import UTC, datetime

from qualock.config.models import QualockConfig
from qualock.pricing.catalog import CATALOG_VERSION, RATE_CARDS, resolve_rate_card
from qualock.pricing.models import AttemptUsageTrust, ModelIdentity, RateComponents
from qualock.qualification.models import QualificationResult

_PROVIDERS = {"codex": "openai", "claude": "anthropic", "antigravity": "google"}
_OPENAI_ALIASES = {"gpt-5.6": "gpt-5.6-sol"}
_ANTIGRAVITY_MODELS = {
    "gemini-3.8-flash-low": "gemini-3.8-flash",
    "gemini-3.8-flash-medium": "gemini-3.8-flash",
    "gemini-3.8-flash-high": "gemini-3.8-flash",
}

_UNAVAILABLE = "unavailable"
_UNKNOWN_MODEL = "unknown_model"
_MISSING_OBSERVED_MODEL = "missing_observed_model"
_INCONSISTENT_OBSERVED_MODEL = "inconsistent_observed_model"
_MALFORMED_MODEL_EVIDENCE = "malformed_model_evidence"
_NO_RATE_CARD = "no_rate_card"
_INVALID_CAPTURE_TIME = "invalid_capture_time"
_RATE_BOUNDARY_CROSSED = "rate_boundary_crossed"

_OBSERVED = "observed"
_KNOWN_ZERO = "known_zero"
_UNOBSERVED = "unobserved"


def provider_for_agent(agent: str) -> str | None:
    return _PROVIDERS.get(agent)


def _canonical_models_for(provider: str) -> frozenset[str]:
    return frozenset(card.canonical_model for card in RATE_CARDS if card.provider == provider)


def _extract_model_value(container: dict[str, object]) -> tuple[str | None, bool]:
    """Return (observed_model, is_malformed) for a single model field."""
    if "model" not in container:
        return None, False
    value = container["model"]
    if value is None:
        return None, False
    if not isinstance(value, str):
        return None, True
    if not value.strip():
        return None, False
    return value, False


def _scan_claude_observations(result: QualificationResult) -> tuple[str | None, str | None]:
    """Return (agreed_model, failure_reason); failure_reason is None on success."""
    observed: set[str] = set()
    for execution in result.executions:
        for attempt in execution.attempts:
            for raw_line in attempt.events_jsonl.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return None, _MALFORMED_MODEL_EVIDENCE
                if not isinstance(event, dict):
                    return None, _MALFORMED_MODEL_EVIDENCE

                event_type = event.get("type")
                candidate: str | None = None
                if event_type == "system" and event.get("subtype") == "init":
                    candidate, malformed = _extract_model_value(event)
                    if malformed:
                        return None, _MALFORMED_MODEL_EVIDENCE
                elif event_type == "assistant":
                    message = event.get("message")
                    if isinstance(message, dict):
                        candidate, malformed = _extract_model_value(message)
                        if malformed:
                            return None, _MALFORMED_MODEL_EVIDENCE

                if candidate is not None:
                    observed.add(candidate)

    if not observed:
        return None, None
    if len(observed) > 1:
        return None, _INCONSISTENT_OBSERVED_MODEL
    return next(iter(observed)), None


def _resolve_claude(configured_model: str, result: QualificationResult) -> ModelIdentity:
    canonical_models = _canonical_models_for("anthropic")
    observed_model, failure = _scan_claude_observations(result)
    if failure is not None:
        return ModelIdentity(None, _UNAVAILABLE, failure)

    if observed_model is not None:
        if observed_model not in canonical_models:
            return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)
        if configured_model in canonical_models and configured_model != observed_model:
            return ModelIdentity(None, _UNAVAILABLE, _INCONSISTENT_OBSERVED_MODEL)
        return ModelIdentity(observed_model, "runtime_observed", None)

    if configured_model in canonical_models:
        return ModelIdentity(configured_model, "configured_exact", None)

    return ModelIdentity(None, _UNAVAILABLE, _MISSING_OBSERVED_MODEL)


def _resolve_openai(configured_model: str) -> ModelIdentity:
    canonical_models = _canonical_models_for("openai")
    if configured_model in canonical_models:
        return ModelIdentity(configured_model, "configured_exact", None)
    alias_target = _OPENAI_ALIASES.get(configured_model)
    if alias_target is not None:
        return ModelIdentity(alias_target, "documented_alias", None)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)


def _resolve_antigravity(configured_model: str) -> ModelIdentity:
    canonical_model = _ANTIGRAVITY_MODELS.get(configured_model)
    if canonical_model is not None:
        return ModelIdentity(canonical_model, "agent_exact_mapping", None)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)


def resolve_model_identity(
    agent: str, configured_model: str, result: QualificationResult
) -> ModelIdentity:
    provider = provider_for_agent(agent)
    if provider == "anthropic":
        return _resolve_claude(configured_model, result)
    if provider == "openai":
        return _resolve_openai(configured_model)
    if provider == "google":
        return _resolve_antigravity(configured_model)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)


def _valid_nonneg_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _iter_json_objects(events_jsonl: str) -> Iterator[dict[str, object]]:
    for raw_line in events_jsonl.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _codex_trust(events_jsonl: str) -> tuple[str, str]:
    completed_turns = 0
    cache_read_valid = True
    for event in _iter_json_objects(events_jsonl):
        if event.get("type") != "turn.completed":
            continue
        completed_turns += 1
        usage = event.get("usage")
        if not isinstance(usage, dict) or not _valid_nonneg_int(
            usage.get("cached_input_tokens")
        ):
            cache_read_valid = False
    cache_read = _OBSERVED if completed_turns > 0 and cache_read_valid else _UNOBSERVED
    return cache_read, _UNOBSERVED


def _claude_trust(events_jsonl: str) -> tuple[str, str]:
    results = [event for event in _iter_json_objects(events_jsonl) if event.get("type") == "result"]
    if len(results) != 1:
        return _UNOBSERVED, _UNOBSERVED
    usage = results[0].get("usage")
    if not isinstance(usage, dict):
        return _UNOBSERVED, _UNOBSERVED
    cache_read = _OBSERVED if _valid_nonneg_int(usage.get("cache_read_input_tokens")) else _UNOBSERVED
    cache_write = (
        _OBSERVED
        if "cache_creation_input_tokens" in usage
        and _valid_nonneg_int(usage.get("cache_creation_input_tokens"))
        else _UNOBSERVED
    )
    return cache_read, cache_write


def _antigravity_trust(events_jsonl: str) -> tuple[str, str]:
    results = [event for event in _iter_json_objects(events_jsonl) if event.get("event") == "result"]
    if len(results) != 1:
        return _UNOBSERVED, _UNOBSERVED
    result_payload = results[0].get("result")
    if not isinstance(result_payload, dict):
        return _UNOBSERVED, _UNOBSERVED
    usage = result_payload.get("usage")
    if not isinstance(usage, dict):
        return _UNOBSERVED, _UNOBSERVED
    cache_read = _OBSERVED if _valid_nonneg_int(usage.get("cache_read_tokens")) else _UNOBSERVED
    return cache_read, _KNOWN_ZERO


_TRUST_EXTRACTORS = {
    "codex": _codex_trust,
    "claude": _claude_trust,
    "antigravity": _antigravity_trust,
}


def build_usage_detail_trust(
    agent: str, result: QualificationResult
) -> tuple[AttemptUsageTrust, ...]:
    extractor = _TRUST_EXTRACTORS.get(agent)
    records: list[AttemptUsageTrust] = []
    for execution in result.executions:
        for attempt in execution.attempts:
            if extractor is None:
                cache_read, cache_write = _UNOBSERVED, _UNOBSERVED
            else:
                cache_read, cache_write = extractor(attempt.events_jsonl)
            records.append(
                AttemptUsageTrust(
                    canary_id=execution.canary_id,
                    side=attempt.side,
                    repetition=attempt.repetition,
                    cached_input_tokens_trust=cache_read,
                    cache_write_input_tokens_trust=cache_write,
                )
            )
    return tuple(records)


def _validate_capture_window(started: datetime, finished: datetime) -> tuple[datetime, datetime]:
    if started.tzinfo is None or started.tzinfo.utcoffset(started) is None:
        raise ValueError("run_started_at must be an aware datetime")
    if finished.tzinfo is None or finished.tzinfo.utcoffset(finished) is None:
        raise ValueError("run_finished_at must be an aware datetime")
    started_utc = started.astimezone(UTC)
    finished_utc = finished.astimezone(UTC)
    if started_utc > finished_utc:
        raise ValueError("run_started_at must be <= run_finished_at")
    return started_utc, finished_utc


def _rates_payload(rates: RateComponents) -> dict[str, str | None]:
    return {
        "input_uncached": str(rates.input_uncached),
        "input_cached": str(rates.input_cached) if rates.input_cached is not None else None,
        "cache_write_lower": (
            str(rates.cache_write_lower) if rates.cache_write_lower is not None else None
        ),
        "cache_write_upper": (
            str(rates.cache_write_upper) if rates.cache_write_upper is not None else None
        ),
        "output": str(rates.output),
    }


def build_pricing_payload(
    config: QualockConfig,
    result: QualificationResult,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> dict[str, object]:
    agent = config.agent.name
    provider = provider_for_agent(agent) or ""
    configured_model = config.model.effective_model
    reasoning_effort = config.model.reasoning_effort

    trust_payload = [
        {
            "canary_id": trust.canary_id,
            "side": trust.side,
            "repetition": trust.repetition,
            "cached_input_tokens_trust": trust.cached_input_tokens_trust,
            "cache_write_input_tokens_trust": trust.cache_write_input_tokens_trust,
        }
        for trust in build_usage_detail_trust(agent, result)
    ]

    base: dict[str, object] = {
        "schema_version": 1,
        "basis": "api-equivalent-reference",
        "currency": "USD",
        "qualification_id": result.qualification_id,
        "agent": agent,
        "provider": provider,
        "configured_model": configured_model,
        "reasoning_effort": reasoning_effort,
        "catalog_version": CATALOG_VERSION,
        "usage_detail_trust": trust_payload,
    }

    def unavailable(
        reason: str,
        started: datetime,
        finished: datetime,
        *,
        canonical_model: str | None = None,
        model_identity_source: str = _UNAVAILABLE,
    ) -> dict[str, object]:
        return {
            **base,
            "availability": "unavailable",
            "run_started_at": started.isoformat(),
            "run_finished_at": finished.isoformat(),
            "canonical_model": canonical_model,
            "model_identity_source": model_identity_source,
            "rate_card_id": None,
            "source_url": None,
            "source_checked_at": None,
            "effective_from": None,
            "effective_until": None,
            "rates_per_million": None,
            "limitations": [],
            "unavailable_reason": reason,
        }

    try:
        started_utc, finished_utc = _validate_capture_window(run_started_at, run_finished_at)
    except ValueError:
        return unavailable(_INVALID_CAPTURE_TIME, run_started_at, run_finished_at)

    identity = resolve_model_identity(agent, configured_model, result)
    if identity.unavailable_reason is not None:
        return unavailable(
            identity.unavailable_reason,
            started_utc,
            finished_utc,
            canonical_model=identity.canonical_model,
            model_identity_source=identity.source,
        )

    canonical_model = identity.canonical_model
    if canonical_model is None:
        return unavailable(
            _UNKNOWN_MODEL, started_utc, finished_utc, model_identity_source=identity.source
        )

    start_card = resolve_rate_card(provider, canonical_model, started_utc)
    finish_card = resolve_rate_card(provider, canonical_model, finished_utc)

    if start_card is None and finish_card is None:
        return unavailable(
            _NO_RATE_CARD,
            started_utc,
            finished_utc,
            canonical_model=canonical_model,
            model_identity_source=identity.source,
        )
    if (
        start_card is None
        or finish_card is None
        or start_card.rate_card_id != finish_card.rate_card_id
    ):
        return unavailable(
            _RATE_BOUNDARY_CROSSED,
            started_utc,
            finished_utc,
            canonical_model=canonical_model,
            model_identity_source=identity.source,
        )

    card = start_card
    return {
        **base,
        "availability": "priced",
        "run_started_at": started_utc.isoformat(),
        "run_finished_at": finished_utc.isoformat(),
        "canonical_model": canonical_model,
        "model_identity_source": identity.source,
        "rate_card_id": card.rate_card_id,
        "source_url": card.source_url,
        "source_checked_at": card.source_checked_at.isoformat(),
        "effective_from": card.effective_from.isoformat() if card.effective_from else None,
        "effective_until": card.effective_until.isoformat() if card.effective_until else None,
        "rates_per_million": _rates_payload(card.rates),
        "limitations": list(card.limitations),
        "unavailable_reason": None,
    }
