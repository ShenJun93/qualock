import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from qualock.cli import app
from qualock.qualification.models import Verdict
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
