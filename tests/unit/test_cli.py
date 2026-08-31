from pathlib import Path

from typer.testing import CliRunner

from qualock.cli import app
from qualock.qualification.models import Verdict
from tests.unit.test_report import sample_result


runner = CliRunner()


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
    assert "BLOCK" in result.stdout


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
