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

# Exact pre-Batch-#39 producer workflow text (normalized text SHA256
# 29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69), captured
# verbatim from git commit ad0a568 (merge-base with main). Legacy migration
# eligibility depends on byte-for-byte fidelity of this fixture; do not
# reformat, reflow, or otherwise "clean up" this string.
LEGACY_PRODUCER_WORKFLOW = r"""name: QuaLock PR Qualification

on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]

permissions:
  contents: read
  pull-requests: read

concurrency:
  group: qualock-pr-${{ github.repository }}-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  qualify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout trusted base
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
        with:
          python-version: "3.12"

      - name: Install QuaLock from trusted checkout
        run: python -m pip install .

      - name: Prepare PR context
        id: prepare
        run: |
          qualock github prepare-pr \
            --event "$GITHUB_EVENT_PATH" \
            --context-out "$RUNNER_TEMP/pr-context.json" \
            --report-out "$RUNNER_TEMP/pr-report.json" \
            --proposed-lock-out "$RUNNER_TEMP/proposed-baseline.lock"

      - name: Upload PR context artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: qualock-pr-context
          path: ${{ runner.temp }}/pr-context.json
          if-no-files-found: error

      - name: Read classification
        id: classification
        run: |
          classification=$(python -c "import json; print(json.load(open('$RUNNER_TEMP/pr-context.json'))['classification'])")
          echo "value=$classification" >> "$GITHUB_OUTPUT"

      - name: Materialize codex credential
        id: credential
        if: steps.classification.outputs.value == 'upgrade'
        env:
          QUALOCK_CODEX_AUTH_B64: ${{ secrets.QUALOCK_CODEX_AUTH_B64 }}
        run: |
          set +x
          install -d -m 700 "$HOME/.codex"
          if [ -n "$QUALOCK_CODEX_AUTH_B64" ]; then
            printf '%s' "$QUALOCK_CODEX_AUTH_B64" | base64 -d > "$HOME/.codex/auth.json"
            chmod 600 "$HOME/.codex/auth.json"
            echo "available=true" >> "$GITHUB_OUTPUT"
          else
            echo "available=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Qualify upgrade
        if: steps.classification.outputs.value == 'upgrade'
        env:
          QUALOCK_CREDENTIAL_AVAILABLE: ${{ steps.credential.outputs.available }}
        run: |
          qualock github qualify-pr \
            --context "$RUNNER_TEMP/pr-context.json" \
            --proposed-lock "$RUNNER_TEMP/proposed-baseline.lock" \
            --report-out "$RUNNER_TEMP/pr-report.json" \
            --credential-available "$QUALOCK_CREDENTIAL_AVAILABLE"

      - name: Clean up codex credential
        if: always()
        run: rm -f "$HOME/.codex/auth.json"

      - name: Upload PR report artifact
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: qualock-pr-report
          path: ${{ runner.temp }}/pr-report.json
          if-no-files-found: error
"""

PRODUCER_PATH = Path(".github/workflows/qualock-pr.yml")
REPORTER_PATH = Path(".github/workflows/qualock-pr-report.yml")


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


def test_setup_migrates_exact_legacy_producer_to_upgraded(tmp_path: Path) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    reporter_path = tmp_path / REPORTER_PATH
    producer_path.parent.mkdir(parents=True)
    producer_path.write_text(LEGACY_PRODUCER_WORKFLOW, encoding="utf-8")
    reporter_path.write_text(REPORTER_WORKFLOW, encoding="utf-8")

    outcome = install_github_workflows(tmp_path)

    assert outcome.status is GitHubSetupStatus.UPGRADED
    assert producer_path.read_text(encoding="utf-8") == PRODUCER_WORKFLOW
    assert reporter_path.read_text(encoding="utf-8") == REPORTER_WORKFLOW


def test_setup_refuses_legacy_producer_with_one_byte_modification(tmp_path: Path) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    producer_path.parent.mkdir(parents=True)
    tampered = LEGACY_PRODUCER_WORKFLOW[:-1] + " "
    assert tampered != LEGACY_PRODUCER_WORKFLOW
    producer_path.write_text(tampered, encoding="utf-8")

    with pytest.raises(GitHubSetupConflictError):
        install_github_workflows(tmp_path)

    assert producer_path.read_text(encoding="utf-8") == tampered


def test_setup_refuses_legacy_producer_with_conflicting_reporter_and_does_not_migrate(
    tmp_path: Path,
) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    reporter_path = tmp_path / REPORTER_PATH
    producer_path.parent.mkdir(parents=True)
    producer_path.write_text(LEGACY_PRODUCER_WORKFLOW, encoding="utf-8")
    reporter_path.write_text("custom reporter\n", encoding="utf-8")

    with pytest.raises(GitHubSetupConflictError):
        install_github_workflows(tmp_path)

    assert producer_path.read_text(encoding="utf-8") == LEGACY_PRODUCER_WORKFLOW
    assert reporter_path.read_text(encoding="utf-8") == "custom reporter\n"


def test_setup_migrates_legacy_producer_and_creates_missing_reporter(tmp_path: Path) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    reporter_path = tmp_path / REPORTER_PATH
    producer_path.parent.mkdir(parents=True)
    producer_path.write_text(LEGACY_PRODUCER_WORKFLOW, encoding="utf-8")

    outcome = install_github_workflows(tmp_path)

    assert outcome.status is GitHubSetupStatus.UPGRADED
    assert producer_path.read_text(encoding="utf-8") == PRODUCER_WORKFLOW
    assert reporter_path.read_text(encoding="utf-8") == REPORTER_WORKFLOW


def test_setup_creates_missing_producer_with_exact_current_reporter(tmp_path: Path) -> None:
    reporter_path = tmp_path / REPORTER_PATH
    reporter_path.parent.mkdir(parents=True)
    reporter_path.write_text(REPORTER_WORKFLOW, encoding="utf-8")

    outcome = install_github_workflows(tmp_path)

    assert outcome.status is GitHubSetupStatus.CREATED
    assert (tmp_path / PRODUCER_PATH).read_text(encoding="utf-8") == PRODUCER_WORKFLOW
    assert reporter_path.read_text(encoding="utf-8") == REPORTER_WORKFLOW


def test_setup_reports_already_configured_for_exact_current_producer_and_reporter(
    tmp_path: Path,
) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    reporter_path = tmp_path / REPORTER_PATH
    producer_path.parent.mkdir(parents=True)
    producer_path.write_text(PRODUCER_WORKFLOW, encoding="utf-8")
    reporter_path.write_text(REPORTER_WORKFLOW, encoding="utf-8")

    outcome = install_github_workflows(tmp_path)

    assert outcome.status is GitHubSetupStatus.ALREADY_CONFIGURED


def test_setup_second_run_after_legacy_migration_is_already_configured(tmp_path: Path) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    producer_path.parent.mkdir(parents=True)
    producer_path.write_text(LEGACY_PRODUCER_WORKFLOW, encoding="utf-8")

    first = install_github_workflows(tmp_path)
    assert first.status is GitHubSetupStatus.UPGRADED

    second = install_github_workflows(tmp_path)
    assert second.status is GitHubSetupStatus.ALREADY_CONFIGURED


def test_setup_treats_crlf_legacy_producer_as_eligible_via_read_text_normalization(
    tmp_path: Path,
) -> None:
    producer_path = tmp_path / PRODUCER_PATH
    producer_path.parent.mkdir(parents=True)
    crlf_legacy = LEGACY_PRODUCER_WORKFLOW.replace("\n", "\r\n")
    producer_path.write_bytes(crlf_legacy.encode("utf-8"))
    assert producer_path.read_text(encoding="utf-8") == LEGACY_PRODUCER_WORKFLOW

    outcome = install_github_workflows(tmp_path)

    assert outcome.status is GitHubSetupStatus.UPGRADED
    assert producer_path.read_text(encoding="utf-8") == PRODUCER_WORKFLOW
