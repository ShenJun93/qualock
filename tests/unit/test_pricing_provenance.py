from qualock.pricing.models import AttemptUsageTrust
from qualock.pricing.resolve import build_usage_detail_trust
from qualock.qualification.models import (
    AttemptResult,
    CanaryExecution,
    QualificationResult,
    Usage,
    Verdict,
)


def make_attempt(
    *,
    side: str = "candidate",
    repetition: int = 1,
    valid: bool = True,
    success: bool = True,
    usage: Usage | None = None,
    events_jsonl: str = "",
) -> AttemptResult:
    return AttemptResult(
        side=side,
        repetition=repetition,
        success=success,
        valid=valid,
        duration_ms=1,
        usage=usage if usage is not None else Usage(),
        events_jsonl=events_jsonl,
    )


def make_execution(
    canary_id: str, attempts: tuple[AttemptResult, ...], *, critical: bool = True
) -> CanaryExecution:
    return CanaryExecution(
        canary_id,
        critical,
        "sha256:test" if attempts else "",
        attempts,
        sum(a.side == "baseline" and a.success for a in attempts),
        sum(a.side == "candidate" and a.success for a in attempts),
        sum(a.side == "baseline" and a.valid for a in attempts),
        sum(a.side == "candidate" and a.valid for a in attempts),
        Verdict.PASS,
        "test",
    )


def make_result(executions: tuple[CanaryExecution, ...]) -> QualificationResult:
    return QualificationResult(
        "q-1", "1.0.0", "1.0.1", Verdict.PASS, executions, (), ()
    )


def test_codex_cache_read_requires_every_completed_turn_detail() -> None:
    events = (
        '{"type":"turn.completed","usage":{"cached_input_tokens":0}}\n'
        '{"type":"turn.completed","usage":{"cached_input_tokens":5}}\n'
    )
    result = make_result(
        (
            make_execution(
                "canary-a",
                (make_attempt(side="candidate", repetition=1, events_jsonl=events),),
            ),
        )
    )
    assert build_usage_detail_trust("codex", result) == (
        AttemptUsageTrust(
            canary_id="canary-a",
            side="candidate",
            repetition=1,
            cached_input_tokens_trust="observed",
            cache_write_input_tokens_trust="unobserved",
        ),
    )


def test_codex_missing_or_malformed_completed_turn_detail_is_unobserved() -> None:
    missing = (
        '{"type":"turn.completed","usage":{"cached_input_tokens":5}}\n'
        '{"type":"turn.completed","usage":{}}\n'
    )
    malformed = '{"type":"turn.completed","usage":{"cached_input_tokens":-1}}\n'
    no_completed_turns = '{"type":"turn.started"}\n'

    for events in (missing, malformed, no_completed_turns):
        result = make_result(
            (
                make_execution(
                    "canary-a",
                    (make_attempt(events_jsonl=events),),
                ),
            )
        )
        trust = build_usage_detail_trust("codex", result)
        assert trust[0].cached_input_tokens_trust == "unobserved"


def test_codex_cache_write_is_always_unobserved() -> None:
    events = '{"type":"turn.completed","usage":{"cached_input_tokens":5}}\n'
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )
    trust = build_usage_detail_trust("codex", result)
    assert trust[0].cache_write_input_tokens_trust == "unobserved"


def test_claude_trust_uses_unique_terminal_result_usage() -> None:
    events = (
        '{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'
        '{"type":"result","usage":{"cache_read_input_tokens":10,'
        '"cache_creation_input_tokens":3}}\n'
    )
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )
    assert build_usage_detail_trust("claude", result) == (
        AttemptUsageTrust(
            canary_id="canary-a",
            side="candidate",
            repetition=1,
            cached_input_tokens_trust="observed",
            cache_write_input_tokens_trust="observed",
        ),
    )


def test_claude_cache_creation_absence_is_unobserved() -> None:
    events = '{"type":"result","usage":{"cache_read_input_tokens":10}}\n'
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )
    trust = build_usage_detail_trust("claude", result)
    assert trust[0].cached_input_tokens_trust == "observed"
    assert trust[0].cache_write_input_tokens_trust == "unobserved"


def test_claude_explicit_zero_cache_creation_is_observed() -> None:
    events = (
        '{"type":"result","usage":{"cache_read_input_tokens":10,'
        '"cache_creation_input_tokens":0}}\n'
    )
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )
    trust = build_usage_detail_trust("claude", result)
    assert trust[0].cache_write_input_tokens_trust == "observed"


def test_antigravity_cache_read_observed_and_write_known_zero() -> None:
    events = (
        '{"event":"result","result":{"usage":{"cache_read_tokens":7}}}\n'
    )
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )
    assert build_usage_detail_trust("antigravity", result) == (
        AttemptUsageTrust(
            canary_id="canary-a",
            side="candidate",
            repetition=1,
            cached_input_tokens_trust="observed",
            cache_write_input_tokens_trust="known_zero",
        ),
    )


def test_antigravity_malformed_cache_read_keeps_write_unobserved() -> None:
    malformed_usage_payloads = (
        '{"event":"result","result":{"usage":{}}}\n',
        '{"event":"result","result":{"usage":{"cache_read_tokens":true}}}\n',
        '{"event":"result","result":{"usage":{"cache_read_tokens":-1}}}\n',
    )
    for events in malformed_usage_payloads:
        result = make_result(
            (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
        )
        trust = build_usage_detail_trust("antigravity", result)
        assert trust[0].cached_input_tokens_trust == "unobserved"
        assert trust[0].cache_write_input_tokens_trust == "unobserved"


def test_gemini_cache_read_requires_one_valid_terminal_result() -> None:
    valid = '{"type":"result","stats":{"cached":7}}\n'
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=valid),)),)
    )

    assert build_usage_detail_trust("gemini", result) == (
        AttemptUsageTrust(
            canary_id="canary-a",
            side="candidate",
            repetition=1,
            cached_input_tokens_trust="observed",
            cache_write_input_tokens_trust="unobserved",
        ),
    )


def test_gemini_duplicate_missing_or_malformed_result_is_unobserved() -> None:
    invalid_streams = (
        "",
        (
            '{"type":"result","stats":{"cached":0}}\n'
            '{"type":"result","stats":{"cached":0}}\n'
        ),
        '{"type":"result"}\n',
        '{"type":"result","stats":null}\n',
        '{"type":"result","stats":{"cached":true}}\n',
        '{"type":"result","stats":{"cached":-1}}\n',
    )

    for events in invalid_streams:
        result = make_result(
            (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
        )
        trust = build_usage_detail_trust("gemini", result)
        assert trust[0].cached_input_tokens_trust == "unobserved"
        assert trust[0].cache_write_input_tokens_trust == "unobserved"


def test_gemini_cache_write_is_unobserved_even_with_extra_fields() -> None:
    events = (
        '{"type":"result","stats":{"cached":0,"cache_write":9,'
        '"cache_creation_input_tokens":11}}\n'
    )
    result = make_result(
        (make_execution("canary-a", (make_attempt(events_jsonl=events),)),)
    )

    trust = build_usage_detail_trust("gemini", result)

    assert trust[0].cached_input_tokens_trust == "observed"
    assert trust[0].cache_write_input_tokens_trust == "unobserved"


def test_usage_trust_is_local_to_attempt_and_never_changes_usage() -> None:
    usage = Usage(input_tokens=10, output_tokens=1, observed=True)
    attempt = make_attempt(
        events_jsonl='{"type":"turn.completed","usage":{}}\n', usage=usage
    )
    result = make_result((make_execution("canary-a", (attempt,)),))

    build_usage_detail_trust("codex", result)

    assert attempt.usage == usage


def test_usage_trust_covers_every_started_failed_or_invalid_attempt_once() -> None:
    attempts = (
        make_attempt(side="baseline", repetition=1, valid=False, success=False),
        make_attempt(side="candidate", repetition=1, valid=True, success=False),
    )
    result = make_result((make_execution("canary-a", attempts),))

    trust = build_usage_detail_trust("codex", result)

    identities = {(t.canary_id, t.side, t.repetition) for t in trust}
    assert identities == {("canary-a", "baseline", 1), ("canary-a", "candidate", 1)}
    assert len(trust) == 2


def test_usage_trust_omits_skipped_executions() -> None:
    result = make_result(
        (
            make_execution("canary-a", ()),
            make_execution("canary-b", (make_attempt(),)),
        )
    )

    trust = build_usage_detail_trust("codex", result)

    assert {t.canary_id for t in trust} == {"canary-b"}
