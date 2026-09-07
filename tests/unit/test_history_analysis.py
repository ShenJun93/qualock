from pathlib import Path

from qualock.history import (
    CanaryEffectiveness,
    HistoricalAttempt,
    HistoricalExecution,
    HistorySummary,
    LoadedReport,
    ReportLoadFailure,
    analyze_history,
)


def hist_attempt(
    *,
    side: str | None = "baseline",
    repetition: int | None = 1,
    success: bool | None = True,
    valid: bool | None = True,
    duration_ms: int | None = 100,
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    usage_observed: bool = True,
) -> HistoricalAttempt:
    return HistoricalAttempt(
        side=side,
        repetition=repetition,
        success=success,
        valid=valid,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_observed=usage_observed,
    )


def paired(canary_id: str, *attempts: HistoricalAttempt) -> HistoricalExecution:
    return HistoricalExecution(canary_id=canary_id, attempts=tuple(attempts))


def eligible_pair(canary_id: str, **overrides: object) -> HistoricalExecution:
    baseline = hist_attempt(side="baseline", repetition=1, **overrides)  # type: ignore[arg-type]
    candidate = hist_attempt(side="candidate", repetition=1, **overrides)  # type: ignore[arg-type]
    return paired(canary_id, baseline, candidate)


def summary_of(*reports: LoadedReport, ignored: tuple[ReportLoadFailure, ...] = ()) -> HistorySummary:
    return HistorySummary(loaded=tuple(reports), ignored=ignored)


def loaded(qualification_id: str, *executions: HistoricalExecution, directory: str = "q-dir") -> LoadedReport:
    return LoadedReport(
        qualification_id=qualification_id,
        qualification_dir=Path(directory),
        executions=tuple(executions),
    )


def repeat(execution_factory, count: int) -> tuple[LoadedReport, ...]:
    return tuple(
        loaded(f"q-{index}", execution_factory(), directory=f"q-dir-{index}")
        for index in range(count)
    )


# ---------------------------------------------------------------------------
# Step 1: pairing identity and metric independence
# ---------------------------------------------------------------------------


def test_empty_attempts_execution_excluded_from_all_metrics() -> None:
    summary = summary_of(loaded("q-1", paired("canary-a")))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == ()
    assert estimate.token_samples == ()
    assert analysis.not_enough_history == (
        CanaryEffectiveness("canary-a", eligible_samples=0, detections=0, detection_rate=None),
    )


def test_mismatched_pairing_execution_excluded_from_all_metrics() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="baseline", repetition=2),
        hist_attempt(side="candidate", repetition=1),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == ()
    assert estimate.token_samples == ()
    assert analysis.not_enough_history[0].eligible_samples == 0


def test_duplicate_slot_pairing_execution_excluded_from_all_metrics() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == ()
    assert estimate.token_samples == ()
    assert analysis.not_enough_history[0].eligible_samples == 0


def test_all_success_stable_baseline_execution_is_eligible_non_detection() -> None:
    execution = eligible_pair("canary-a")
    summary = summary_of(*repeat(lambda: execution, 3))

    analysis = analyze_history(summary, ["canary-a"])

    ranked = analysis.ranked[0]
    assert ranked.eligible_samples == 3
    assert ranked.detections == 0
    assert ranked.detection_rate == 0.0


def test_malformed_success_loses_effectiveness_only() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1, success=None),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    assert analysis.not_enough_history[0].eligible_samples == 0
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == (200,)
    assert estimate.token_samples == (30,)


def test_valid_false_loses_effectiveness_but_keeps_runtime_and_token() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1, valid=False),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    assert analysis.not_enough_history[0].eligible_samples == 0
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == (200,)
    assert estimate.token_samples == (30,)


def test_malformed_or_negative_duration_loses_runtime_only() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1, duration_ms=-1),
    )
    summary = summary_of(*repeat(lambda: execution, 3))

    analysis = analyze_history(summary, ["canary-a"])

    ranked = analysis.ranked[0]
    assert ranked.eligible_samples == 3
    assert ranked.detections == 0
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == ()
    assert estimate.token_samples == (30, 30, 30)


def test_unobserved_or_missing_tokens_lose_token_only() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1, usage_observed=False),
    )
    summary = summary_of(*repeat(lambda: execution, 3))

    analysis = analyze_history(summary, ["canary-a"])

    ranked = analysis.ranked[0]
    assert ranked.eligible_samples == 3
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == (200, 200, 200)
    assert estimate.token_samples == ()


def test_candidate_failure_with_all_valid_is_detection_and_cost_sample() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1),
        hist_attempt(side="candidate", repetition=1, success=False),
    )
    summary = summary_of(*repeat(lambda: execution, 3))

    analysis = analyze_history(summary, ["canary-a"])

    ranked = analysis.ranked[0]
    assert ranked.eligible_samples == 3
    assert ranked.detections == 3
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == (200, 200, 200)
    assert estimate.token_samples == (30, 30, 30)


def test_unstable_baseline_loses_effectiveness_only() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1, success=False),
        hist_attempt(side="candidate", repetition=1),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    assert analysis.not_enough_history[0].eligible_samples == 0
    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_samples_ms == (200,)
    assert estimate.token_samples == (30,)


def test_cache_and_reasoning_absence_from_model_cannot_affect_token_totals() -> None:
    execution = paired(
        "canary-a",
        hist_attempt(side="baseline", repetition=1, input_tokens=1000, output_tokens=2000),
        hist_attempt(side="candidate", repetition=1, input_tokens=1000, output_tokens=2000),
    )
    summary = summary_of(loaded("q-1", execution))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.token_samples == (6000,)
    assert not hasattr(execution.attempts[0], "cached_input_tokens")
    assert not hasattr(execution.attempts[0], "cache_write_input_tokens")
    assert not hasattr(execution.attempts[0], "reasoning_output_tokens")


# ---------------------------------------------------------------------------
# Step 2: ranking and current-suite filtering
# ---------------------------------------------------------------------------


def _effectiveness_report(canary_id: str, detections: int, non_detections: int, directory: str) -> LoadedReport:
    executions = []
    for i in range(detections):
        executions.append(
            paired(
                canary_id,
                hist_attempt(side="baseline", repetition=1),
                hist_attempt(side="candidate", repetition=1, success=False),
            )
        )
    for i in range(non_detections):
        executions.append(eligible_pair(canary_id))
    return loaded(f"q-{directory}", *executions, directory=directory)


def test_ranking_and_current_suite_filtering() -> None:
    current_ids = ["critical-a", "workflow-z", "workflow-b", "workflow-a"]

    reports = (
        # critical-a: 3 detections / 3 eligible => rate 1.0
        loaded(
            "q-critical-a",
            *[
                paired(
                    "critical-a",
                    hist_attempt(side="baseline", repetition=1),
                    hist_attempt(side="candidate", repetition=1, success=False),
                )
                for _ in range(3)
            ],
            directory="d-critical-a",
        ),
        # workflow-z: 1 detection, 2 non-detections => 3 eligible, rate 1/3
        loaded(
            "q-workflow-z",
            paired(
                "workflow-z",
                hist_attempt(side="baseline", repetition=1),
                hist_attempt(side="candidate", repetition=1, success=False),
            ),
            eligible_pair("workflow-z"),
            eligible_pair("workflow-z"),
            directory="d-workflow-z",
        ),
        # workflow-b: 2 eligible samples -> not enough history
        loaded(
            "q-workflow-b",
            eligible_pair("workflow-b"),
            eligible_pair("workflow-b"),
            directory="d-workflow-b",
        ),
        # workflow-a: 0 eligible samples -> not enough history
        loaded("q-workflow-a", paired("workflow-a"), directory="d-workflow-a"),
        # obsolete: abundant eligible history but not in current suite
        loaded(
            "q-obsolete",
            *[eligible_pair("obsolete") for _ in range(5)],
            directory="d-obsolete",
        ),
    )
    summary = summary_of(*reports)

    analysis = analyze_history(summary, current_ids)

    assert [item.canary_id for item in analysis.ranked] == ["critical-a", "workflow-z"]
    assert [item.canary_id for item in analysis.not_enough_history] == ["workflow-b", "workflow-a"]
    assert all(item.canary_id != "obsolete" for item in analysis.per_canary_estimates)
    assert all(item.canary_id != "obsolete" for item in analysis.ranked)
    assert all(item.canary_id != "obsolete" for item in analysis.not_enough_history)


def test_ranking_threshold_boundary_at_three() -> None:
    def executions(count: int) -> list[HistoricalExecution]:
        return [eligible_pair("canary-a") for _ in range(count)]

    for count, expect_ranked in ((0, False), (1, False), (2, False), (3, True)):
        summary = summary_of(loaded("q", *executions(count)))
        analysis = analyze_history(summary, ["canary-a"])
        if expect_ranked:
            assert [item.canary_id for item in analysis.ranked] == ["canary-a"]
            assert analysis.not_enough_history == ()
        else:
            assert analysis.ranked == ()
            assert [item.canary_id for item in analysis.not_enough_history] == ["canary-a"]


def test_deterministic_tie_break_by_eligible_samples_then_canary_id() -> None:
    # canary-b and canary-c: both zero detections, so detection_rate (0.0) and
    # detections (0) tie identically; differ only by eligible_samples (4 vs 3).
    a = loaded(
        "q-a",
        eligible_pair("canary-b"),
        eligible_pair("canary-b"),
        eligible_pair("canary-b"),
        eligible_pair("canary-b"),
        directory="d-a",
    )
    b = loaded(
        "q-b",
        eligible_pair("canary-c"),
        eligible_pair("canary-c"),
        eligible_pair("canary-c"),
        directory="d-b",
    )
    # canary-d and canary-e: identical on all three numeric keys (3/0/0.0), differ only by ID
    c = loaded("q-c", eligible_pair("canary-e"), eligible_pair("canary-e"), eligible_pair("canary-e"), directory="d-c")
    d = loaded("q-d", eligible_pair("canary-d"), eligible_pair("canary-d"), eligible_pair("canary-d"), directory="d-d")

    summary = summary_of(a, b, c, d)
    analysis = analyze_history(summary, ["canary-b", "canary-c", "canary-d", "canary-e"])

    assert [item.canary_id for item in analysis.ranked] == [
        "canary-b",
        "canary-c",
        "canary-d",
        "canary-e",
    ]


def test_policy_string_independence_structural() -> None:
    assert not hasattr(HistoricalAttempt, "verdict")
    assert not hasattr(HistoricalAttempt, "reason")
    assert not hasattr(HistoricalExecution, "verdict")
    assert not hasattr(HistoricalExecution, "reason")


# ---------------------------------------------------------------------------
# Step 3: medians and suite all-or-nothing estimates
# ---------------------------------------------------------------------------


def _execution_with_totals(canary_id: str, duration: int, token: int) -> HistoricalExecution:
    return paired(
        canary_id,
        hist_attempt(side="baseline", repetition=1, duration_ms=0, input_tokens=0, output_tokens=0),
        hist_attempt(
            side="candidate",
            repetition=1,
            duration_ms=duration,
            input_tokens=token // 2,
            output_tokens=token - token // 2,
        ),
    )


def test_runtime_and_token_median_odd_sample_count() -> None:
    executions = [
        _execution_with_totals("canary-a", duration, token)
        for duration, token in zip([100, 500, 300], [10, 30, 20], strict=True)
    ]
    summary = summary_of(loaded("q", *executions))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_median_ms == 300
    assert estimate.token_median == 20


def test_runtime_and_token_median_even_sample_count_without_pre_rounding() -> None:
    executions = [
        _execution_with_totals("canary-a", duration, token)
        for duration, token in zip([1500, 1501], [125, 126], strict=True)
    ]
    summary = summary_of(loaded("q", *executions))

    analysis = analyze_history(summary, ["canary-a"])

    estimate = analysis.per_canary_estimates[0]
    assert estimate.runtime_median_ms == 1500.5
    assert estimate.token_median == 125.5


def test_suite_estimate_available_for_runtime_but_missing_one_token_sample() -> None:
    current_ids = ["canary-a", "canary-b"]
    a = loaded(
        "q-a",
        eligible_pair("canary-a"),
        directory="d-a",
    )
    b = loaded(
        "q-b",
        paired(
            "canary-b",
            hist_attempt(side="baseline", repetition=1, usage_observed=False),
            hist_attempt(side="candidate", repetition=1, usage_observed=False),
        ),
        directory="d-b",
    )
    summary = summary_of(a, b)

    analysis = analyze_history(summary, current_ids)

    assert analysis.suite_estimate.runtime_ms == 400.0
    assert analysis.suite_estimate.tokens is None
    assert analysis.suite_estimate.missing_token_canaries == ("canary-b",)
    assert analysis.suite_estimate.missing_runtime_canaries == ()


def test_suite_estimate_available_for_tokens_but_missing_one_runtime_sample() -> None:
    current_ids = ["canary-a", "canary-b"]
    a = loaded(
        "q-a",
        eligible_pair("canary-a"),
        directory="d-a",
    )
    b = loaded(
        "q-b",
        paired(
            "canary-b",
            hist_attempt(side="baseline", repetition=1, duration_ms=-1),
            hist_attempt(side="candidate", repetition=1, duration_ms=-1),
        ),
        directory="d-b",
    )
    summary = summary_of(a, b)

    analysis = analyze_history(summary, current_ids)

    assert analysis.suite_estimate.tokens == 60.0
    assert analysis.suite_estimate.runtime_ms is None
    assert analysis.suite_estimate.missing_runtime_canaries == ("canary-b",)
    assert analysis.suite_estimate.missing_token_canaries == ()


def test_missing_lists_preserve_current_config_order() -> None:
    current_ids = ["workflow-z", "workflow-a", "critical-b"]
    # none of the current canaries have any history at all -> all missing
    summary = summary_of()

    analysis = analyze_history(summary, current_ids)

    assert analysis.suite_estimate.missing_runtime_canaries == (
        "workflow-z",
        "workflow-a",
        "critical-b",
    )
    assert analysis.suite_estimate.missing_token_canaries == (
        "workflow-z",
        "workflow-a",
        "critical-b",
    )
    assert [item.canary_id for item in analysis.not_enough_history] == [
        "workflow-z",
        "workflow-a",
        "critical-b",
    ]


def test_partial_budgeted_historical_run_contributes_per_canary_medians_only() -> None:
    current_ids = ["canary-a", "canary-b"]
    report_data = loaded(
        "q",
        eligible_pair("canary-a"),
        paired("canary-b"),  # attempts=() budget-skipped
        directory="d",
    )
    summary = summary_of(report_data)

    analysis = analyze_history(summary, current_ids)

    estimates = {item.canary_id: item for item in analysis.per_canary_estimates}
    assert estimates["canary-a"].runtime_samples_ms == (200,)
    assert estimates["canary-a"].runtime_median_ms == 200.0
    assert estimates["canary-b"].runtime_samples_ms == ()
    assert estimates["canary-b"].runtime_median_ms is None
    assert analysis.suite_estimate.runtime_ms is None
    assert analysis.suite_estimate.missing_runtime_canaries == ("canary-b",)
