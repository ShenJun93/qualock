import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from qualock.pricing.catalog import (
    CATALOG_VERSION,
    RATE_CARDS,
    _validate_catalog,
    parse_rate_components,
    resolve_rate_card,
    validate_effective_interval,
)
from qualock.pricing.models import RateCard

_FORBIDDEN_IMPORT_ROOTS = {"httpx", "requests", "urllib", "socket", "aiohttp"}


def test_catalog_version_and_rate_card_ids_are_unique() -> None:
    assert CATALOG_VERSION == "2026-09-07.1"
    ids = [card.rate_card_id for card in RATE_CARDS]
    assert len(ids) == len(set(ids))
    assert len(RATE_CARDS) == 6


def test_rate_cards_are_frozen_material_snapshots() -> None:
    assert isinstance(RATE_CARDS, tuple)
    for card in RATE_CARDS:
        with pytest.raises(FrozenInstanceError):
            card.rate_card_id = "mutated"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            card.rates.input_uncached = card.rates.input_uncached  # type: ignore[misc]


def _card(provider: str, canonical_model: str, rate_card_id_suffix: str) -> RateCard:
    matches = [
        card
        for card in RATE_CARDS
        if card.provider == provider
        and card.canonical_model == canonical_model
        and card.rate_card_id.endswith(rate_card_id_suffix)
    ]
    assert len(matches) == 1
    return matches[0]


def test_openai_cards_pin_standard_rates_cache_write_multiplier_and_no_expiry() -> None:
    terra = _card("openai", "gpt-5.6-terra", "2026-09-07")
    sol = _card("openai", "gpt-5.6-sol", "2026-09-07")

    for card, expected_url in (
        (terra, "https://developers.openai.com/api/docs/models/gpt-5.6-terra"),
        (sol, "https://developers.openai.com/api/docs/models/gpt-5.6-sol"),
    ):
        assert card.effective_from == date(2026, 9, 7)
        assert card.effective_until is None
        assert card.source_url == expected_url
        assert card.source_checked_at == date(2026, 9, 7)
        assert "1.25x cache-write token rate" in card.limitations[0]

    assert terra.rates.input_uncached == Decimal("2.00")
    assert terra.rates.input_cached == Decimal("0.20")
    assert terra.rates.cache_write_lower == terra.rates.cache_write_upper == Decimal("2.50")
    assert terra.rates.output == Decimal("12.00")

    assert sol.rates.input_uncached == Decimal("4.00")
    assert sol.rates.input_cached == Decimal("0.40")
    assert sol.rates.cache_write_lower == sol.rates.cache_write_upper == Decimal("5.00")
    assert sol.rates.output == Decimal("20.00")


def test_anthropic_cards_pin_global_rates_and_cache_write_range() -> None:
    sonnet5 = _card("anthropic", "claude-sonnet-5", "2026-09-07")
    sonnet46 = _card("anthropic", "claude-sonnet-4-6", "2026-09-07")

    for card in (sonnet5, sonnet46):
        assert card.effective_from == date(2026, 9, 7)
        assert card.effective_until is None
        assert card.source_checked_at == date(2026, 9, 7)
        assert "Cache-write TTL is not observed" in card.limitations[0]
        rates = card.rates
        assert rates.cache_write_lower is not None
        assert rates.cache_write_upper is not None
        assert rates.cache_write_lower < rates.cache_write_upper

    assert sonnet5.source_url == "https://platform.claude.com/docs/en/about-claude/pricing"
    assert (
        sonnet46.source_url == "https://platform.claude.com/docs/en/models/sonnet-4-6/overview"
    )
    assert sonnet5.rates.input_uncached == Decimal("2.00")
    assert sonnet5.rates.output == Decimal("10.00")
    assert sonnet46.rates.input_uncached == Decimal("3.00")
    assert sonnet46.rates.output == Decimal("15.00")


def test_gemini_boundary_selects_old_then_new_card() -> None:
    old = resolve_rate_card(
        "google",
        "gemini-3.8-flash",
        datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
    )
    new = resolve_rate_card("google", "gemini-3.8-flash", datetime(2027, 1, 1, tzinfo=UTC))
    assert old is not None
    assert old.rate_card_id == "google:gemini-3.8-flash:standard:through-2026-12-31"
    assert new is not None
    assert new.rate_card_id == "google:gemini-3.8-flash:standard:from-2027-01-01"


def test_initial_cards_do_not_resolve_before_applicability_floor() -> None:
    before_floor = datetime(2026, 9, 6, 23, 59, tzinfo=UTC)
    assert resolve_rate_card("openai", "gpt-5.6-terra", before_floor) is None
    assert resolve_rate_card("anthropic", "claude-sonnet-5", before_floor) is None
    assert resolve_rate_card("google", "gemini-3.8-flash", before_floor) is None


def test_effective_until_is_utc_date_inclusive() -> None:
    boundary_utc = resolve_rate_card(
        "google", "gemini-3.8-flash", datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    )
    assert boundary_utc is not None
    assert boundary_utc.rate_card_id == "google:gemini-3.8-flash:standard:through-2026-12-31"

    non_utc_offset = timezone(timedelta(hours=5))
    still_old_side_in_utc = datetime(2027, 1, 1, 3, 0, tzinfo=non_utc_offset)
    resolved = resolve_rate_card("google", "gemini-3.8-flash", still_old_side_in_utc)
    assert resolved is not None
    assert resolved.rate_card_id == "google:gemini-3.8-flash:standard:through-2026-12-31"


def test_overlapping_rate_cards_are_rejected_by_catalog_validation() -> None:
    rates = parse_rate_components(
        {
            "input_uncached": "1.00",
            "input_cached": "0.10",
            "cache_write_lower": None,
            "cache_write_upper": None,
            "output": "5.00",
        }
    )
    earlier = RateCard(
        rate_card_id="google:test-model:standard:a",
        provider="google",
        canonical_model="test-model",
        effective_from=date(2026, 1, 1),
        effective_until=date(2026, 6, 30),
        source_url="https://example.invalid/a",
        source_checked_at=date(2026, 1, 1),
        rates=rates,
        limitations=(),
    )
    later = RateCard(
        rate_card_id="google:test-model:standard:b",
        provider="google",
        canonical_model="test-model",
        effective_from=date(2026, 6, 1),
        effective_until=None,
        source_url="https://example.invalid/b",
        source_checked_at=date(2026, 1, 1),
        rates=rates,
        limitations=(),
    )
    with pytest.raises(ValueError):
        _validate_catalog((earlier, later))


def test_resolve_rate_card_rejects_naive_datetime() -> None:
    naive = datetime(2026, 9, 10)  # noqa: DTZ001
    with pytest.raises(ValueError):
        resolve_rate_card("openai", "gpt-5.6-terra", naive)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-0.01", -1, True])
def test_rate_parser_accepts_only_finite_nonnegative_decimal_strings(value: object) -> None:
    raw = {
        "input_uncached": value,
        "input_cached": "0.20",
        "cache_write_lower": "2.50",
        "cache_write_upper": "2.50",
        "output": "12.00",
    }
    with pytest.raises(ValueError):
        parse_rate_components(raw)


def test_cache_write_rates_are_both_null_or_ordered() -> None:
    both_null = parse_rate_components(
        {
            "input_uncached": "1.00",
            "input_cached": "0.10",
            "cache_write_lower": None,
            "cache_write_upper": None,
            "output": "5.00",
        }
    )
    assert both_null.cache_write_lower is None
    assert both_null.cache_write_upper is None

    with pytest.raises(ValueError):
        parse_rate_components(
            {
                "input_uncached": "1.00",
                "input_cached": "0.10",
                "cache_write_lower": "2.50",
                "cache_write_upper": None,
                "output": "5.00",
            }
        )

    with pytest.raises(ValueError):
        parse_rate_components(
            {
                "input_uncached": "1.00",
                "input_cached": "0.10",
                "cache_write_lower": "3.00",
                "cache_write_upper": "2.00",
                "output": "5.00",
            }
        )


def test_effective_interval_rejects_from_after_until() -> None:
    validate_effective_interval(None, None)
    validate_effective_interval(date(2026, 1, 1), date(2026, 1, 1))
    validate_effective_interval(date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        validate_effective_interval(date(2026, 12, 31), date(2026, 1, 1))


def test_pricing_runtime_has_no_network_client_imports() -> None:
    pricing_dir = Path(__file__).resolve().parents[2] / "src" / "qualock" / "pricing"
    for path in sorted(pricing_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not (roots & _FORBIDDEN_IMPORT_ROOTS), f"{path}: forbidden import {roots}"
