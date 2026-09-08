from datetime import UTC, datetime

import pytest

import qualock.pricing.resolve as pricing_resolve
from qualock.config.models import QualockConfig
from qualock.pricing.models import ModelIdentity
from qualock.pricing.resolve import (
    build_pricing_payload,
    provider_for_agent,
    resolve_model_identity,
)
from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Verdict,
)


def qualification_with_events(*events_jsonl: str) -> QualificationResult:
    attempts = tuple(
        AttemptResult(
            side="baseline",
            repetition=index,
            success=True,
            valid=True,
            duration_ms=1,
            events_jsonl=events,
        )
        for index, events in enumerate(events_jsonl, 1)
    )
    execution = CanaryExecution(
        "canary-a",
        True,
        "sha256:test",
        attempts,
        len(attempts),
        0,
        len(attempts),
        0,
        Verdict.PASS,
        "test",
    )
    return QualificationResult("q-1", "1.0.0", "1.0.1", Verdict.PASS, (execution,), (), ())


def test_provider_for_agent_is_exact_and_closed() -> None:
    assert provider_for_agent("codex") == "openai"
    assert provider_for_agent("claude") == "anthropic"
    assert provider_for_agent("antigravity") == "google"
    assert provider_for_agent("gemini") == "google"
    assert provider_for_agent("gpt") is None
    assert provider_for_agent("") is None


def test_claude_alias_resolves_consistent_runtime_model() -> None:
    assert resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events('{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'),
    ) == ModelIdentity("claude-sonnet-5", "runtime_observed", None)


def test_claude_alias_without_observation_fails_closed() -> None:
    result = resolve_model_identity("claude", "sonnet", qualification_with_events(""))
    assert result == ModelIdentity(None, "unavailable", "missing_observed_model")


def test_claude_conflicting_observations_fail_closed() -> None:
    result = resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events(
            '{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n',
            '{"type":"system","subtype":"init","model":"claude-sonnet-4-6"}\n',
        ),
    )
    assert result == ModelIdentity(None, "unavailable", "inconsistent_observed_model")


def test_claude_exact_config_requires_observed_agreement() -> None:
    no_runtime = resolve_model_identity(
        "claude", "claude-sonnet-5", qualification_with_events("")
    )
    assert no_runtime == ModelIdentity("claude-sonnet-5", "configured_exact", None)

    agreeing = resolve_model_identity(
        "claude",
        "claude-sonnet-5",
        qualification_with_events('{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'),
    )
    assert agreeing == ModelIdentity("claude-sonnet-5", "runtime_observed", None)

    disagreeing = resolve_model_identity(
        "claude",
        "claude-sonnet-5",
        qualification_with_events(
            '{"type":"system","subtype":"init","model":"claude-sonnet-4-6"}\n'
        ),
    )
    assert disagreeing == ModelIdentity(None, "unavailable", "inconsistent_observed_model")


def test_claude_model_paths_missing_and_empty_are_absent() -> None:
    result = resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events(
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"model":""}}\n'
        ),
    )
    assert result == ModelIdentity(None, "unavailable", "missing_observed_model")


def test_claude_non_string_model_is_malformed() -> None:
    result = resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events('{"type":"system","subtype":"init","model":123}\n'),
    )
    assert result == ModelIdentity(None, "unavailable", "malformed_model_evidence")


def test_claude_malformed_or_non_object_jsonl_is_malformed() -> None:
    invalid_json = resolve_model_identity(
        "claude", "sonnet", qualification_with_events("not json\n")
    )
    assert invalid_json == ModelIdentity(None, "unavailable", "malformed_model_evidence")

    non_object = resolve_model_identity(
        "claude", "sonnet", qualification_with_events("[1, 2, 3]\n")
    )
    assert non_object == ModelIdentity(None, "unavailable", "malformed_model_evidence")


def test_claude_unrelated_events_are_ignored() -> None:
    result = resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events(
            '{"type":"user","message":{"content":[]}}\n'
            '{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'
            '{"type":"result","subtype":"success"}\n'
        ),
    )
    assert result == ModelIdentity("claude-sonnet-5", "runtime_observed", None)


def test_claude_unknown_consistent_runtime_model_is_unknown() -> None:
    result = resolve_model_identity(
        "claude",
        "sonnet",
        qualification_with_events(
            '{"type":"system","subtype":"init","model":"claude-nonexistent-9"}\n'
        ),
    )
    assert result == ModelIdentity(None, "unavailable", "unknown_model")


def test_openai_exact_canonical_models_resolve() -> None:
    empty = qualification_with_events()
    assert resolve_model_identity("codex", "gpt-5.6-terra", empty) == ModelIdentity(
        "gpt-5.6-terra", "configured_exact", None
    )
    assert resolve_model_identity("codex", "gpt-5.6-sol", empty) == ModelIdentity(
        "gpt-5.6-sol", "configured_exact", None
    )


def test_openai_documented_alias_resolves_only_to_sol() -> None:
    result = resolve_model_identity("codex", "gpt-5.6", qualification_with_events())
    assert result == ModelIdentity("gpt-5.6-sol", "documented_alias", None)


def test_openai_unknown_and_convenience_names_do_not_fuzzy_match() -> None:
    empty = qualification_with_events()
    assert resolve_model_identity("codex", "gpt-5.6-turbo", empty) == ModelIdentity(
        None, "unavailable", "unknown_model"
    )
    assert resolve_model_identity("codex", "gpt-5", empty) == ModelIdentity(
        None, "unavailable", "unknown_model"
    )


def test_antigravity_three_explicit_effort_ids_map() -> None:
    empty = qualification_with_events()
    for suffix in ("low", "medium", "high"):
        result = resolve_model_identity("antigravity", f"gemini-3.8-flash-{suffix}", empty)
        assert result == ModelIdentity("gemini-3.8-flash", "agent_exact_mapping", None)


def test_antigravity_unlisted_suffix_does_not_resolve() -> None:
    empty = qualification_with_events()
    assert resolve_model_identity("antigravity", "gemini-3.8-flash-ultra", empty) == ModelIdentity(
        None, "unavailable", "unknown_model"
    )
    assert resolve_model_identity("antigravity", "gemini-3.8-flash", empty) == ModelIdentity(
        None, "unavailable", "unknown_model"
    )


def test_gemini_agreeing_init_and_terminal_models_are_runtime_observed() -> None:
    events = (
        '{"type":"init","model":"gemini-3.8-flash"}\n'
        '{"type":"result","stats":{"models":{"gemini-3.8-flash":{}}}}\n'
    )

    assert resolve_model_identity(
        "gemini", "gemini-3.8-flash", qualification_with_events(events)
    ) == ModelIdentity("gemini-3.8-flash", "runtime_observed", None)


def test_gemini_model_observations_must_agree_across_attempts() -> None:
    result = resolve_model_identity(
        "gemini",
        "gemini-3.8-flash",
        qualification_with_events(
            '{"type":"init","model":"gemini-3.8-flash"}\n',
            '{"type":"result","stats":{"models":{"gemini-other":{}}}}\n',
        ),
    )

    assert result == ModelIdentity(
        None, "unavailable", "inconsistent_observed_model"
    )


def test_gemini_without_runtime_model_observation_fails_closed() -> None:
    result = resolve_model_identity(
        "gemini", "gemini-3.8-flash", qualification_with_events("")
    )

    assert result == ModelIdentity(None, "unavailable", "missing_observed_model")


def test_gemini_malformed_model_evidence_fails_closed() -> None:
    malformed_streams = (
        "not json\n",
        '{"type":"init","model":7}\n',
        '{"type":"result","stats":null}\n',
        '{"type":"result","stats":{"models":[]}}\n',
        '{"type":"result","stats":{"models":{"":{}}}}\n',
    )

    for events in malformed_streams:
        result = resolve_model_identity(
            "gemini", "gemini-3.8-flash", qualification_with_events(events)
        )
        assert result == ModelIdentity(
            None, "unavailable", "malformed_model_evidence"
        )


def test_gemini_conflicting_init_and_terminal_models_fail_closed() -> None:
    events = (
        '{"type":"init","model":"gemini-3.8-flash"}\n'
        '{"type":"result","stats":{"models":{"gemini-other":{}}}}\n'
    )

    result = resolve_model_identity(
        "gemini", "gemini-3.8-flash", qualification_with_events(events)
    )

    assert result == ModelIdentity(
        None, "unavailable", "inconsistent_observed_model"
    )


def test_gemini_unknown_observed_model_never_uses_configured_alias() -> None:
    result = resolve_model_identity(
        "gemini",
        "gemini-3.8-flash-latest",
        qualification_with_events(
            '{"type":"init","model":"gemini-3.8-flash-latest"}\n'
        ),
    )

    assert result == ModelIdentity(None, "unavailable", "unknown_model")


def test_gemini_unknown_observed_model_takes_precedence_over_exact_mismatch() -> None:
    result = resolve_model_identity(
        "gemini",
        "gemini-3.8-flash",
        qualification_with_events(
            '{"type":"init","model":"gemini-nonexistent-9"}\n'
        ),
    )

    assert result == ModelIdentity(
        None, "unavailable", "unknown_model"
    )


def test_gemini_known_exact_config_must_agree_with_known_observed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def canonical_models_for(provider: str) -> frozenset[str]:
        assert provider == "google"
        return frozenset(("gemini-3.8-flash", "gemini-3.8-pro"))

    monkeypatch.setattr(pricing_resolve, "_canonical_models_for", canonical_models_for)

    result = resolve_model_identity(
        "gemini",
        "gemini-3.8-flash",
        qualification_with_events('{"type":"init","model":"gemini-3.8-pro"}\n'),
    )

    assert result == ModelIdentity(
        None, "unavailable", "inconsistent_observed_model"
    )


def test_gemini_pricing_payload_uses_runtime_model_and_pinned_google_card() -> None:
    config = QualockConfig.model_validate(
        {
            "agent": {"name": "gemini"},
            "model": {
                "id": "gemini-3.8-flash",
                "reasoning_effort": "provider-default",
            },
        }
    )
    events = (
        '{"type":"init","model":"gemini-3.8-flash"}\n'
        '{"type":"result","stats":{"cached":0,'
        '"models":{"gemini-3.8-flash":{}}}}\n'
    )
    instant = datetime(2026, 9, 8, tzinfo=UTC)

    payload = build_pricing_payload(
        config, qualification_with_events(events), instant, instant
    )

    assert payload["availability"] == "priced"
    assert payload["provider"] == "google"
    assert payload["canonical_model"] == "gemini-3.8-flash"
    assert payload["model_identity_source"] == "runtime_observed"
    assert payload["rate_card_id"] == (
        "google:gemini-3.8-flash:standard:through-2026-12-31"
    )
    assert payload["usage_detail_trust"] == [
        {
            "canary_id": "canary-a",
            "side": "baseline",
            "repetition": 1,
            "cached_input_tokens_trust": "observed",
            "cache_write_input_tokens_trust": "unobserved",
        }
    ]
