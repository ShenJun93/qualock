from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qualock import cli
from qualock.github_pr.commands import PreparePrOutcome
from qualock.github_pr.models import (
    PrClassification,
    PrReportVerdict,
    PullRequestContext,
    PullRequestReport,
)
from qualock.github_pr.publisher import GitHubPublishError
from qualock.github_pr.setup import GitHubSetupConflictError, GitHubSetupOutcome
from qualock.github_pr.source import PrContextError

runner = CliRunner()

_REPO = "octo-org/octo-repo"
_SHA_A = "a" * 40
_SHA_B = "b" * 40


def _context(classification: PrClassification = PrClassification.UPGRADE) -> PullRequestContext:
    return PullRequestContext(
        repository_id=1,
        repository_full_name=_REPO,
        pr_number=7,
        pr_author_login="octocat",
        base_sha=_SHA_A,
        head_sha=_SHA_B,
        producer_run_id=42,
        changed_paths=(".qualock/baseline.lock",),
        classification=classification,
    )


def _report(context: PullRequestContext) -> PullRequestReport:
    return PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=context.classification,
        qualock_version="0.0.0",
        verdict=PrReportVerdict.PASS,
    )


# ---------------------------------------------------------------------------
# Step 1: setup visibility / command discovery
# ---------------------------------------------------------------------------


def test_github_setup_is_visible_but_plumbing_commands_are_hidden() -> None:
    result = runner.invoke(cli.app, ["github", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.stdout
    assert "prepare-pr" not in result.stdout
    assert "qualify-pr" not in result.stdout
    assert "report-pr" not in result.stdout


def test_github_subgroup_is_visible_at_top_level() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "github" in result.stdout


def test_github_setup_prints_paths_secret_name_and_status_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["github", "setup"])
    assert result.exit_code == 0
    assert str(tmp_path / ".github/workflows/qualock-pr.yml") in result.stdout
    assert str(tmp_path / ".github/workflows/qualock-pr-report.yml") in result.stdout
    assert "QUALOCK_CODEX_AUTH_B64" in result.stdout
    assert "qualock/pr" in result.stdout
    assert "commit" in result.stdout.lower()
    assert "secret" in result.stdout.lower()
    assert "branch protection" in result.stdout.lower()
    assert (
        "python -c \"import base64,pathlib; "
        "p=pathlib.Path.home()/'.codex/auth.json'; "
        "print(base64.b64encode(p.read_bytes()).decode())\""
    ) in result.stdout


def test_github_setup_conflict_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def _raise(root: Path) -> GitHubSetupOutcome:
        raise GitHubSetupConflictError("existing workflow file(s) differ from the QuaLock template")

    monkeypatch.setattr(cli, "install_github_workflows", _raise)
    result = runner.invoke(cli.app, ["github", "setup"])
    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# Step 2: prepare-pr hidden command wiring
# ---------------------------------------------------------------------------


def test_prepare_pr_requires_github_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(tmp_path / "event.json"),
            "--context-out",
            str(tmp_path / "context.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--proposed-lock-out",
            str(tmp_path / "lock.json"),
        ],
    )
    assert result.exit_code == 3


def test_prepare_pr_requires_run_id_and_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(tmp_path / "event.json"),
            "--context-out",
            str(tmp_path / "context.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--proposed-lock-out",
            str(tmp_path / "lock.json"),
        ],
    )
    assert result.exit_code == 3
    assert "secret-token-value" not in result.stdout


def test_prepare_pr_malformed_run_id_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_RUN_ID", "not-an-int")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(tmp_path / "event.json"),
            "--context-out",
            str(tmp_path / "context.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--proposed-lock-out",
            str(tmp_path / "lock.json"),
        ],
    )
    assert result.exit_code == 3


def test_prepare_pr_writes_context_and_proposed_lock_for_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.UPGRADE)
    captured: dict[str, Any] = {}

    class _FakeSource:
        def __init__(self, token: str) -> None:
            captured["token"] = token

    def _fake_prepare_pr(
        root: Path,
        event_path: Path,
        *,
        source: Any,
        producer_run_id: int,
        expected_repository: str,
    ) -> PreparePrOutcome:
        captured["producer_run_id"] = producer_run_id
        captured["expected_repository"] = expected_repository
        return PreparePrOutcome(context=context, proposed_lock=b"lock-bytes", terminal_report=None)

    monkeypatch.setattr(cli, "HttpxGitHubPrSource", _FakeSource)
    monkeypatch.setattr(cli, "prepare_pr", _fake_prepare_pr)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    context_out = tmp_path / "context.json"
    report_out = tmp_path / "report.json"
    lock_out = tmp_path / "lock.json"

    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(event),
            "--context-out",
            str(context_out),
            "--report-out",
            str(report_out),
            "--proposed-lock-out",
            str(lock_out),
        ],
    )

    assert result.exit_code == 0
    assert captured["token"] == "secret-token-value"
    assert captured["producer_run_id"] == 42
    assert captured["expected_repository"] == _REPO
    assert context_out.is_file()
    assert lock_out.read_bytes() == b"lock-bytes"
    assert not report_out.exists()
    assert result.stdout.strip() == "upgrade"
    assert "secret-token-value" not in result.stdout


def test_prepare_pr_writes_terminal_report_and_no_lock_for_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.NOT_APPLICABLE)
    report = _report(context)

    def _fake_prepare_pr(
        root: Path,
        event_path: Path,
        *,
        source: Any,
        producer_run_id: int,
        expected_repository: str,
    ) -> PreparePrOutcome:
        return PreparePrOutcome(context=context, proposed_lock=None, terminal_report=report)

    monkeypatch.setattr(cli, "HttpxGitHubPrSource", lambda token: object())
    monkeypatch.setattr(cli, "prepare_pr", _fake_prepare_pr)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    context_out = tmp_path / "context.json"
    report_out = tmp_path / "report.json"
    lock_out = tmp_path / "lock.json"

    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(event),
            "--context-out",
            str(context_out),
            "--report-out",
            str(report_out),
            "--proposed-lock-out",
            str(lock_out),
        ],
    )

    assert result.exit_code == 0
    assert context_out.is_file()
    assert report_out.is_file()
    assert not lock_out.exists()
    assert result.stdout.strip() == "not_applicable"


def test_prepare_pr_context_failure_exits_1_without_leaking_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    def _fake_prepare_pr(*args: Any, **kwargs: Any) -> PreparePrOutcome:
        raise PrContextError("cannot fetch pull request octo-org/octo-repo#7: 401 unauthorized")

    monkeypatch.setattr(cli, "HttpxGitHubPrSource", lambda token: object())
    monkeypatch.setattr(cli, "prepare_pr", _fake_prepare_pr)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "prepare-pr",
            "--event",
            str(event),
            "--context-out",
            str(tmp_path / "context.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--proposed-lock-out",
            str(tmp_path / "lock.json"),
        ],
    )

    assert result.exit_code == 1
    assert "secret-token-value" not in result.stdout
    assert "401 unauthorized" not in result.stdout


# ---------------------------------------------------------------------------
# Step 3a: qualify-pr hidden command wiring
# ---------------------------------------------------------------------------


def test_qualify_pr_rejects_invalid_credential_available_flag(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "github",
            "qualify-pr",
            "--context",
            str(tmp_path / "context.json"),
            "--proposed-lock",
            str(tmp_path / "lock.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--credential-available",
            "maybe",
        ],
    )
    assert result.exit_code == 3


@pytest.mark.parametrize(
    "verdict",
    [
        PrReportVerdict.PASS,
        PrReportVerdict.WARN,
        PrReportVerdict.BLOCK,
        PrReportVerdict.INCOMPLETE,
    ],
)
def test_qualify_pr_exits_0_for_every_terminal_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: PrReportVerdict
) -> None:
    monkeypatch.chdir(tmp_path)
    context = _context()
    context_path = tmp_path / "context.json"
    lock_path = tmp_path / "lock.json"
    report_out = tmp_path / "report.json"
    lock_path.write_bytes(b"{}")

    from qualock.github_pr.report import write_context

    write_context(context_path, context)

    report = PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=context.classification,
        qualock_version="0.0.0",
        verdict=verdict,
    )

    def _fake_qualify_prepared_pr(
        root: Path,
        ctx: PullRequestContext,
        proposed_lock: bytes,
        *,
        credential_available: bool,
    ) -> PullRequestReport:
        assert credential_available is True
        return report

    monkeypatch.setattr(cli, "qualify_prepared_pr", _fake_qualify_prepared_pr)

    result = runner.invoke(
        cli.app,
        [
            "github",
            "qualify-pr",
            "--context",
            str(context_path),
            "--proposed-lock",
            str(lock_path),
            "--report-out",
            str(report_out),
            "--credential-available",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert report_out.is_file()


def test_qualify_pr_artifact_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = runner.invoke(
        cli.app,
        [
            "github",
            "qualify-pr",
            "--context",
            str(tmp_path / "missing-context.json"),
            "--proposed-lock",
            str(tmp_path / "missing-lock.json"),
            "--report-out",
            str(tmp_path / "report.json"),
            "--credential-available",
            "true",
        ],
    )
    assert result.exit_code == 1
    assert not (tmp_path / "report.json").exists()


def test_qualify_pr_oversized_proposed_lock_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context()
    context_path = tmp_path / "context.json"
    from qualock.github_pr.report import write_context

    write_context(context_path, context)
    lock_path = tmp_path / "lock.json"
    lock_path.write_bytes(b"0" * 200_000)

    result = runner.invoke(
        cli.app,
        [
            "github",
            "qualify-pr",
            "--context",
            str(context_path),
            "--proposed-lock",
            str(lock_path),
            "--report-out",
            str(tmp_path / "report.json"),
            "--credential-available",
            "true",
        ],
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Step 3b: report-pr hidden command wiring
# ---------------------------------------------------------------------------


def test_report_pr_requires_github_token_and_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(tmp_path / "event.json"),
            "--context",
            str(tmp_path / "context.json"),
            "--report",
            str(tmp_path / "report.json"),
        ],
    )
    assert result.exit_code == 3


def test_report_pr_passes_none_for_missing_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.UPGRADE)
    context_path = tmp_path / "context.json"
    from qualock.github_pr.report import write_context

    write_context(context_path, context)

    captured: dict[str, Any] = {}

    def _fake_publish_pr_report(
        event_path: Path,
        context_model: PullRequestContext,
        report_model: PullRequestReport | None,
        *,
        publisher: Any,
        display_names: dict[str, str],
        expected_repository: str,
    ) -> None:
        captured["report_model"] = report_model
        captured["expected_repository"] = expected_repository

    monkeypatch.setattr(cli, "HttpxGitHubPublisher", lambda token: object())
    monkeypatch.setattr(cli, "publish_pr_report", _fake_publish_pr_report)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(event),
            "--context",
            str(context_path),
            "--report",
            str(tmp_path / "does-not-exist.json"),
        ],
    )

    assert result.exit_code == 0
    assert captured["report_model"] is None
    assert captured["expected_repository"] == _REPO


def test_report_pr_passes_none_for_malformed_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.UPGRADE)
    context_path = tmp_path / "context.json"
    from qualock.github_pr.report import write_context

    write_context(context_path, context)

    malformed_report = tmp_path / "report.json"
    malformed_report.write_text("not valid json", encoding="utf-8")

    captured: dict[str, Any] = {}

    def _fake_publish_pr_report(
        event_path: Path,
        context_model: PullRequestContext,
        report_model: PullRequestReport | None,
        *,
        publisher: Any,
        display_names: dict[str, str],
        expected_repository: str,
    ) -> None:
        captured["report_model"] = report_model
        captured["expected_repository"] = expected_repository

    monkeypatch.setattr(cli, "HttpxGitHubPublisher", lambda token: object())
    monkeypatch.setattr(cli, "publish_pr_report", _fake_publish_pr_report)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(event),
            "--context",
            str(context_path),
            "--report",
            str(malformed_report),
        ],
    )

    assert result.exit_code == 0
    assert captured["report_model"] is None
    assert captured["expected_repository"] == _REPO


def test_report_pr_malformed_context_still_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    malformed_context = tmp_path / "context.json"
    malformed_context.write_text("not valid json", encoding="utf-8")

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(event),
            "--context",
            str(malformed_context),
            "--report",
            str(tmp_path / "does-not-exist.json"),
        ],
    )

    assert result.exit_code == 1


def test_report_pr_publisher_failure_exits_1_without_leaking_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.UPGRADE)
    context_path = tmp_path / "context.json"
    from qualock.github_pr.report import write_context

    write_context(context_path, context)

    def _fake_publish_pr_report(*args: Any, **kwargs: Any) -> None:
        raise GitHubPublishError("GitHub API returned status 401 for /repos/octo-org/octo-repo")

    monkeypatch.setattr(cli, "HttpxGitHubPublisher", lambda token: object())
    monkeypatch.setattr(cli, "publish_pr_report", _fake_publish_pr_report)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(event),
            "--context",
            str(context_path),
            "--report",
            str(tmp_path / "does-not-exist.json"),
        ],
    )

    assert result.exit_code == 1
    assert "secret-token-value" not in result.stdout
    assert "401" not in result.stdout


def test_report_pr_uses_trusted_display_names_when_project_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.chdir(tmp_path)

    context = _context(PrClassification.UPGRADE)
    context_path = tmp_path / "context.json"
    from qualock.github_pr.report import write_context

    write_context(context_path, context)

    class _FakeCanary:
        id = "critical-bug"
        name = "Critical Bug Canary"

    monkeypatch.setattr(cli, "load_project", lambda root: (object(), [_FakeCanary()]))

    captured: dict[str, Any] = {}

    def _fake_publish_pr_report(
        event_path: Path,
        context_model: PullRequestContext,
        report_model: PullRequestReport | None,
        *,
        publisher: Any,
        display_names: dict[str, str],
        expected_repository: str,
    ) -> None:
        captured["display_names"] = display_names

    monkeypatch.setattr(cli, "HttpxGitHubPublisher", lambda token: object())
    monkeypatch.setattr(cli, "publish_pr_report", _fake_publish_pr_report)

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "github",
            "report-pr",
            "--event",
            str(event),
            "--context",
            str(context_path),
            "--report",
            str(tmp_path / "does-not-exist.json"),
        ],
    )

    assert result.exit_code == 0
    assert captured["display_names"] == {"critical-bug": "Critical Bug Canary"}
