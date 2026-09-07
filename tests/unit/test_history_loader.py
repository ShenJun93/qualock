import json
from pathlib import Path

import pytest

from qualock.history import (
    HistoricalAttempt,
    HistoricalExecution,
    HistorySummary,
    LoadedReport,
    ReportLoadFailure,
    scan_results,
)


def write_report(results: Path, directory: str, payload: object) -> Path:
    root = results / directory
    root.mkdir(parents=True)
    path = root / "report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def attempt(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "side": "baseline",
        "repetition": 1,
        "success": True,
        "valid": True,
        "duration_ms": 100,
        "usage": {"observed": True, "input_tokens": 10, "output_tokens": 5},
    }
    value.update(overrides)
    return value


def report(qualification_id: str, attempts: list[object] | None = None) -> dict[str, object]:
    return {
        "qualification_id": qualification_id,
        "executions": [
            {
                "canary_id": "canary-a",
                "attempts": [attempt()] if attempts is None else attempts,
            }
        ],
    }


def test_nonexistent_results_directory_returns_empty_summary_without_creating_it(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"

    assert scan_results(results) == HistorySummary(loaded=(), ignored=())
    assert not results.exists()


def test_empty_results_directory_returns_empty_summary(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()

    assert scan_results(results) == HistorySummary(loaded=(), ignored=())


def test_unreadable_report_uses_fixed_non_sensitive_failure_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results"
    path = write_report(results, "q-dir", report("q-id"))
    original_read_text = Path.read_text

    def raise_for_report(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise OSError("sensitive local detail")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", raise_for_report)

    assert scan_results(results) == HistorySummary(
        loaded=(),
        ignored=(ReportLoadFailure(results / "q-dir", "unreadable file"),),
    )


def test_invalid_json_report_is_ignored_with_fixed_reason(tmp_path: Path) -> None:
    results = tmp_path / "results"
    path = write_report(results, "q-dir", {})
    path.write_text("{not JSON", encoding="utf-8")

    assert scan_results(results).ignored == (
        ReportLoadFailure(results / "q-dir", "invalid JSON"),
    )


@pytest.mark.parametrize("payload", [[], "report", 12, None])
def test_non_object_json_report_is_ignored_with_fixed_reason(
    tmp_path: Path, payload: object
) -> None:
    results = tmp_path / "results"
    write_report(results, "q-dir", payload)

    assert scan_results(results).ignored == (
        ReportLoadFailure(results / "q-dir", "not a JSON object"),
    )


@pytest.mark.parametrize("qualification_id", [None, "", 7, False])
def test_missing_or_invalid_qualification_identity_rejects_report(
    tmp_path: Path, qualification_id: object
) -> None:
    results = tmp_path / "results"
    payload: dict[str, object] = {"executions": []}
    if qualification_id is not None:
        payload["qualification_id"] = qualification_id
    write_report(results, "q-dir", payload)

    assert scan_results(results).ignored == (
        ReportLoadFailure(results / "q-dir", "missing or invalid qualification_id"),
    )


@pytest.mark.parametrize("executions", [None, {}, "executions", False])
def test_missing_or_invalid_executions_list_rejects_report(
    tmp_path: Path, executions: object
) -> None:
    results = tmp_path / "results"
    payload: dict[str, object] = {"qualification_id": "q-id"}
    if executions is not None:
        payload["executions"] = executions
    write_report(results, "q-dir", payload)

    assert scan_results(results).ignored == (
        ReportLoadFailure(results / "q-dir", "missing or invalid executions list"),
    )


@pytest.mark.parametrize(
    "execution",
    [
        "execution",
        {},
        {"canary_id": "", "attempts": []},
        {"canary_id": 8, "attempts": []},
        {"canary_id": "canary-a"},
        {"canary_id": "canary-a", "attempts": {}},
    ],
)
def test_malformed_execution_entry_rejects_the_whole_report(
    tmp_path: Path, execution: object
) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "q-dir",
        {"qualification_id": "q-id", "executions": [execution]},
    )

    assert scan_results(results) == HistorySummary(
        loaded=(),
        ignored=(ReportLoadFailure(results / "q-dir", "malformed execution entry"),),
    )


def test_mixed_valid_and_malformed_reports_are_processed_independently(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(results, "a-valid", report("q-good", []))
    write_report(results, "b-invalid", {"qualification_id": "q-bad"})

    assert scan_results(results) == HistorySummary(
        loaded=(
            LoadedReport(
                qualification_id="q-good",
                qualification_dir=results / "a-valid",
                executions=(HistoricalExecution(canary_id="canary-a", attempts=()),),
            ),
        ),
        ignored=(
            ReportLoadFailure(results / "b-invalid", "missing or invalid executions list"),
        ),
    )


def test_raw_contradictory_verdict_and_reason_fields_are_ignored(tmp_path: Path) -> None:
    results = tmp_path / "results"
    payload = report("q-id", [attempt()])
    payload.update({"verdict": "block", "reasons": ["must not be parsed"]})
    execution = payload["executions"][0]  # type: ignore[index]
    execution.update({"verdict": "pass", "reason": "contradictory policy prose"})
    write_report(results, "q-dir", payload)

    loaded = scan_results(results).loaded

    assert loaded == (
        LoadedReport(
            qualification_id="q-id",
            qualification_dir=results / "q-dir",
            executions=(
                HistoricalExecution(
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
                ),
            ),
        ),
    )
    assert not hasattr(loaded[0], "verdict")
    assert not hasattr(loaded[0].executions[0], "reason")


def test_malformed_attempt_element_normalizes_without_rejecting_report(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    write_report(results, "q-dir", report("q-id", ["malformed", attempt(side="candidate")]))

    assert scan_results(results).loaded[0].executions[0].attempts == (
        HistoricalAttempt(
            side=None,
            repetition=None,
            success=None,
            valid=None,
            duration_ms=None,
            input_tokens=None,
            output_tokens=None,
            usage_observed=False,
        ),
        HistoricalAttempt(
            side="candidate",
            repetition=1,
            success=True,
            valid=True,
            duration_ms=100,
            input_tokens=10,
            output_tokens=5,
            usage_observed=True,
        ),
    )
    assert scan_results(results).ignored == ()


def test_pre_batch_40_attempt_without_usage_is_salvaged(tmp_path: Path) -> None:
    results = tmp_path / "results"
    pre_40_attempt = attempt()
    del pre_40_attempt["usage"]
    write_report(results, "q-dir", report("q-id", [pre_40_attempt]))

    assert scan_results(results).loaded[0].executions[0].attempts == (
        HistoricalAttempt(
            side="baseline",
            repetition=1,
            success=True,
            valid=True,
            duration_ms=100,
            input_tokens=None,
            output_tokens=None,
            usage_observed=False,
        ),
    )


def test_attempt_fields_normalize_independently_and_reject_bools_as_integers(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    malformed = attempt(
        side=4,
        repetition=True,
        success=1,
        valid="true",
        duration_ms=False,
        usage={"observed": 1, "input_tokens": True, "output_tokens": False},
    )
    write_report(results, "q-dir", report("q-id", [malformed]))

    assert scan_results(results).loaded[0].executions[0].attempts == (
        HistoricalAttempt(
            side=None,
            repetition=None,
            success=None,
            valid=None,
            duration_ms=None,
            input_tokens=None,
            output_tokens=None,
            usage_observed=False,
        ),
    )


def test_scan_is_directory_name_sorted_path_neutral_and_report_only(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(results, "z-last", report("q-z", []))
    write_report(results, "a-first", report("q-a", []))
    (results / "middle-baseline-only").mkdir()
    (results / "middle-baseline-only" / "baseline.json").write_text("{}", encoding="utf-8")
    (results / "report.json").write_text("{}", encoding="utf-8")

    summary = scan_results(results)

    assert tuple(item.qualification_id for item in summary.loaded) == ("q-a", "q-z")
    assert tuple(item.qualification_dir.name for item in summary.loaded) == (
        "a-first",
        "z-last",
    )
    assert summary.ignored == ()


def test_duplicate_qualification_id_uses_first_loaded_directory(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(results, "z-created-first", report("duplicate", [attempt(side="candidate")]))
    write_report(results, "a-created-second", report("duplicate", [attempt(side="baseline")]))

    summary = scan_results(results)

    assert tuple(item.qualification_dir.name for item in summary.loaded) == ("a-created-second",)
    assert summary.loaded[0].executions[0].attempts[0].side == "baseline"
    assert summary.ignored == (
        ReportLoadFailure(results / "z-created-first", "duplicate qualification_id"),
    )


def test_structurally_invalid_report_does_not_reserve_qualification_id(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "a-malformed",
        {"qualification_id": "shared", "executions": [{"canary_id": "canary-a"}]},
    )
    write_report(results, "b-valid", report("shared", []))

    summary = scan_results(results)

    assert tuple(item.qualification_dir.name for item in summary.loaded) == ("b-valid",)
    assert summary.ignored == (
        ReportLoadFailure(results / "a-malformed", "malformed execution entry"),
    )


def test_valid_project_protection_envelope_is_silently_skipped(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "pp-dir",
        {"kind": "project_protection", "result": {"ok": True}},
    )
    write_report(results, "q-dir", report("q-id"))

    summary = scan_results(results)

    assert tuple(item.qualification_id for item in summary.loaded) == ("q-id",)
    assert summary.ignored == ()


def test_malformed_project_protection_like_report_is_still_ignored(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(results, "q-dir", {"kind": "project_protection", "result": "not-an-object"})

    assert scan_results(results).ignored == (
        ReportLoadFailure(results / "q-dir", "missing or invalid qualification_id"),
    )


def test_non_utf8_report_is_ignored_with_fixed_reason_and_siblings_still_load(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    write_report(results, "a-good", report("q-good"))
    bad_dir = results / "b-bad"
    bad_dir.mkdir()
    (bad_dir / "report.json").write_bytes(b"\xff\xfe\x00not valid utf-8")

    summary = scan_results(results)

    assert tuple(item.qualification_id for item in summary.loaded) == ("q-good",)
    assert summary.ignored == (ReportLoadFailure(bad_dir, "unreadable file"),)


def test_historical_attempt_new_fields_default_to_none_with_old_argument_set() -> None:
    attempt_result = HistoricalAttempt(
        side="baseline",
        repetition=1,
        success=True,
        valid=True,
        duration_ms=100,
        input_tokens=10,
        output_tokens=5,
        usage_observed=True,
    )

    assert attempt_result.cached_input_tokens is None
    assert attempt_result.cache_write_input_tokens is None
    assert attempt_result.reasoning_output_tokens is None


def test_new_usage_detail_fields_normalize_from_usage_object(tmp_path: Path) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "q-dir",
        report(
            "q-id",
            [
                attempt(
                    usage={
                        "observed": True,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": 4,
                        "cache_write_input_tokens": 2,
                        "reasoning_output_tokens": 3,
                    }
                )
            ],
        ),
    )

    attempt_result = scan_results(results).loaded[0].executions[0].attempts[0]
    assert attempt_result.cached_input_tokens == 4
    assert attempt_result.cache_write_input_tokens == 2
    assert attempt_result.reasoning_output_tokens == 3


def test_new_usage_detail_fields_normalize_independently_and_reject_bools(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "q-dir",
        report(
            "q-id",
            [
                attempt(
                    usage={
                        "observed": True,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": True,
                        "cache_write_input_tokens": "2",
                        "reasoning_output_tokens": 3,
                    }
                )
            ],
        ),
    )

    attempt_result = scan_results(results).loaded[0].executions[0].attempts[0]
    assert attempt_result.cached_input_tokens is None
    assert attempt_result.cache_write_input_tokens is None
    assert attempt_result.reasoning_output_tokens == 3


def test_missing_usage_detail_keys_and_malformed_usage_yield_none_for_all_three(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    write_report(
        results,
        "q-dir",
        report(
            "q-id",
            [
                attempt(usage={"observed": True, "input_tokens": 10, "output_tokens": 5}),
                attempt(usage="not-an-object"),
            ],
        ),
    )

    attempts = scan_results(results).loaded[0].executions[0].attempts
    for attempt_result in attempts:
        assert attempt_result.cached_input_tokens is None
        assert attempt_result.cache_write_input_tokens is None
        assert attempt_result.reasoning_output_tokens is None


def test_scan_preserves_report_bytes_and_mtimes(tmp_path: Path) -> None:
    results = tmp_path / "results"
    paths = (
        write_report(results, "a-report", report("q-a")),
        write_report(results, "b-report", report("q-b")),
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    scan_results(results)

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    assert after == before
