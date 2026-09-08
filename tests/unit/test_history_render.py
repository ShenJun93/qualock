from pathlib import Path

from qualock.history import (
    CanaryEffectiveness,
    CanaryEstimate,
    HistoryAnalysis,
    ReportLoadFailure,
    SuiteEstimate,
)
from qualock.history.render import render_history_text


def empty_analysis() -> HistoryAnalysis:
    return HistoryAnalysis(
        loaded_reports=0,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(
            runtime_ms=None,
            tokens=None,
            missing_runtime_canaries=(),
            missing_token_canaries=(),
        ),
    )


def test_zero_history_includes_counts_and_section_labels() -> None:
    text = render_history_text(empty_analysis())

    assert "Loaded 0" in text
    assert "Ignored 0" in text
    assert "Estimated model-attempt runtime" in text
    assert "Estimated model tokens" in text
    assert "No qualification history found yet" in text
    assert "error" not in text.lower()


def test_zero_history_does_not_raise() -> None:
    render_history_text(empty_analysis())


def test_display_rounding_pins_public_output() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=3,
        ignored_reports=(),
        ranked=(CanaryEffectiveness("sample", 3, 2, 2 / 3),),
        not_enough_history=(),
        per_canary_estimates=(
            CanaryEstimate("sample", (60_600,), (12_346,), 60_600.0, 12_345.6),
        ),
        suite_estimate=SuiteEstimate(60_600.0, 12_345.6, (), ()),
    )
    text = render_history_text(analysis)
    assert "1m 1s" in text
    assert "12,346" in text
    assert "67%" in text


def test_banker_rounding_runtime_tie_rounds_to_even() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=1,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(),
        per_canary_estimates=(
            CanaryEstimate("sample", (60_500,), (), 60_500.0, None),
        ),
        suite_estimate=SuiteEstimate(60_500.0, None, (), ("sample",)),
    )
    text = render_history_text(analysis)
    assert "1m 0s" in text


def test_banker_rounding_tokens_tie_rounds_to_even() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=1,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(),
        per_canary_estimates=(
            CanaryEstimate("sample", (), (12_344,), None, 12_344.5),
        ),
        suite_estimate=SuiteEstimate(None, 12_344.5, ("sample",), ()),
    )
    text = render_history_text(analysis)
    assert "12,344" in text
    assert "12,345" not in text


def test_ranked_rows_use_analysis_ranked_order() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=5,
        ignored_reports=(),
        ranked=(
            CanaryEffectiveness("zeta", 4, 4, 1.0),
            CanaryEffectiveness("alpha", 3, 1, 1 / 3),
        ),
        not_enough_history=(),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(None, None, ("zeta", "alpha"), ("zeta", "alpha")),
    )
    text = render_history_text(analysis)
    assert text.index("zeta") < text.index("alpha")


def test_not_enough_history_preserves_config_order() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=1,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(
            CanaryEffectiveness("beta", 1, 0, None),
            CanaryEffectiveness("alpha", 0, 0, None),
        ),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(None, None, ("beta", "alpha"), ("beta", "alpha")),
    )
    text = render_history_text(analysis)
    assert text.index("beta") < text.index("alpha")
    assert "not enough history" in text.lower()


def test_missing_runtime_and_token_lists_are_explicit_and_separate() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=1,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(CanaryEffectiveness("only-canary", 0, 0, None),),
        per_canary_estimates=(CanaryEstimate("only-canary", (), (), None, None),),
        suite_estimate=SuiteEstimate(
            runtime_ms=None,
            tokens=None,
            missing_runtime_canaries=("only-canary",),
            missing_token_canaries=("only-canary",),
        ),
    )
    text = render_history_text(analysis)
    assert "only-canary" in text
    assert "unavailable" in text.lower()


def test_ignored_diagnostics_show_dir_name_and_fixed_reason() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=0,
        ignored_reports=(
            ReportLoadFailure(Path("/tmp/results/bad-qual"), "invalid JSON"),
        ),
        ranked=(),
        not_enough_history=(),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(None, None, (), ()),
    )
    text = render_history_text(analysis)
    assert "bad-qual" in text
    assert "invalid JSON" in text
    assert "/tmp/results" not in text


def test_no_raw_exception_payload_in_output() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=0,
        ignored_reports=(
            ReportLoadFailure(Path("/tmp/results/bad-qual"), "unreadable file"),
        ),
        ranked=(),
        not_enough_history=(),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(None, None, (), ()),
    )
    text = render_history_text(analysis)
    assert "Traceback" not in text
    assert "Errno" not in text


def test_output_never_uses_forbidden_pricing_words() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=3,
        ignored_reports=(),
        ranked=(CanaryEffectiveness("sample", 3, 2, 2 / 3),),
        not_enough_history=(),
        per_canary_estimates=(
            CanaryEstimate("sample", (60_600,), (12_346,), 60_600.0, 12_345.6),
        ),
        suite_estimate=SuiteEstimate(60_600.0, 12_345.6, (), ()),
    )
    text = render_history_text(analysis).lower()
    for forbidden in ("pricing", "price", "billing", "cost", "cap ", "$"):
        assert forbidden not in text


def test_forbidden_word_check_targets_renderer_copy_not_canary_ids() -> None:
    analysis = HistoryAnalysis(
        loaded_reports=1,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(CanaryEffectiveness("pricing-canary", 1, 0, None),),
        per_canary_estimates=(),
        suite_estimate=SuiteEstimate(None, None, (), ()),
    )
    text = render_history_text(analysis)
    assert "pricing-canary" in text


def test_runtime_section_always_notes_excluded_overhead() -> None:
    text = render_history_text(empty_analysis())
    section = text[text.index("Estimated model-attempt runtime") :]
    assert "setup" in section.lower()
    assert "preparation" in section.lower()
    assert "cli overhead" in section.lower()


def test_qualock_history_public_output_is_unchanged() -> None:
    text = render_history_text(empty_analysis())

    assert text == (
        "Loaded 0 qualification report(s).\n"
        "Ignored 0 report(s).\n"
        "No qualification history found yet. "
        "Run `qualock check` to start building history.\n"
        "\n"
        "Estimated model-attempt runtime\n"
        "  Suite estimate unavailable.\n"
        "  Note: this excludes setup, preparation, and CLI overhead "
        "(model-attempt time only).\n"
        "\n"
        "Estimated model tokens\n"
        "  Suite estimate unavailable.\n"
        "\n"
        "Ranked current canaries\n"
        "  (none)\n"
        "\n"
        "Not-enough-history current canaries\n"
        "  (none)\n"
    )


def test_history_renderer_does_not_consult_pricing_package() -> None:
    import inspect

    import qualock.history.render as render_module

    source = inspect.getsource(render_module)
    assert "qualock.pricing" not in source
    assert "pricing" not in source.lower()
