import subprocess
from pathlib import Path

import pytest

from qualock.github_pr.setup import (
    GitHubSetupConflictError,
    GitHubSetupOutcome,
    GitHubSetupStatus,
    install_github_workflows,
)
from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW


def test_setup_creates_exactly_two_workflows(tmp_path: Path) -> None:
    outcome = install_github_workflows(tmp_path)
    assert outcome.status is GitHubSetupStatus.CREATED
    assert (tmp_path / ".github/workflows/qualock-pr.yml").read_text() == PRODUCER_WORKFLOW
    assert (tmp_path / ".github/workflows/qualock-pr-report.yml").read_text() == REPORTER_WORKFLOW


def test_setup_returns_outcome_paths(tmp_path: Path) -> None:
    outcome = install_github_workflows(tmp_path)
    assert isinstance(outcome, GitHubSetupOutcome)
    assert outcome.producer_path == tmp_path / ".github/workflows/qualock-pr.yml"
    assert outcome.reporter_path == tmp_path / ".github/workflows/qualock-pr-report.yml"


def test_setup_refuses_any_different_existing_file_without_partial_overwrite(
    tmp_path: Path,
) -> None:
    producer = tmp_path / ".github/workflows/qualock-pr.yml"
    reporter = tmp_path / ".github/workflows/qualock-pr-report.yml"
    producer.parent.mkdir(parents=True)
    producer.write_text("custom\n")
    reporter.write_text("custom reporter\n")
    with pytest.raises(GitHubSetupConflictError):
        install_github_workflows(tmp_path)
    assert producer.read_text() == "custom\n"
    assert reporter.read_text() == "custom reporter\n"


def test_setup_refuses_when_only_one_file_conflicts_without_writing_the_other(
    tmp_path: Path,
) -> None:
    producer = tmp_path / ".github/workflows/qualock-pr.yml"
    producer.parent.mkdir(parents=True)
    producer.write_text("custom\n")
    with pytest.raises(GitHubSetupConflictError):
        install_github_workflows(tmp_path)
    assert producer.read_text() == "custom\n"
    assert not (tmp_path / ".github/workflows/qualock-pr-report.yml").exists()


def test_setup_is_idempotent_when_files_already_match(tmp_path: Path) -> None:
    first = install_github_workflows(tmp_path)
    assert first.status is GitHubSetupStatus.CREATED
    second = install_github_workflows(tmp_path)
    assert second.status is GitHubSetupStatus.ALREADY_CONFIGURED


def test_setup_second_run_invokes_no_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_github_workflows(tmp_path)

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess must not be invoked")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    outcome = install_github_workflows(tmp_path)
    assert outcome.status is GitHubSetupStatus.ALREADY_CONFIGURED


def test_setup_touches_no_files_outside_the_two_approved_paths(tmp_path: Path) -> None:
    install_github_workflows(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    install_github_workflows(tmp_path)
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert before == after
    assert before == [
        ".github/workflows/qualock-pr-report.yml",
        ".github/workflows/qualock-pr.yml",
    ]
