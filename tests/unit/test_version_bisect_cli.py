from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock import cli
from qualock.agents.resolver import CodexResolveError
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.qualification.models import Verdict
from qualock.version_bisect.models import BisectOutcome, BisectStep, BisectStop

runner = CliRunner()


def make_outcome(
    stop_reason: BisectStop,
    *,
    steps: tuple[BisectStep, ...],
    last_known_good: str = "0.151.0",
    first_bad: str | None = None,
    upper_version: str = "0.153.0",
) -> BisectOutcome:
    return BisectOutcome(
        bisect_id="bisect-20260903T120000Z-aaaaaaaa",
        baseline_version="0.151.0",
        upper_version=upper_version,
        steps=steps,
        last_known_good=last_known_good,
        first_bad=first_bad,
        stop_reason=stop_reason,
    )


def invoke_outcome(tmp_path: Path, monkeypatch, outcome: BisectOutcome, *args: str):
    monkeypatch.chdir(tmp_path)

    def fake_execute_bisect(root: Path, upper_spec: str, *, on_start=None, on_step=None, **kwargs):
        if on_start is not None:
            on_start(outcome.baseline_version, outcome.upper_version, tmp_path)
        if on_step is not None:
            for step in outcome.steps:
                on_step(step)
        return outcome

    monkeypatch.setattr(cli, "execute_bisect", fake_execute_bisect)
    return runner.invoke(cli.app, ["bisect", *args])


def test_bisect_first_bad_found_prints_report_and_exits_two(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="0.152.0", qualification_id="q1", verdict=Verdict.BLOCK),
    )
    outcome = make_outcome(
        BisectStop.FIRST_BAD_FOUND,
        steps=steps,
        last_known_good="0.151.0",
        first_bad="0.152.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "0.153.0")

    assert "QuaLock Version Bisect" in result.stdout
    assert "Baseline: Codex 0.151.0" in result.stdout
    assert "Searching through: 0.153.0" in result.stdout
    assert "0.152.0  BLOCK" in result.stdout
    assert "FIRST BAD RELEASE" in result.stdout
    assert "0.151.0" in result.stdout
    assert f".qualock/results/{outcome.bisect_id}/" in result.stdout
    assert result.exit_code == 2


def test_bisect_no_bad_found_prints_summary_and_exits_zero(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="0.152.0", qualification_id="q1", verdict=Verdict.PASS),
        BisectStep(version="0.153.0", qualification_id="q2", verdict=Verdict.PASS),
    )
    outcome = make_outcome(
        BisectStop.NO_BAD_FOUND,
        steps=steps,
        last_known_good="0.153.0",
        upper_version="0.153.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "0.153.0")

    assert "No confirmed bad release found through Codex 0.153.0." in result.stdout
    assert result.exit_code == 0


def test_bisect_warn_unresolved_prints_search_stopped_and_exits_four(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="0.152.0", qualification_id="q1", verdict=Verdict.WARN),
    )
    outcome = make_outcome(BisectStop.WARN_UNRESOLVED, steps=steps)
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "0.153.0")

    assert "SEARCH STOPPED" in result.stdout
    assert "WARN" in result.stdout
    assert "No first bad release was claimed." in result.stdout
    assert result.exit_code == 4


def test_bisect_incomplete_prints_search_stopped_and_exits_four(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="0.152.0", qualification_id="q1", verdict=Verdict.INCOMPLETE),
    )
    outcome = make_outcome(BisectStop.INCOMPLETE, steps=steps)
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "0.153.0")

    assert "SEARCH STOPPED" in result.stdout
    assert "INCOMPLETE" in result.stdout
    assert "No first bad release was claimed." in result.stdout
    assert result.exit_code == 4


@pytest.mark.parametrize(
    "exc",
    [
        CommandError("bad input"),
        ConfigError("bad config"),
        CanaryLoadError("bad canary"),
        FileNotFoundError("missing"),
    ],
)
def test_bisect_input_errors_exit_three(
    tmp_path: Path, monkeypatch, exc: Exception
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(exc),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 3


def test_bisect_stale_baseline_exits_four(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            BaselineStaleError("stale")
        ),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 4


def test_bisect_codex_resolve_error_exits_one_with_literal_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            CodexResolveError("npm registry unreachable [literal]")
        ),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 1
    assert "npm registry unreachable [literal]" in result.stdout


def test_bisect_unexpected_os_error_exits_one_with_literal_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            OSError("disk full [literal]")
        ),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 1
    assert "disk full [literal]" in result.stdout
