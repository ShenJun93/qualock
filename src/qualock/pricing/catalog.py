from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from qualock.pricing.models import RateCard, RateComponents

CATALOG_VERSION = "2026-09-07.1"

_RATE_KEYS = frozenset(
    {"input_uncached", "input_cached", "cache_write_lower", "cache_write_upper", "output"}
)

_SOURCE_CHECKED_AT = date(2026, 9, 7)

_OPENAI_LIMITATION = (
    "the pinned standard card includes the published 1.25x cache-write token rate. "
    "Long-context, tool/search, Batch/Priority/Flex/other serving modifiers remain excluded "
    "because current attempt telemetry does not preserve correct per-request modifier data."
)
_ANTHROPIC_LIMITATION = (
    "Batch, data residency, fast mode, server-side tool fees and subscription/seat economics "
    "are excluded. Cache-write TTL is not observed, so cache-write tokens create a bounded range."
)
_GEMINI_LIMITATION = (
    "explicit cache-storage token-hour fees, tools/search, tier discounts and "
    "regional/enterprise terms are excluded. Output rate already includes thinking tokens."
)


def _parse_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a decimal string")  # noqa: TRY004
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} is not a valid decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field_name} must be a finite, non-negative decimal")
    return parsed


def _parse_optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _parse_decimal(value, field_name)


def parse_rate_components(raw: object) -> RateComponents:
    if not isinstance(raw, dict):
        raise ValueError("rate components must be a JSON object")  # noqa: TRY004
    if set(raw.keys()) != _RATE_KEYS:
        raise ValueError("rate components must have exactly five keys")

    input_uncached = _parse_decimal(raw["input_uncached"], "input_uncached")
    output = _parse_decimal(raw["output"], "output")
    input_cached = _parse_optional_decimal(raw["input_cached"], "input_cached")
    cache_write_lower = _parse_optional_decimal(raw["cache_write_lower"], "cache_write_lower")
    cache_write_upper = _parse_optional_decimal(raw["cache_write_upper"], "cache_write_upper")

    if (cache_write_lower is None) != (cache_write_upper is None):
        raise ValueError(
            "cache_write_lower and cache_write_upper must both be present or both be null"
        )
    if (
        cache_write_lower is not None
        and cache_write_upper is not None
        and cache_write_lower > cache_write_upper
    ):
        raise ValueError("cache_write_lower must be <= cache_write_upper")

    return RateComponents(
        input_uncached=input_uncached,
        input_cached=input_cached,
        cache_write_lower=cache_write_lower,
        cache_write_upper=cache_write_upper,
        output=output,
    )


def validate_effective_interval(effective_from: date | None, effective_until: date | None) -> None:
    if (
        effective_from is not None
        and effective_until is not None
        and effective_from > effective_until
    ):
        raise ValueError("effective_from must be <= effective_until")


def _rates(
    input_uncached: str,
    input_cached: str,
    cache_write_lower: str | None,
    cache_write_upper: str | None,
    output: str,
) -> RateComponents:
    return parse_rate_components(
        {
            "input_uncached": input_uncached,
            "input_cached": input_cached,
            "cache_write_lower": cache_write_lower,
            "cache_write_upper": cache_write_upper,
            "output": output,
        }
    )


def _card(
    rate_card_id: str,
    provider: str,
    canonical_model: str,
    effective_from: date | None,
    effective_until: date | None,
    source_url: str,
    rates: RateComponents,
    limitations: tuple[str, ...],
) -> RateCard:
    validate_effective_interval(effective_from, effective_until)
    return RateCard(
        rate_card_id=rate_card_id,
        provider=provider,
        canonical_model=canonical_model,
        effective_from=effective_from,
        effective_until=effective_until,
        source_url=source_url,
        source_checked_at=_SOURCE_CHECKED_AT,
        rates=rates,
        limitations=limitations,
    )


def _validate_catalog(cards: tuple[RateCard, ...]) -> None:
    seen_ids: set[str] = set()
    for card in cards:
        if card.rate_card_id in seen_ids:
            raise ValueError(f"duplicate rate_card_id: {card.rate_card_id}")
        seen_ids.add(card.rate_card_id)
        validate_effective_interval(card.effective_from, card.effective_until)

    by_key: dict[tuple[str, str], list[RateCard]] = {}
    for card in cards:
        by_key.setdefault((card.provider, card.canonical_model), []).append(card)

    for group in by_key.values():
        ordered = sorted(group, key=lambda card: card.effective_from or date.min)
        for earlier, later in pairwise(ordered):
            if earlier.effective_until is None or (
                later.effective_from is not None
                and earlier.effective_until >= later.effective_from
            ):
                raise ValueError(
                    "overlapping rate cards for "
                    f"{earlier.provider}:{earlier.canonical_model}: "
                    f"{earlier.rate_card_id} and {later.rate_card_id}"
                )


RATE_CARDS: tuple[RateCard, ...] = (
    _card(
        "openai:gpt-5.6-terra:standard:2026-09-07",
        "openai",
        "gpt-5.6-terra",
        date(2026, 9, 7),
        None,
        "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
        _rates("2.00", "0.20", "2.50", "2.50", "12.00"),
        (_OPENAI_LIMITATION,),
    ),
    _card(
        "openai:gpt-5.6-sol:standard:2026-09-07",
        "openai",
        "gpt-5.6-sol",
        date(2026, 9, 7),
        None,
        "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
        _rates("4.00", "0.40", "5.00", "5.00", "20.00"),
        (_OPENAI_LIMITATION,),
    ),
    _card(
        "anthropic:claude-sonnet-5:standard-global:2026-09-07",
        "anthropic",
        "claude-sonnet-5",
        date(2026, 9, 7),
        None,
        "https://platform.claude.com/docs/en/about-claude/pricing",
        _rates("2.00", "0.20", "2.50", "4.00", "10.00"),
        (_ANTHROPIC_LIMITATION,),
    ),
    _card(
        "anthropic:claude-sonnet-4-6:standard-global:2026-09-07",
        "anthropic",
        "claude-sonnet-4-6",
        date(2026, 9, 7),
        None,
        "https://platform.claude.com/docs/en/models/sonnet-4-6/overview",
        _rates("3.00", "0.30", "3.75", "6.00", "15.00"),
        (_ANTHROPIC_LIMITATION,),
    ),
    _card(
        "google:gemini-3.8-flash:standard:through-2026-12-31",
        "google",
        "gemini-3.8-flash",
        date(2026, 9, 7),
        date(2026, 12, 31),
        "https://ai.google.dev/gemini-api/docs/pricing",
        _rates("0.75", "0.075", None, None, "3.75"),
        (_GEMINI_LIMITATION,),
    ),
    _card(
        "google:gemini-3.8-flash:standard:from-2027-01-01",
        "google",
        "gemini-3.8-flash",
        date(2027, 1, 1),
        None,
        "https://ai.google.dev/gemini-api/docs/pricing",
        _rates("1.50", "0.15", None, None, "7.50"),
        (_GEMINI_LIMITATION,),
    ),
)

_validate_catalog(RATE_CARDS)


def resolve_rate_card(provider: str, canonical_model: str, instant: datetime) -> RateCard | None:
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise ValueError("instant must be an aware datetime")
    as_date = instant.astimezone(UTC).date()

    matches = [
        card
        for card in RATE_CARDS
        if card.provider == provider
        and card.canonical_model == canonical_model
        and (card.effective_from is None or as_date >= card.effective_from)
        and (card.effective_until is None or as_date <= card.effective_until)
    ]
    if len(matches) != 1:
        return None
    return matches[0]
