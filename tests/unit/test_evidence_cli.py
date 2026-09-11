"""Unit tests for the QuaLock public evidence CLI.

Tests the evidence_app Typer group registered as `qualock evidence`, covering:
- Exactly two subcommands: export and verify
- Help and option contracts
- Exit code mapping (0 for valid verify regardless of stored verdict, 3 for domain errors, 1 for unexpected)
- Literal-safe bounded rendering expectations
- Preservation of all existing unrelated CLI commands and top-level verify
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import qualock
from qualock.baseline.io import BaselineStaleError
from qualock.cli import app
from qualock.evidence.bundle_models import (
    BundleCompleteness,
    EvidenceBundleError,
    EvidenceBundleReason,
    EvidenceManifest,
    PublicBaselineLock,
    PublicCanaries,
    PublicModelPin,
    PublicQualification,
    PublicReport,
    RuntimeAgentIdentity,
    VerifiedEvidenceBundle,
)
from qualock.evidence.export import ExportedEvidenceBundle
from qualock.evidence.provenance import (
    EvidenceProvenance,
    EvidenceProvenanceError,
    ProvenanceModelPin,
)
from qualock.qualification.models import Verdict

runner = CliRunner()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _make_verified_bundle(
    qualification_id: str = "check-1",
    verdict: Verdict = Verdict.PASS,
    baseline_version: str = "0.150.0",
    candidate_version: str = "0.151.0",
    agent_name: str = "codex",
    manifest_sha256: str = "a" * 64,
) -> VerifiedEvidenceBundle:
    baseline_id = RuntimeAgentIdentity(
        name=agent_name,
        version=baseline_version,
        binary_sha256="b" * 64,
        support_sha256="s" * 64 if agent_name == "gemini" else None,
    )
    candidate_id = RuntimeAgentIdentity(
        name=agent_name,
        version=candidate_version,
        binary_sha256="c" * 64,
        support_sha256="s" * 64 if agent_name == "gemini" else None,
    )
    model = PublicModelPin(id="gpt-4o", snapshot="2024-05-13", reasoning_effort="medium")
    prov_model = ProvenanceModelPin(id="gpt-4o", snapshot="2024-05-13", reasoning_effort="medium")
    completeness = BundleCompleteness(
        attempts_expected=6,
        attempts_used=6,
        max_attempts=None,
        max_tokens=None,
        observed_tokens=0,
        all_canaries_complete=True,
    )
    manifest = EvidenceManifest(
        schema_version=1,
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC).isoformat(),
        run_qualock_version=qualock.__version__,
        exporter_qualock_version=qualock.__version__,
        qualification_id=qualification_id,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        verdict=verdict,
        baseline_identity=baseline_id,
        candidate_identity=candidate_id,
        model=model,
        baseline_lock_sha256="1" * 64,
        suite_sha256="2" * 64,
        config_sha256="3" * 64,
        run_order_sha256="4" * 64,
        completeness=completeness,
        canaries={},
        files={},
    )
    report = PublicReport(
        qualification_id=qualification_id,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
        completeness=completeness,
    )
    qualification = PublicQualification(
        qualification_id=qualification_id,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        verdict=verdict,
        run_order=(),
        completeness=completeness,
    )
    baseline_lock = PublicBaselineLock(
        schema_version=1,
        created_at="2026-09-01T00:00:00+00:00",
        qualock_version=qualock.__version__,
        agent=baseline_id.model_dump(mode="json"),
        model=model,
        suite_sha256="2" * 64,
        config_sha256="3" * 64,
        canaries={},
    )
    provenance = EvidenceProvenance(
        schema_version=1,
        qualification_id=qualification_id,
        run_qualock_version=qualock.__version__,
        baseline_lock_sha256="1" * 64,
        baseline_identity=baseline_id,
        candidate_identity=candidate_id,
        model=prov_model,
        repetitions=3,
        run_order_sha256="4" * 64,
        canaries=(),
    )
    canaries = PublicCanaries(schema_version=1, canaries=())
    return VerifiedEvidenceBundle(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        report=report,
        qualification=qualification,
        baseline_lock=baseline_lock,
        provenance=provenance,
        canaries=canaries,
    )


# --- Step 5.1: Help, discovery, and surface preservation -----------------------


def test_top_level_help_lists_evidence_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "evidence" in stdout


def test_top_level_help_preserves_all_existing_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    expected_commands = [
        "init",
        "doctor",
        "baseline",
        "check",
        "monitor",
        "bisect",
        "schedule",
        "setup",
        "protect",
        "verify",
        "watch",
        "start",
        "report",
        "history",
        "cost",
        "github",
    ]
    for cmd in expected_commands:
        assert cmd in stdout, f"expected existing command '{cmd}' in top-level help"


def test_top_level_verify_is_unrelated_to_evidence_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = []
    fake_result = SimpleNamespace(
        operation_id="op-1",
        status=SimpleNamespace(value="pass"),
    )

    def fake_project_verify(root: Path) -> SimpleNamespace:
        called.append(root)
        return fake_result

    monkeypatch.setattr("qualock.cli.execute_project_verify", fake_project_verify)
    monkeypatch.setattr(
        "qualock.cli.render_verify_terminal",
        lambda res, ep: "QuaLock Project Protection: PASS\n",
    )

    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0
    assert len(called) == 1
    assert "QuaLock Project Protection" in result.stdout


def test_evidence_help_exposes_exactly_export_and_verify() -> None:
    result = runner.invoke(app, ["evidence", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "export" in stdout
    assert "verify" in stdout

    forbidden = ["run", "mutate", "network", "publish", "delete", "import"]
    for word in forbidden:
        assert word not in stdout.split()


def test_evidence_app_and_command_functions_contract() -> None:
    from qualock import cli

    assert hasattr(cli, "evidence_app"), "cli module must expose evidence_app Typer instance"
    assert hasattr(cli, "evidence_export_command"), "cli must export evidence_export_command"
    assert hasattr(cli, "evidence_verify_command"), "cli must export evidence_verify_command"

    export_sig = inspect.signature(cli.evidence_export_command)
    assert "qualification_id" in export_sig.parameters
    assert "out" in export_sig.parameters
    assert export_sig.parameters["qualification_id"].annotation in (str, "str")
    assert export_sig.parameters["out"].annotation in (Path, "Path")

    verify_sig = inspect.signature(cli.evidence_verify_command)
    assert "bundle" in verify_sig.parameters
    assert verify_sig.parameters["bundle"].annotation in (Path, "Path")


def test_evidence_export_help_contract() -> None:
    result = runner.invoke(app, ["evidence", "export", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "QUALIFICATION_ID" in stdout
    assert "--out" in stdout


def test_evidence_verify_help_contract() -> None:
    result = runner.invoke(app, ["evidence", "verify", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "BUNDLE" in stdout or "DIRECTORY" in stdout


def test_evidence_no_subcommand_shows_help_or_error() -> None:
    result = runner.invoke(app, ["evidence"])
    stdout = _strip_ansi(result.stdout)
    assert "export" in stdout or "verify" in stdout or result.exit_code != 0


def test_evidence_rejects_unknown_subcommands() -> None:
    assert runner.invoke(app, ["evidence", "run"]).exit_code != 0
    assert runner.invoke(app, ["evidence", "mutate"]).exit_code != 0
    assert runner.invoke(app, ["evidence", "upload"]).exit_code != 0


# --- Step 5.1: Argument and option contracts -----------------------------------


def test_evidence_export_missing_required_out_option_fails() -> None:
    result = runner.invoke(app, ["evidence", "export", "check-1"])
    assert result.exit_code != 0
    output = _strip_ansi(result.output)
    assert "Missing option" in output or "--out" in output


def test_evidence_export_missing_qualification_id_fails(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["evidence", "export", "--out", str(out_dir)])
    assert result.exit_code != 0
    output = _strip_ansi(result.output)
    assert "Missing argument" in output or "QUALIFICATION_ID" in output


def test_evidence_export_rejects_extra_positional_arguments(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app, ["evidence", "export", "check-1", "extra-arg", "--out", str(out_dir)]
    )
    assert result.exit_code != 0


def test_evidence_verify_missing_bundle_argument_fails() -> None:
    result = runner.invoke(app, ["evidence", "verify"])
    assert result.exit_code != 0
    output = _strip_ansi(result.output)
    assert "Missing argument" in output or "BUNDLE" in output or "DIRECTORY" in output


def test_evidence_verify_rejects_extra_arguments(tmp_path: Path) -> None:
    result = runner.invoke(app, ["evidence", "verify", str(tmp_path), "extra"])
    assert result.exit_code != 0


# --- Step 5.1: Export execution, exit mapping, and safety ----------------------


def test_evidence_export_calls_export_evidence_bundle_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "exported-bundle"
    recorded_calls: list[tuple[Path, str, Path]] = []

    def fake_export(
        root: Path, qualification_id: str, destination: Path
    ) -> ExportedEvidenceBundle:
        recorded_calls.append((root, qualification_id, destination))
        return ExportedEvidenceBundle(
            path=destination,
            qualification_id=qualification_id,
            manifest_sha256="e" * 64,
        )

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fake_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-safe-1", "--out", str(out_dir)]
    )
    assert result.exit_code == 0
    assert len(recorded_calls) == 1
    assert recorded_calls[0][1] == "q-safe-1"
    assert recorded_calls[0][2] == out_dir
    stdout = _strip_ansi(result.stdout)
    assert "q-safe-1" in stdout
    assert "e" * 64 in stdout


def test_evidence_export_bundle_error_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"

    def fail_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "destination")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fail_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-safe-1", "--out", str(out_dir)]
    )
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "unsafe_path" in stdout or "destination" in stdout
    assert "Traceback" not in stdout


def test_evidence_export_missing_source_artifacts_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"

    def fail_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise FileNotFoundError("missing report.json in results")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fail_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-safe-1", "--out", str(out_dir)]
    )
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "Traceback" not in stdout


def test_evidence_export_provenance_failure_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"

    def fail_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise EvidenceProvenanceError("evidence provenance unavailable for legacy check")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fail_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-legacy", "--out", str(out_dir)]
    )
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "provenance unavailable" in stdout
    assert "Traceback" not in stdout


def test_evidence_export_stale_baseline_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"

    def fail_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise BaselineStaleError("canary suite modified since baseline pinned")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fail_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-stale", "--out", str(out_dir)]
    )
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "modified since baseline" in stdout
    assert "Traceback" not in stdout


def test_evidence_export_destination_conflict_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "conflict"

    def fail_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "destination")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fail_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-conflict", "--out", str(out_dir)]
    )
    assert result.exit_code == 3


def test_evidence_export_unexpected_failure_exits_1_without_raw_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"

    def crash_export(root: Path, qid: str, dest: Path) -> ExportedEvidenceBundle:
        raise RuntimeError("database exploded /secret/db/credentials.key")

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", crash_export)

    result = runner.invoke(
        app, ["evidence", "export", "q-crash", "--out", str(out_dir)]
    )
    assert result.exit_code == 1
    stdout = _strip_ansi(result.stdout)
    assert "credentials.key" not in stdout
    assert "Traceback" not in stdout


# --- Step 5.1: Verify execution, exit mapping, and verdicts ---------------------


def test_evidence_verify_calls_verify_evidence_bundle_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bdir = tmp_path / "bundle"
    recorded_calls: list[Path] = []
    bundle_obj = _make_verified_bundle(qualification_id="q-v0")

    def fake_verify(bundle: Path) -> VerifiedEvidenceBundle:
        recorded_calls.append(bundle)
        return bundle_obj

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fake_verify)

    result = runner.invoke(app, ["evidence", "verify", str(bdir)])
    assert result.exit_code == 0
    assert len(recorded_calls) == 1
    assert recorded_calls[0] == bdir
    stdout = _strip_ansi(result.stdout)
    assert "q-v0" in stdout


def test_evidence_verify_exits_0_for_pass_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_obj = _make_verified_bundle(qualification_id="q-pass", verdict=Verdict.PASS)
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: bundle_obj)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "PASS" in stdout
    assert "q-pass" in stdout


def test_evidence_verify_exits_0_for_block_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_obj = _make_verified_bundle(qualification_id="q-block", verdict=Verdict.BLOCK)
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: bundle_obj)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "BLOCK" in stdout
    assert "q-block" in stdout


def test_evidence_verify_exits_0_for_warn_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_obj = _make_verified_bundle(qualification_id="q-warn", verdict=Verdict.WARN)
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: bundle_obj)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "WARN" in stdout
    assert "q-warn" in stdout


def test_evidence_verify_exits_0_for_incomplete_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_obj = _make_verified_bundle(
        qualification_id="q-incomplete", verdict=Verdict.INCOMPLETE
    )
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: bundle_obj)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "INCOMPLETE" in stdout
    assert "q-incomplete" in stdout


def test_evidence_verify_bundle_error_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_verify(b: Path) -> VerifiedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.DIGEST_MISMATCH, "report.json")

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fail_verify)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "digest_mismatch" in stdout or "report.json" in stdout
    assert "Traceback" not in stdout


def test_evidence_verify_tampered_manifest_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_verify(b: Path) -> VerifiedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_MANIFEST, "manifest.json")

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fail_verify)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "malformed_manifest" in stdout
    assert "Traceback" not in stdout


def test_evidence_verify_verdict_mismatch_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_verify(b: Path) -> VerifiedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "report.json")

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fail_verify)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "verdict_mismatch" in stdout
    assert "Traceback" not in stdout


def test_evidence_verify_missing_directory_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_verify(b: Path) -> VerifiedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_PATH, "bundle")

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fail_verify)

    nonexistent = tmp_path / "does-not-exist"
    result = runner.invoke(app, ["evidence", "verify", str(nonexistent)])
    assert result.exit_code == 3
    stdout = _strip_ansi(result.stdout)
    assert "Traceback" not in stdout


def test_evidence_verify_unexpected_failure_exits_1_without_raw_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash_verify(b: Path) -> VerifiedEvidenceBundle:
        raise RuntimeError("verifier core dumped with secret_token_xyz")

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", crash_verify)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 1
    stdout = _strip_ansi(result.stdout)
    assert "secret_token_xyz" not in stdout
    assert "Traceback" not in stdout


# --- Step 5.1: Bounded literal-safe rendering expectations --------------------


def test_evidence_verify_success_output_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = _make_verified_bundle(
        qualification_id="q-contract-check",
        verdict=Verdict.PASS,
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        agent_name="codex",
        manifest_sha256="c" * 64,
    )
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: verified)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "q-contract-check" in stdout
    assert "codex" in stdout
    assert "0.150.0" in stdout
    assert "0.151.0" in stdout
    assert "PASS" in stdout
    assert "1" in stdout
    assert "c" * 64 in stdout


def test_evidence_verify_literal_safe_dynamic_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil_id = "q-[bold]dangerous[/bold]"
    verified = _make_verified_bundle(qualification_id=evil_id)
    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", lambda b: verified)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 0
    assert evil_id in result.stdout


def test_evidence_export_literal_safe_dynamic_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil_id = "q-[red]exploit[/red]"

    def fake_export(
        root: Path, qualification_id: str, destination: Path
    ) -> ExportedEvidenceBundle:
        return ExportedEvidenceBundle(
            path=destination,
            qualification_id=qualification_id,
            manifest_sha256="f" * 64,
        )

    monkeypatch.setattr("qualock.cli.export_evidence_bundle", fake_export)
    out_dir = tmp_path / "bundle-[blue]out[/blue]"

    result = runner.invoke(
        app, ["evidence", "export", evil_id, "--out", str(out_dir)]
    )
    assert result.exit_code == 0
    assert evil_id in result.stdout


def test_evidence_failure_output_bounded_and_literal_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil_label = "[yellow]corrupt_payload_label[/yellow]"

    def fail_verify(b: Path) -> VerifiedEvidenceBundle:
        raise EvidenceBundleError(EvidenceBundleReason.MALFORMED_PAYLOAD, evil_label)

    monkeypatch.setattr("qualock.cli.verify_evidence_bundle", fail_verify)

    result = runner.invoke(app, ["evidence", "verify", str(tmp_path)])
    assert result.exit_code == 3
    assert evil_label in result.stdout
