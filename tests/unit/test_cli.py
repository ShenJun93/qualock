import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from qualock.canary.loader import CanaryLoadError
from qualock.cli import app
from qualock.commands import CommandError
from qualock.config.io import ConfigError, write_default_config
from qualock.history.models import (
    CanaryEffectiveness,
    CanaryEstimate,
    HistoryAnalysis,
    SuiteEstimate,
)
from qualock.pricing.models import CostAnalysis, SuiteCostEstimate
from qualock.qualification.models import Verdict
from qualock.run.process import ProcessResult
from tests.unit.test_report import sample_result

runner = CliRunner()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def test_init_creates_project_structure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".qualock/config.yaml").is_file()
    assert (tmp_path / ".qualock/canaries").is_dir()
    assert (tmp_path / ".qualock/results").is_dir()
    assert (tmp_path / ".qualock/.gitignore").read_text(encoding="utf-8") == "results/\nwork/\n"


def test_doctor_invalid_config_exits_3(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".qualock/config.yaml"
    config.parent.mkdir()
    config.write_text("schema_version: 2\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3


def test_check_block_verdict_exits_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())
    result = runner.invoke(app, ["check", "codex@0.151.0"])
    assert result.exit_code == 2
    assert "QuaLock Safety Check" in result.stdout
    assert "DON'T UPDATE YET" in result.stdout
    assert "critical-bug" in result.stdout
    assert "Technical evidence: .qualock/results/q1/" in result.stdout


def test_check_easy_output_is_exactly_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 2
    assert result.stdout == (
        "QuaLock Safety Check\n\n"
        "DON'T UPDATE YET\n\n"
        "At least one critical protected workflow regressed.\n\n"
        "Codex 0.150.0 -> 0.151.0\n\n"
        "Protected workflows\n"
        "- REGRESSED: critical-bug  3/3 -> 0/3\n\n"
        "Recommendation:\n"
        "Keep using Codex 0.150.0 for now. Do not update to Codex 0.151.0 until the \n"
        "regression is understood.\n\n"
        "Observed model tokens: unavailable\n\n"
        "Technical evidence: .qualock/results/q1/\n"
    )


def test_check_incomplete_exits_4(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = sample_result()
    incomplete = source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=Verdict.INCOMPLETE,
        executions=source.executions,
        reasons=("invalid evidence",),
        run_order=source.run_order,
    )
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: incomplete)
    result = runner.invoke(app, ["check", "codex@0.151.0"])
    assert result.exit_code == 4
    assert "CHECK COULD NOT FINISH" in result.stdout


def test_check_provenance_command_error_exits_3_without_safety_or_technical_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def fail(root: Path, candidate: str) -> None:
        raise CommandError("qualification evidence provenance could not be written")

    monkeypatch.setattr("qualock.cli.execute_check", fail)

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 3
    assert "SAFE TO UPDATE" not in result.stdout
    assert "DON'T UPDATE YET" not in result.stdout
    assert "REVIEW BEFORE UPDATING" not in result.stdout
    assert "CHECK COULD NOT FINISH" not in result.stdout
    assert "Quality  BLOCK" not in result.stdout
    assert "Recommendation:" not in result.stdout


def test_check_technical_preserves_existing_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "codex@0.151.0", "--technical"])

    assert result.exit_code == 2
    assert "Qualock qualification" in result.stdout
    assert "Quality  BLOCK" in result.stdout
    assert "QuaLock Safety Check" not in result.stdout


def test_report_prints_latest_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".qualock/results/q-old"
    new = tmp_path / ".qualock/results/q-new"
    old.mkdir(parents=True); new.mkdir(parents=True)
    (old / "report.md").write_text("OLD", encoding="utf-8")
    (new / "report.md").write_text("NEW REPORT", encoding="utf-8")
    import os
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert "NEW REPORT" in result.stdout


def test_doctor_fails_when_docker_cli_exists_but_daemon_is_unreachable(tmp_path: Path, monkeypatch) -> None:
    class FakeDockerRunner:
        def available(self) -> bool:
            return True

        def daemon_ready(self) -> bool:
            return False

    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="codex"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.DockerRunner", FakeDockerRunner)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Docker" in result.stdout
    assert "FAIL" in result.stdout


def _doctor_docker_should_not_be_constructed(*args, **kwargs):
    raise AssertionError("antigravity doctor must not touch Docker")


def test_doctor_antigravity_passes_without_touching_docker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="antigravity"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "qualock.cli.AntigravityResolver.locate", lambda self: Path("/usr/bin/agy")
    )
    monkeypatch.setattr("qualock.cli.DockerRunner", _doctor_docker_should_not_be_constructed)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Linux" in result.stdout
    assert "bwrap" in result.stdout
    assert "Antigravity" in result.stdout
    assert "Canaries" in result.stdout
    assert "Docker" not in result.stdout


def test_doctor_antigravity_fails_on_non_linux_host(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="antigravity"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "qualock.cli.AntigravityResolver.locate", lambda self: Path("/usr/bin/agy")
    )
    monkeypatch.setattr("qualock.cli.DockerRunner", _doctor_docker_should_not_be_constructed)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Linux" in result.stdout
    assert "Docker" not in result.stdout


def test_doctor_antigravity_fails_when_bwrap_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="antigravity"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr(
        "qualock.cli.shutil.which", lambda name: None if name == "bwrap" else f"/usr/bin/{name}"
    )
    monkeypatch.setattr("qualock.cli.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "qualock.cli.AntigravityResolver.locate", lambda self: Path("/usr/bin/agy")
    )
    monkeypatch.setattr("qualock.cli.DockerRunner", _doctor_docker_should_not_be_constructed)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "bwrap" in result.stdout
    assert "Docker" not in result.stdout


def test_doctor_antigravity_uses_shared_resolver_construction(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[None] = []

    class FakeResolver:
        def locate(self) -> Path:
            return Path("/usr/bin/agy")

    def fake_from_environment() -> FakeResolver:
        calls.append(None)
        return FakeResolver()

    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="antigravity"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "qualock.cli.AntigravityResolver.from_environment",
        staticmethod(fake_from_environment),
    )
    monkeypatch.setattr("qualock.cli.DockerRunner", _doctor_docker_should_not_be_constructed)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert calls == [None]


def test_doctor_antigravity_fails_when_binary_missing_or_rejected(tmp_path: Path, monkeypatch) -> None:
    from qualock.agents.antigravity_resolver import AntigravityResolveError

    def _reject(self) -> Path:
        raise AntigravityResolveError("no Antigravity binary found: pass binary_path or install agy on PATH")

    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="antigravity"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.platform.system", lambda: "Linux")
    monkeypatch.setattr("qualock.cli.AntigravityResolver.locate", _reject)
    monkeypatch.setattr("qualock.cli.DockerRunner", _doctor_docker_should_not_be_constructed)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Antigravity" in result.stdout
    assert "Docker" not in result.stdout


def test_doctor_gemini_is_offline_and_checks_node_key_docker_and_canaries(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeDockerRunner:
        def daemon_ready(self) -> bool:
            return True

    process_calls: list[tuple[tuple[str, ...], float]] = []

    def fake_run_process(argv, *, timeout_seconds, **kwargs):
        process_calls.append((tuple(argv), timeout_seconds))
        return ProcessResult(0, "v20.19.0\n", "", 0.01, False)

    def provider_call_forbidden(*args, **kwargs):
        raise AssertionError("Gemini doctor must not resolve or call a provider")

    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="gemini"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.DockerRunner", FakeDockerRunner)
    monkeypatch.setattr("qualock.cli.run_process", fake_run_process)
    monkeypatch.setattr("qualock.agents.gemini_resolver.GeminiResolver.resolve", provider_call_forbidden)
    monkeypatch.setattr(
        "qualock.agents.gemini_resolver.GeminiResolver.latest_version",
        provider_call_forbidden,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert process_calls == [(('node', '--version'), 10)]
    assert "Git" in result.stdout
    assert "npm" in result.stdout
    assert "Docker" in result.stdout
    assert "Node >=20" in result.stdout
    assert "Gemini key" in result.stdout
    assert "Canaries" in result.stdout


@pytest.mark.parametrize(
    ("node_result", "api_key", "failed_check"),
    [
        (ProcessResult(0, "v19.9.0\n", "", 0.01, False), "key", "Node >=20"),
        (ProcessResult(0, "v20.0.0\n", "", 0.01, False), "", "Gemini key"),
    ],
)
def test_doctor_gemini_fails_closed_for_old_node_or_empty_key(
    tmp_path: Path,
    monkeypatch,
    node_result: ProcessResult,
    api_key: str,
    failed_check: str,
) -> None:
    class FakeDockerRunner:
        def daemon_ready(self) -> bool:
            return True

    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(name="gemini"))
    monkeypatch.setattr("qualock.cli.load_project", lambda root: (config, [object()]))
    monkeypatch.setattr("qualock.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("qualock.cli.DockerRunner", FakeDockerRunner)
    monkeypatch.setattr("qualock.cli.run_process", lambda *args, **kwargs: node_result)
    monkeypatch.setenv("GEMINI_API_KEY", api_key)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert failed_check in result.stdout
    assert f"{failed_check:<10} FAIL" in result.stdout


def test_check_easy_renders_workflow_name_literally(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())
    monkeypatch.setattr(
        "qualock.cli.load_project",
        lambda root: (object(), [SimpleNamespace(id="critical-bug", name="Django [async]")]),
    )

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 2
    assert "Django [async]" in result.stdout


def test_check_claude_easy_output_uses_claude_code_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "claude@2.1.260"])

    assert result.exit_code == 2
    assert "Claude Code 0.150.0 -> 0.151.0" in result.stdout
    assert "Keep using Claude Code 0.150.0" in result.stdout
    assert "Do not update to Claude Code 0.151.0" in result.stdout


def test_check_claude_technical_output_uses_claude_code_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "claude@2.1.260", "--technical"])

    assert result.exit_code == 2
    assert "Qualock qualification: Claude Code 0.150.0 -> 0.151.0" in result.stdout


def test_baseline_unknown_agent_display_fails_cleanly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_baseline",
        lambda root, agent: SimpleNamespace(
            agent=SimpleNamespace(name="future-agent"), version="1.0.0"
        ),
    )
    result = runner.invoke(app, ["baseline", "future-agent@1.0.0"])
    assert result.exit_code == 3
    assert "unsupported agent" in result.stdout


def test_baseline_claude_output_uses_claude_code_name(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_baseline",
        lambda root, agent: SimpleNamespace(
            agent=SimpleNamespace(name="claude", version="2.1.260")
        ),
    )

    result = runner.invoke(app, ["baseline", "claude@2.1.260"])

    assert result.exit_code == 0
    assert result.stdout == "Baseline pinned: Claude Code 2.1.260\n"


def test_baseline_antigravity_output_uses_antigravity_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_baseline",
        lambda root, agent: SimpleNamespace(
            agent=SimpleNamespace(name="antigravity", version="1.1.27")
        ),
    )

    result = runner.invoke(app, ["baseline", "antigravity@1.1.27"])

    assert result.exit_code == 0
    assert result.stdout == "Baseline pinned: Antigravity 1.1.27\n"


def test_check_antigravity_easy_output_uses_antigravity_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_check", lambda root, candidate: sample_result())

    result = runner.invoke(app, ["check", "antigravity@1.1.27"])

    assert result.exit_code == 2
    assert "Antigravity 0.150.0 -> 0.151.0" in result.stdout
    assert "Keep using Antigravity 0.150.0" in result.stdout
    assert "Do not update to Antigravity 0.151.0" in result.stdout


def test_check_max_attempts_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, int] = {}

    def fake_execute_check(root: Path, candidate: str, *, max_attempts: int):
        captured["max_attempts"] = max_attempts
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", "6"],
    )

    assert captured == {"max_attempts": 6}
    assert result.exit_code == 2


def test_check_without_budget_preserves_two_argument_execute_check_call(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[tuple[Path, str]] = []

    def fake_execute_check(root: Path, candidate: str):
        called.append((root, candidate))
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result.exit_code == 2
    assert called == [(tmp_path, "codex@0.151.0")]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_check_rejects_nonpositive_max_attempts_before_execution(
    tmp_path: Path, monkeypatch, bad: str
) -> None:
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_execute_check(*args, **kwargs):
        nonlocal called
        called = True
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", bad],
    )

    assert result.exit_code == 3
    assert "max attempts must be greater than zero" in result.stdout
    assert called is False


def test_budget_limited_incomplete_keeps_exit_4(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = sample_result()
    incomplete = source.__class__(
        qualification_id=source.qualification_id,
        baseline_version=source.baseline_version,
        candidate_version=source.candidate_version,
        verdict=Verdict.INCOMPLETE,
        executions=source.executions,
        reasons=("budget skipped a configured canary",),
        run_order=source.run_order,
    )
    monkeypatch.setattr(
        "qualock.cli.execute_check",
        lambda root, candidate, *, max_attempts: incomplete,
    )

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", "6"],
    )

    assert result.exit_code == 4
    assert "CHECK COULD NOT FINISH" in result.stdout


def test_check_max_tokens_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, int] = {}

    def fake_execute_check(root: Path, candidate: str, *, max_tokens: int):
        captured["max_tokens"] = max_tokens
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-tokens", "50000"],
    )

    assert captured == {"max_tokens": 50000}
    assert result.exit_code == 2


def test_check_both_budgets_are_forwarded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, int] = {}

    def fake_execute_check(root: Path, candidate: str, *, max_attempts: int, max_tokens: int):
        captured["max_attempts"] = max_attempts
        captured["max_tokens"] = max_tokens
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-attempts", "6", "--max-tokens", "50000"],
    )

    assert captured == {"max_attempts": 6, "max_tokens": 50000}
    assert result.exit_code == 2


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_check_rejects_nonpositive_max_tokens_before_execution(
    tmp_path: Path, monkeypatch, bad: str
) -> None:
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_execute_check(*args, **kwargs):
        nonlocal called
        called = True
        return sample_result()

    monkeypatch.setattr("qualock.cli.execute_check", fake_execute_check)

    result = runner.invoke(
        app,
        ["check", "codex@0.151.0", "--max-tokens", bad],
    )

    assert result.exit_code == 3
    assert "max tokens must be greater than zero" in result.stdout
    assert called is False


def test_check_max_tokens_help_describes_threshold_not_hard_cap() -> None:
    result = runner.invoke(app, ["check", "--help"])
    stdout = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "--max-tokens" in stdout
    assert "threshold" in stdout.lower()
    assert "between complete canaries" in stdout.lower()
    assert "not a hard cap" in stdout.lower()
    assert "billing limit" in stdout.lower()


def test_check_pricing_writer_failure_preserves_cli_output_and_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualock.commands as commands_module
    from tests.unit.test_commands import FakeBackend, FakeResolver, setup_project

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    setup_project(root_a)
    setup_project(root_b)

    monkeypatch.setattr(commands_module, "_default_resolver", lambda agent_name: FakeResolver())
    monkeypatch.setattr(
        commands_module,
        "_default_backend",
        lambda root, config, agent_name: FakeBackend(),
    )
    monkeypatch.setattr(commands_module, "_qualification_id", lambda prefix: "cli-pricing-fixed")

    for root in (root_a, root_b):
        commands_module.execute_baseline(
            root,
            "codex@0.150.0",
            resolver=FakeResolver(),
            backend=FakeBackend(),
            qualification_id="baseline-cli-pricing",
            created_at="2026-09-08T00:00:00Z",
        )

    monkeypatch.chdir(root_a)
    result_a = runner.invoke(app, ["check", "codex@0.151.0"])

    def fail_write(_directory: Path, _payload: dict[str, object]) -> Path:
        raise OSError("sensitive writer detail")

    monkeypatch.setattr(commands_module, "write_pricing_sidecar", fail_write)
    monkeypatch.chdir(root_b)
    result_b = runner.invoke(app, ["check", "codex@0.151.0"])

    assert result_a.exit_code == result_b.exit_code
    assert result_a.stdout == result_b.stdout
    assert (root_a / ".qualock/results/cli-pricing-fixed/pricing.json").is_file()
    assert not (root_b / ".qualock/results/cli-pricing-fixed/pricing.json").exists()


def test_monitor_checked_output_has_no_usage_line(tmp_path: Path, monkeypatch) -> None:
    from qualock.release_monitor.models import MonitorAction, MonitorOutcome

    monkeypatch.chdir(tmp_path)
    outcome = MonitorOutcome(
        action=MonitorAction.CHECKED,
        agent_name="codex",
        baseline_version="0.150.0",
        latest_version="0.151.0",
        qualification_result=sample_result(),
    )
    monkeypatch.setattr("qualock.cli.execute_monitor", lambda root, **kwargs: outcome)

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 2
    assert "QuaLock Safety Check" in result.stdout
    assert "Observed model tokens" not in result.stdout
    assert "Technical evidence: .qualock/results/q1/\n" in result.stdout


def _write_valid_history_project(root: Path) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True)
    config_path = ub / "config.yaml"
    write_default_config(config_path)
    grader = ub / "canaries/grader.patch"
    grader.write_text("patch", encoding="utf-8")
    (ub / "canaries/sample.yaml").write_text(
        f"""schema_version: 1
id: sample
name: Sample
repository:
  url: https://example.invalid/repo.git
  base_sha: {"a" * 40}
runtime:
  image: python:3.12-slim
task: Fix it.
setup: []
agent:
  timeout_seconds: 60
grader:
  patch: grader.patch
  command:
    - pytest -q
constraints:
  protected_paths:
    - tests/**
critical: true
""",
        encoding="utf-8",
    )


def test_history_zero_history_exits_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    analysis = HistoryAnalysis(
        loaded_reports=0,
        ignored_reports=(),
        ranked=(),
        not_enough_history=(CanaryEffectiveness("sample", 0, 0, None),),
        per_canary_estimates=(CanaryEstimate("sample", (), (), None, None),),
        suite_estimate=SuiteEstimate(None, None, ("sample",), ("sample",)),
    )
    monkeypatch.setattr("qualock.cli.execute_history", lambda root: analysis)
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "No qualification history found yet" in result.stdout
    assert "Estimated model-attempt runtime" in result.stdout


@pytest.mark.parametrize(
    "exc",
    [
        ConfigError("bad config"),
        CanaryLoadError("bad canary"),
        CommandError("no canaries found"),
        ValueError("bad input"),
    ],
)
def test_history_configuration_failures_exit_3(tmp_path: Path, monkeypatch, exc: Exception) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_history", lambda root: (_ for _ in ()).throw(exc))
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 3
    assert str(exc) in result.stdout


def test_history_unexpected_error_exits_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_history",
        lambda root: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 1
    assert "boom" in result.stdout


def test_history_rejects_extra_arguments() -> None:
    result = runner.invoke(app, ["history", "extra"])
    assert result.exit_code != 0


def test_history_real_cold_start_does_not_create_results_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_history_project(tmp_path)
    results = tmp_path / ".qualock/results"
    assert not results.exists()

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "No qualification history found yet" in result.stdout
    assert not results.exists()


def test_history_real_invocation_preserves_artifact_bytes_and_mtimes(
    tmp_path: Path, monkeypatch
) -> None:
    from qualock.evidence.storage import write_qualification_artifacts

    monkeypatch.chdir(tmp_path)
    _write_valid_history_project(tmp_path)
    results = tmp_path / ".qualock/results"
    write_qualification_artifacts(
        results, sample_result(), agent_display_name="Codex"
    )

    before = {
        p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in results.rglob("*")
        if p.is_file()
    }
    result = runner.invoke(app, ["history"])
    after = {
        p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in results.rglob("*")
        if p.is_file()
    }

    assert result.exit_code == 0
    assert after == before


def _cost_analysis(
    *,
    selected_canonical_model: str | None = None,
    selected_rate_card_id: str | None = None,
    suite: SuiteCostEstimate | None = None,
    per_canary: tuple = (),
    selected_cohort_runs: int = 0,
    priceable_qualification_runs: int = 0,
    older_unpinned_runs: int = 0,
    unavailable_pricing_runs: int = 0,
    excluded_config_runs: int = 0,
    excluded_cohort_runs: int = 0,
) -> CostAnalysis:
    return CostAnalysis(
        current_agent="codex",
        configured_model="gpt-5.6-terra",
        reasoning_effort="high",
        selected_canonical_model=selected_canonical_model,
        selected_rate_card_id=selected_rate_card_id,
        per_canary=per_canary,
        suite=suite if suite is not None else SuiteCostEstimate(None, None, ()),
        selected_cohort_runs=selected_cohort_runs,
        priceable_qualification_runs=priceable_qualification_runs,
        older_unpinned_runs=older_unpinned_runs,
        unavailable_pricing_runs=unavailable_pricing_runs,
        excluded_config_runs=excluded_config_runs,
        excluded_cohort_runs=excluded_cohort_runs,
        pricing_failures=(),
        limitations=(),
    )


def test_cost_zero_history_exits_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    analysis = _cost_analysis()
    monkeypatch.setattr("qualock.cli.execute_cost", lambda root: analysis)
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 0
    assert "Reference cost unavailable." in result.stdout


def test_cost_no_matching_cohort_exits_zero_with_guidance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    analysis = _cost_analysis(older_unpinned_runs=5)
    monkeypatch.setattr("qualock.cli.execute_cost", lambda root: analysis)
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 0
    assert "Reference cost unavailable." in result.stdout
    assert "Run a normal qualification after pricing provenance is available" in result.stdout


def test_cost_unavailable_sidecars_exit_zero_with_truthful_guidance(
    tmp_path: Path, monkeypatch
) -> None:
    from qualock.pricing.models import CanaryCostEstimate

    monkeypatch.chdir(tmp_path)
    per_canary = (CanaryCostEstimate("sample", (), None, None),)
    analysis = _cost_analysis(
        selected_canonical_model="gpt-5.6-sol",
        selected_rate_card_id="openai:gpt-5.6-sol:standard:2026-09-07",
        suite=SuiteCostEstimate(None, None, ("sample",)),
        per_canary=per_canary,
        selected_cohort_runs=1,
    )
    monkeypatch.setattr("qualock.cli.execute_cost", lambda root: analysis)
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 0
    assert "No trustworthy monetary samples are available" in result.stdout
    assert "cohort." in result.stdout


def test_cost_unavailable_gemini_history_exits_zero_without_writes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    analysis = CostAnalysis(
        current_agent="gemini",
        configured_model="gemini-3.8-flash",
        reasoning_effort="provider-default",
        selected_canonical_model=None,
        selected_rate_card_id=None,
        per_canary=(),
        suite=SuiteCostEstimate(None, None, ()),
        selected_cohort_runs=0,
        priceable_qualification_runs=0,
        older_unpinned_runs=0,
        unavailable_pricing_runs=1,
        excluded_config_runs=0,
        excluded_cohort_runs=0,
        pricing_failures=(),
        limitations=(),
    )
    monkeypatch.setattr("qualock.cli.execute_cost", lambda root: analysis)
    before = tuple(tmp_path.rglob("*"))

    result = runner.invoke(app, ["cost"])

    assert result.exit_code == 0
    assert "Reference cost unavailable." in result.stdout
    assert tuple(tmp_path.rglob("*")) == before


def test_cost_empty_suite_exits_3(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_cost",
        lambda root: (_ for _ in ()).throw(CommandError("no canaries found")),
    )
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 3
    assert "no canaries found" in result.stdout


@pytest.mark.parametrize(
    "exc",
    [
        ConfigError("bad config"),
        CanaryLoadError("bad canary"),
        CommandError("no canaries found"),
        ValueError("bad input"),
    ],
)
def test_cost_config_and_canary_errors_exit_3_without_markup(
    tmp_path: Path, monkeypatch, exc: Exception
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("qualock.cli.execute_cost", lambda root: (_ for _ in ()).throw(exc))
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 3
    assert str(exc) in result.stdout


def test_cost_unexpected_error_exits_1_with_safe_fixed_message(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_cost",
        lambda root: (_ for _ in ()).throw(RuntimeError("/secret/path leaked traceback")),
    )
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 1
    assert result.stdout.strip() == "unable to analyze reference cost"
    assert "/secret/path" not in result.stdout
    assert "traceback" not in result.stdout.lower()


def test_cost_rejects_extra_arguments_and_pricing_flags() -> None:
    assert runner.invoke(app, ["cost", "extra"]).exit_code != 0
    assert runner.invoke(app, ["cost", "--max-cost", "5"]).exit_code != 0
    assert runner.invoke(app, ["cost", "--json"]).exit_code != 0
    assert runner.invoke(app, ["cost", "--budget", "5"]).exit_code != 0


def test_help_has_cost_but_no_max_cost_budget_json_or_pricing_options() -> None:
    top_level = runner.invoke(app, ["--help"])
    stdout = _strip_ansi(top_level.stdout)
    assert top_level.exit_code == 0
    assert "cost" in stdout

    cost_help = runner.invoke(app, ["cost", "--help"])
    cost_stdout = _strip_ansi(cost_help.stdout)
    assert cost_help.exit_code == 0
    assert "--max-cost" not in cost_stdout
    assert "--budget" not in cost_stdout
    assert "--json" not in cost_stdout
    assert "--pricing" not in cost_stdout


def test_cost_real_cold_start_does_not_create_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_history_project(tmp_path)
    results = tmp_path / ".qualock/results"
    assert not results.exists()

    result = runner.invoke(app, ["cost"])

    assert result.exit_code == 0
    assert "Reference cost unavailable." in result.stdout
    assert not results.exists()


def test_cost_real_invocation_preserves_all_artifact_bytes_and_mtimes(
    tmp_path: Path, monkeypatch
) -> None:
    from qualock.evidence.storage import write_qualification_artifacts

    monkeypatch.chdir(tmp_path)
    _write_valid_history_project(tmp_path)
    results = tmp_path / ".qualock/results"
    write_qualification_artifacts(results, sample_result(), agent_display_name="Codex")

    before = {
        p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in results.rglob("*")
        if p.is_file()
    }
    result = runner.invoke(app, ["cost"])
    after = {
        p.relative_to(results): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in results.rglob("*")
        if p.is_file()
    }

    assert result.exit_code == 0
    assert after == before


def test_cost_real_invocation_succeeds_with_network_blocked(tmp_path: Path, monkeypatch) -> None:
    import socket

    monkeypatch.chdir(tmp_path)
    _write_valid_history_project(tmp_path)

    def _blocked_connect(*args, **kwargs):
        raise OSError("network is blocked")

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    result = runner.invoke(app, ["cost"])

    assert result.exit_code == 0


def test_cost_sidecar_discovery_is_path_neutral_on_windows(tmp_path: Path, monkeypatch) -> None:
    nested = tmp_path / "project with spaces"
    nested.mkdir()
    monkeypatch.chdir(nested)
    _write_valid_history_project(nested)

    result = runner.invoke(app, ["cost"])

    assert result.exit_code == 0
    assert "Reference cost unavailable." in result.stdout
