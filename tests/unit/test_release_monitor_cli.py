from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import qualock.release_monitor.commands as monitor_commands
from qualock import cli
from qualock.agents.releases import ReleaseDiscoveryError
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import CommandError
from qualock.config.io import ConfigError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.models import MonitorAction, MonitorOutcome
from tests.unit.test_report import sample_result

runner = CliRunner()


def result_with_verdict(verdict: Verdict) -> QualificationResult:
    source = sample_result()
    return source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=source.run_order,
    )


def monitor_outcome(
    action: MonitorAction,
    *,
    agent_name: str = "codex",
    result: QualificationResult | None = None,
    recorded: Verdict | None = None,
    warning: str | None = None,
) -> MonitorOutcome:
    return MonitorOutcome(
        action=action,
        agent_name=agent_name,
        baseline_version="0.151.0",
        latest_version="0.152.0",
        qualification_result=result,
        recorded_verdict=recorded,
        state_warning=warning,
    )


def invoke_outcome(tmp_path: Path, monkeypatch, outcome: MonitorOutcome, *args: str):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "execute_monitor", lambda root, **kwargs: outcome)
    return runner.invoke(cli.app, ["monitor", *args])


def test_monitor_no_new_release_exits_zero(tmp_path: Path, monkeypatch) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="codex",
        baseline_version="0.151.0",
        latest_version="0.151.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome)
    assert result.exit_code == 0
    assert "QuaLock Release Monitor" in result.stdout
    assert "Baseline: Codex 0.151.0" in result.stdout
    assert "Latest:   Codex 0.151.0" in result.stdout
    assert "No newer Codex release needs qualification." in result.stdout


def test_monitor_claude_no_new_release_uses_claude_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="claude",
        baseline_version="2.1.260",
        latest_version="2.1.260",
    )

    result = invoke_outcome(tmp_path, monkeypatch, outcome)

    assert result.exit_code == 0
    assert "Baseline: Claude Code 2.1.260" in result.stdout
    assert "Latest:   Claude Code 2.1.260" in result.stdout
    assert "No newer Claude Code release needs qualification." in result.stdout
    assert "Codex" not in result.stdout


def test_monitor_claude_already_qualified_uses_claude_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(
            MonitorAction.ALREADY_QUALIFIED,
            agent_name="claude",
            recorded=Verdict.PASS,
        ),
    )

    assert result.exit_code == 0
    assert "Claude Code 0.152.0 was already qualified" in result.stdout
    assert "Codex" not in result.stdout


def test_monitor_baseline_newer_than_npm_reports_no_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="codex",
        baseline_version="0.152.0",
        latest_version="0.151.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome)
    assert result.exit_code == 0
    assert "Your baseline is newer than npm latest." in result.stdout
    assert "No downgrade qualification was run." in result.stdout


def test_monitor_no_downgrade_exits_zero(tmp_path: Path, monkeypatch) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.NO_DOWNGRADE, recorded=Verdict.PASS),
    )
    assert result.exit_code == 0
    assert "no downgrade check" in result.stdout.lower()


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [(Verdict.PASS, 0), (Verdict.WARN, 0), (Verdict.BLOCK, 2)],
)
def test_monitor_remembered_terminal_exit(
    tmp_path: Path,
    monkeypatch,
    verdict: Verdict,
    exit_code: int,
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.ALREADY_QUALIFIED, recorded=verdict),
    )
    assert result.exit_code == exit_code
    assert verdict.value.upper() in result.stdout


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [
        (Verdict.PASS, 0),
        (Verdict.WARN, 0),
        (Verdict.BLOCK, 2),
        (Verdict.INCOMPLETE, 4),
    ],
)
def test_monitor_checked_result_uses_existing_safety_summary(
    tmp_path: Path,
    monkeypatch,
    verdict: Verdict,
    exit_code: int,
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(MonitorAction.CHECKED, result=result_with_verdict(verdict)),
    )
    assert result.exit_code == exit_code
    assert "QuaLock Safety Check" in result.stdout
    assert "Technical evidence: .qualock/results/q1/" in result.stdout


@pytest.mark.parametrize(
    "exc",
    [
        ConfigError("bad config"),
        CanaryLoadError("bad canary"),
        CommandError("bad input"),
        FileNotFoundError("missing"),
    ],
)
def test_monitor_input_errors_exit_three(
    tmp_path: Path, monkeypatch, exc: Exception
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(exc),
    )
    result = runner.invoke(cli.app, ["monitor"])
    assert result.exit_code == 3


def test_monitor_gemini_no_new_release_uses_gemini_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="gemini",
        baseline_version="0.59.0",
        latest_version="0.59.0",
    )

    result = invoke_outcome(tmp_path, monkeypatch, outcome)

    assert result.exit_code == 0
    assert "Baseline: Gemini CLI 0.59.0" in result.stdout
    assert "Latest:   Gemini CLI 0.59.0" in result.stdout
    assert "No newer Gemini CLI release needs qualification." in result.stdout
    assert "Codex" not in result.stdout


def test_monitor_outcome_with_unsupported_agent_exits_three(
    tmp_path: Path, monkeypatch
) -> None:
    outcome = MonitorOutcome(
        action=MonitorAction.NO_NEW_RELEASE,
        agent_name="cursor",
        baseline_version="0.151.0",
        latest_version="0.151.0",
    )
    result = invoke_outcome(tmp_path, monkeypatch, outcome)
    assert result.exit_code == 3


def test_monitor_antigravity_capability_failure_exits_three_end_to_end(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        monitor_commands,
        "load_project",
        lambda root: (SimpleNamespace(agent=SimpleNamespace(name="antigravity")), []),
    )
    monkeypatch.setattr(
        monitor_commands,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="antigravity", version="1.0.0")
        ),
    )
    monkeypatch.setattr(
        monitor_commands, "suite_fingerprint", lambda canaries: "suite-now"
    )
    monkeypatch.setattr(
        monitor_commands, "config_fingerprint", lambda config: "config-now"
    )
    monkeypatch.setattr(monitor_commands, "assert_suite_fresh", lambda *args: None)

    result = runner.invoke(cli.app, ["monitor"])

    assert result.exit_code == 3
    assert "release monitor is unavailable for Antigravity" in result.stdout


def test_monitor_stale_baseline_exits_four(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(BaselineStaleError("stale")),
    )
    assert runner.invoke(cli.app, ["monitor"]).exit_code == 4


def test_monitor_operational_error_exits_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(OSError("state unavailable")),
    )
    assert runner.invoke(cli.app, ["monitor"]).exit_code == 1


def test_release_discovery_error_exits_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "execute_monitor",
        lambda root, **kwargs: (_ for _ in ()).throw(
            ReleaseDiscoveryError("npm unavailable")
        ),
    )

    result = runner.invoke(cli.app, ["monitor"])

    assert result.exit_code == 1
    assert "npm unavailable" in result.stdout


def test_monitor_warning_is_literal_and_preserves_pass_exit(
    tmp_path: Path, monkeypatch
) -> None:
    result = invoke_outcome(
        tmp_path,
        monkeypatch,
        monitor_outcome(
            MonitorAction.ALREADY_QUALIFIED,
            recorded=Verdict.PASS,
            warning="ignored [literal]",
        ),
    )
    assert result.exit_code == 0
    assert "Warning: ignored [literal]" in result.stdout


def test_monitor_force_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    observed: list[bool] = []
    monkeypatch.chdir(tmp_path)

    def fake_execute_monitor(root: Path, **kwargs) -> MonitorOutcome:
        observed.append(kwargs["force"])
        return monitor_outcome(MonitorAction.NO_NEW_RELEASE)

    monkeypatch.setattr(cli, "execute_monitor", fake_execute_monitor)
    result = runner.invoke(cli.app, ["monitor", "--force"])

    assert result.exit_code == 0
    assert observed == [True]


def test_monitor_check_executor_prints_transition_before_check(monkeypatch) -> None:
    events: list[str] = []
    expected = sample_result()
    monkeypatch.setattr(
        cli,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="codex", version="0.151.0")
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, **kwargs: events.append(message),
    )

    def fake_execute_check(root: Path, candidate: str) -> QualificationResult:
        events.append("check")
        return expected

    monkeypatch.setattr(cli, "execute_check", fake_execute_check)

    result = cli._monitor_check_executor(Path("."), "codex@0.152.0")

    assert result is expected
    assert events == [
        "Baseline: Codex 0.151.0",
        "Latest:   Codex 0.152.0",
        "\nNew Codex release found. Qualifying 0.152.0 against baseline 0.151.0.",
        "check",
    ]


def test_monitor_check_executor_prints_claude_transition(monkeypatch) -> None:
    events: list[str] = []
    expected = sample_result()
    monkeypatch.setattr(
        cli,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="claude", version="2.1.260")
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, **kwargs: events.append(message),
    )
    monkeypatch.setattr(
        cli,
        "execute_check",
        lambda root, candidate: events.append("check") or expected,
    )

    result = cli._monitor_check_executor(Path("."), "claude@2.1.261")

    assert result is expected
    assert events == [
        "Baseline: Claude Code 2.1.260",
        "Latest:   Claude Code 2.1.261",
        (
            "\nNew Claude Code release found. Qualifying 2.1.261 "
            "against baseline 2.1.260."
        ),
        "check",
    ]


def test_monitor_check_executor_labels_baseline_from_lock_not_candidate(
    monkeypatch,
) -> None:
    events: list[str] = []
    expected = sample_result()
    monkeypatch.setattr(
        cli,
        "read_baseline_lock",
        lambda path: SimpleNamespace(
            agent=SimpleNamespace(name="codex", version="0.151.0")
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, **kwargs: events.append(message),
    )
    monkeypatch.setattr(
        cli,
        "execute_check",
        lambda root, candidate: events.append("check") or expected,
    )

    result = cli._monitor_check_executor(Path("."), "claude@2.1.261")

    assert result is expected
    assert events == [
        "Baseline: Codex 0.151.0",
        "Latest:   Claude Code 2.1.261",
        (
            "\nNew Claude Code release found. Qualifying 2.1.261 "
            "against baseline 0.151.0."
        ),
        "check",
    ]


def test_monitor_force_help_is_agent_neutral() -> None:
    result = runner.invoke(cli.app, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "newer release" in result.stdout
    assert "newer Codex release" not in result.stdout
