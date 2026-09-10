from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import qualock.version_bisect.commands as bisect_commands
from qualock import cli
from qualock.agents.releases import ReleaseDiscoveryError
from qualock.agents.resolver import CodexResolveError
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.qualification.models import Verdict
from qualock.version_bisect.models import BisectAgent, BisectOutcome, BisectStep, BisectStop

runner = CliRunner()


def patch_gemini_bisect_project(monkeypatch: pytest.MonkeyPatch, *, baseline_version: str) -> None:
    monkeypatch.setattr(
        bisect_commands,
        "load_project",
        lambda root: (SimpleNamespace(agent=SimpleNamespace(name="gemini")), []),
    )
    monkeypatch.setattr(
        bisect_commands,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="gemini", version=baseline_version)
        ),
    )
    monkeypatch.setattr(bisect_commands, "suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr(bisect_commands, "config_fingerprint", lambda config: "config-now")
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)


def make_outcome(
    stop_reason: BisectStop,
    *,
    steps: tuple[BisectStep, ...],
    last_known_good: str = "0.151.0",
    first_bad: str | None = None,
    baseline_version: str = "0.151.0",
    upper_version: str = "0.153.0",
    agent_name: BisectAgent = "codex",
) -> BisectOutcome:
    return BisectOutcome(
        bisect_id="bisect-20260903T120000Z-aaaaaaaa",
        agent_name=agent_name,
        baseline_version=baseline_version,
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
            on_start(outcome.agent_name, outcome.baseline_version, outcome.upper_version, tmp_path)
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


def test_bisect_claude_first_bad_found_prints_report_and_exits_two(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="2.1.261", qualification_id="q1", verdict=Verdict.BLOCK),
    )
    outcome = make_outcome(
        BisectStop.FIRST_BAD_FOUND,
        steps=steps,
        last_known_good="2.1.260",
        first_bad="2.1.261",
        baseline_version="2.1.260",
        upper_version="2.1.263",
        agent_name="claude",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "2.1.263")

    assert "Baseline: Claude Code 2.1.260" in result.stdout
    assert "Searching through: 2.1.263" in result.stdout
    assert "2.1.261  BLOCK" in result.stdout
    assert "FIRST BAD RELEASE" in result.stdout
    assert "Claude Code 2.1.261" in result.stdout
    assert "Last known good: Claude Code 2.1.260" in result.stdout
    assert "Codex" not in result.stdout
    assert result.exit_code == 2


def test_bisect_claude_no_bad_found_prints_summary_and_exits_zero(
    tmp_path: Path, monkeypatch
) -> None:
    steps = (
        BisectStep(version="2.1.261", qualification_id="q1", verdict=Verdict.PASS),
        BisectStep(version="2.1.263", qualification_id="q2", verdict=Verdict.PASS),
    )
    outcome = make_outcome(
        BisectStop.NO_BAD_FOUND,
        steps=steps,
        last_known_good="2.1.263",
        upper_version="2.1.263",
        agent_name="claude",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome, "2.1.263")

    assert "No confirmed bad release found through Claude Code 2.1.263." in result.stdout
    assert "Last known good: Claude Code 2.1.263" in result.stdout
    assert "Codex" not in result.stdout
    assert result.exit_code == 0


def test_print_bisect_start_renders_agent_display_name(tmp_path: Path, capsys) -> None:
    cli._print_bisect_start("claude", "2.1.260", "2.1.263", tmp_path)

    captured = capsys.readouterr()
    assert "Baseline: Claude Code 2.1.260" in captured.out
    assert "Searching through: 2.1.263" in captured.out


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


def test_bisect_antigravity_unsupported_exits_three_with_literal_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            CommandError("version bisect does not support agent 'antigravity'")
        ),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 3
    assert "version bisect does not support agent 'antigravity'" in result.stdout


def test_bisect_release_discovery_error_exits_one_with_literal_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_bisect",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            ReleaseDiscoveryError("catalog unavailable")
        ),
    )
    result = runner.invoke(cli.app, ["bisect", "0.153.0"])
    assert result.exit_code == 1
    assert "catalog unavailable" in result.stdout


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


# --- Step 4: Gemini forward-scan parity (end-to-end through real preflight) --


def test_bisect_cli_gemini_cross_agent_mismatch_rejected_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    patch_gemini_bisect_project(monkeypatch, baseline_version="0.58.0")
    monkeypatch.setattr(
        bisect_commands,
        "default_stable_release_catalog",
        lambda agent_name, **kwargs: SimpleNamespace(
            stable_versions=lambda: ("0.59.0", "0.60.0")
        ),
    )

    result = runner.invoke(cli.app, ["bisect", "codex@0.60.0"])

    assert result.exit_code == 3
    assert "upper bound agent codex does not match baseline agent gemini" in result.stdout


def test_bisect_cli_gemini_upper_not_in_catalog_rejected_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    patch_gemini_bisect_project(monkeypatch, baseline_version="0.58.0")
    monkeypatch.setattr(
        bisect_commands,
        "default_stable_release_catalog",
        lambda agent_name, **kwargs: SimpleNamespace(
            stable_versions=lambda: ("0.59.0", "0.60.0")
        ),
    )

    result = runner.invoke(cli.app, ["bisect", "gemini@0.61.0"])

    assert result.exit_code == 3
    assert "gemini@0.61.0 is not a published stable release" in result.stdout
