"""End-to-end integration tests for reproducible regression evidence bundles.

Covers Task 5 Step 5.2:
- Standalone export -> copy -> delete source project -> verify workflow
- Exact byte and mtime preservation across verification
- Strict offline guards ensuring no network/subprocess/Docker invocations during verify
- One-byte tamper mutation variants for each required payload class and optional pricing
- Verification exit 0 for valid bundles across PASS, BLOCK, WARN, and INCOMPLETE verdicts
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from qualock.cli import app
from qualock.commands import execute_baseline, execute_check
from qualock.config.io import write_default_config
from qualock.evidence.bundle_models import (
    BASELINE_LOCK_FILENAME,
    CANARIES_FILENAME,
    MANIFEST_FILENAME,
    PRICING_FILENAME,
    PROVENANCE_FILENAME,
    QUALIFICATION_FILENAME,
    REPORT_FILENAME,
    REQUIRED_PAYLOAD_FILENAMES,
    EvidenceBundleError,
    VerifiedEvidenceBundle,
)
from qualock.evidence.verify import verify_evidence_bundle
from qualock.pricing.sidecar import write_pricing_sidecar
from qualock.qualification.models import Verdict
from tests.unit.evidence_bundle_fixtures import _pricing_payload
from tests.unit.test_evidence_export import FakeBackend, FakeResolver

runner = CliRunner()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _install_offline_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    def _violation(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline violation: network, process, or Docker called during verify")

    monkeypatch.setattr(socket.socket, "connect", _violation)
    monkeypatch.setattr(socket.socket, "bind", _violation)
    monkeypatch.setattr(subprocess, "run", _violation)
    monkeypatch.setattr(subprocess, "Popen", _violation)

    import qualock.run.docker as docker_module
    import qualock.run.process as process_module

    monkeypatch.setattr(process_module, "run_process", _violation)
    monkeypatch.setattr(docker_module.DockerRunner, "__init__", _violation)
    monkeypatch.setattr(docker_module.DockerRunner, "daemon_ready", _violation)


def _snapshot_bundle(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        entry.relative_to(root): (entry.read_bytes(), entry.stat().st_mtime_ns)
        for entry in sorted(root.rglob("*"))
        if entry.is_file()
    }


def _create_synthetic_check(
    root: Path,
    *,
    qualification_id: str = "check-e2e",
    critical: bool = True,
    success_versions: set[str] | None = None,
    max_attempts: int | None = None,
    include_pricing: bool = False,
) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True, exist_ok=True)
    (ub / "results").mkdir(parents=True, exist_ok=True)
    config_path = ub / "config.yaml"
    write_default_config(config_path)

    grader = ub / "canaries/grader.patch"
    grader.write_text("patch-content", encoding="utf-8")

    canary_data: dict[str, Any] = {
        "schema_version": 1,
        "id": "sample-canary",
        "name": "Sample Canary",
        "repository": {
            "url": "https://example.invalid/e2e-repo.git",
            "base_sha": "f" * 40,
        },
        "runtime": {"image": "python:3.12-slim"},
        "task": "Resolve regression.",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {
            "patch": "grader.patch",
            "command": ["pytest", "-q"],
        },
        "constraints": {
            "protected_paths": ["tests/**"],
        },
        "critical": critical,
    }
    (ub / "canaries/sample.yaml").write_text(
        yaml.safe_dump(canary_data, sort_keys=False), encoding="utf-8"
    )

    resolver = FakeResolver(agent_name="codex")
    backend = FakeBackend(
        success_versions=success_versions if success_versions is not None else {"0.150.0", "0.151.0"}
    )
    execute_baseline(
        root,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="base-e2e",
        created_at="2026-09-01T00:00:00Z",
    )
    execute_check(
        root,
        "codex@0.151.0",
        resolver=resolver,
        backend=backend,
        qualification_id=qualification_id,
        max_attempts=max_attempts,
    )

    if include_pricing:
        qdir = ub / f"results/{qualification_id}"
        report_data = __import__("json").loads((qdir / REPORT_FILENAME).read_text(encoding="utf-8"))
        pricing = _pricing_payload(qualification_id, report_data["executions"])
        (qdir / PRICING_FILENAME).unlink(missing_ok=True)
        write_pricing_sidecar(qdir, pricing)


# --- Step 5.2: Standalone export-copy-delete-project-verify E2E ----------------


def test_standalone_export_copy_delete_project_verify_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "source-project"
    qualification_id = "check-standalone-pass"
    _create_synthetic_check(project_root, qualification_id=qualification_id)

    export_dir = tmp_path / "exported-bundle"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0, f"export failed: {res_export.stdout}"
    assert export_dir.is_dir()
    for req_file in REQUIRED_PAYLOAD_FILENAMES + (MANIFEST_FILENAME,):
        assert (export_dir / req_file).is_file(), f"missing exported file {req_file}"

    # Copy bundle to a new directory
    standalone_dir = tmp_path / "standalone-copied-bundle"
    shutil.copytree(export_dir, standalone_dir)

    # Completely delete the source QuaLock project and export dir
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)
    assert not project_root.exists()
    assert not export_dir.exists()

    # Snapshot bundle bytes and mtimes before verify
    before_snapshot = _snapshot_bundle(standalone_dir)
    assert len(before_snapshot) >= 6

    # Install offline guards: any socket, subprocess, or Docker call must fail
    _install_offline_guards(monkeypatch)

    # Change current working directory to a separate non-project path
    monkeypatch.chdir(tmp_path)

    # Verify copied directory via CLI
    res_verify = runner.invoke(app, ["evidence", "verify", str(standalone_dir)])
    assert res_verify.exit_code == 0, f"verification failed: {res_verify.stdout}"
    stdout = _strip_ansi(res_verify.stdout)
    assert qualification_id in stdout
    assert "0.150.0" in stdout
    assert "0.151.0" in stdout
    assert "PASS" in stdout
    assert "1" in stdout  # schema version

    # Also verify via direct verifier API
    bundle = verify_evidence_bundle(standalone_dir)
    assert isinstance(bundle, VerifiedEvidenceBundle)
    assert bundle.manifest.qualification_id == qualification_id
    assert bundle.report.verdict is Verdict.PASS

    # Snapshot bundle bytes and mtimes after verify and require exact preservation
    after_snapshot = _snapshot_bundle(standalone_dir)
    assert after_snapshot == before_snapshot, "bundle bytes or mtimes were mutated during verify"


# --- Step 5.2: One-byte tamper variants for each required payload class --------


@pytest.mark.parametrize(
    "tamper_filename",
    [
        MANIFEST_FILENAME,
        REPORT_FILENAME,
        QUALIFICATION_FILENAME,
        BASELINE_LOCK_FILENAME,
        PROVENANCE_FILENAME,
        CANARIES_FILENAME,
    ],
)
def test_standalone_verify_fails_on_one_byte_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper_filename: str
) -> None:
    project_root = tmp_path / "source-project"
    qualification_id = "check-tamper"
    _create_synthetic_check(project_root, qualification_id=qualification_id)

    export_dir = tmp_path / "bundle-original"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0

    tampered_dir = tmp_path / f"bundle-tampered-{tamper_filename}"
    shutil.copytree(export_dir, tampered_dir)

    # Delete source project
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    # Tamper with exactly one byte in target file
    target_path = tampered_dir / tamper_filename
    assert target_path.is_file()
    content = bytearray(target_path.read_bytes())
    offset = min(20, len(content) - 1)
    content[offset] ^= 0x01
    target_path.write_bytes(bytes(content))

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    # CLI verification must exit with code 3
    res_verify = runner.invoke(app, ["evidence", "verify", str(tampered_dir)])
    assert res_verify.exit_code == 3, (
        f"expected exit 3 on tampered {tamper_filename}, got {res_verify.exit_code}: "
        f"{res_verify.stdout}"
    )
    stdout = _strip_ansi(res_verify.stdout)
    assert "Traceback" not in stdout

    # Direct verifier must raise EvidenceBundleError
    with pytest.raises(EvidenceBundleError):
        verify_evidence_bundle(tampered_dir)


def test_standalone_verify_fails_on_one_byte_tamper_pricing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "source-project"
    qualification_id = "check-tamper-pricing"
    _create_synthetic_check(
        project_root, qualification_id=qualification_id, include_pricing=True
    )

    export_dir = tmp_path / "bundle-original"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0
    assert (export_dir / PRICING_FILENAME).is_file()

    tampered_dir = tmp_path / "bundle-tampered-pricing"
    shutil.copytree(export_dir, tampered_dir)

    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    target_path = tampered_dir / PRICING_FILENAME
    content = bytearray(target_path.read_bytes())
    offset = min(20, len(content) - 1)
    content[offset] ^= 0x01
    target_path.write_bytes(bytes(content))

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    res_verify = runner.invoke(app, ["evidence", "verify", str(tampered_dir)])
    assert res_verify.exit_code == 3
    assert "Traceback" not in res_verify.stdout

    with pytest.raises(EvidenceBundleError):
        verify_evidence_bundle(tampered_dir)


# --- Step 5.2: Verification exit 0 across stored verdicts ----------------------


def test_standalone_e2e_block_verdict_verifies_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project-block"
    qualification_id = "check-e2e-block"
    # Candidate fails critical canary -> BLOCK
    _create_synthetic_check(
        project_root,
        qualification_id=qualification_id,
        critical=True,
        success_versions={"0.150.0"},
    )

    export_dir = tmp_path / "bundle-block"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0

    standalone_dir = tmp_path / "standalone-block"
    shutil.copytree(export_dir, standalone_dir)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_bundle(standalone_dir)
    res_verify = runner.invoke(app, ["evidence", "verify", str(standalone_dir)])
    assert res_verify.exit_code == 0, f"BLOCK verify failed: {res_verify.stdout}"
    stdout = _strip_ansi(res_verify.stdout)
    assert "BLOCK" in stdout
    assert qualification_id in stdout

    bundle = verify_evidence_bundle(standalone_dir)
    assert bundle.report.verdict is Verdict.BLOCK
    assert _snapshot_bundle(standalone_dir) == before


def test_standalone_e2e_warn_verdict_verifies_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project-warn"
    qualification_id = "check-e2e-warn"
    # Candidate fails non-critical canary -> WARN
    _create_synthetic_check(
        project_root,
        qualification_id=qualification_id,
        critical=False,
        success_versions={"0.150.0"},
    )

    export_dir = tmp_path / "bundle-warn"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0

    standalone_dir = tmp_path / "standalone-warn"
    shutil.copytree(export_dir, standalone_dir)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_bundle(standalone_dir)
    res_verify = runner.invoke(app, ["evidence", "verify", str(standalone_dir)])
    assert res_verify.exit_code == 0, f"WARN verify failed: {res_verify.stdout}"
    stdout = _strip_ansi(res_verify.stdout)
    assert "WARN" in stdout
    assert qualification_id in stdout

    bundle = verify_evidence_bundle(standalone_dir)
    assert bundle.report.verdict is Verdict.WARN
    assert _snapshot_bundle(standalone_dir) == before


def test_standalone_e2e_incomplete_verdict_verifies_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project-incomplete"
    qualification_id = "check-e2e-incomplete"
    # Attempt budget exceeded -> INCOMPLETE
    _create_synthetic_check(
        project_root,
        qualification_id=qualification_id,
        max_attempts=2,
    )

    export_dir = tmp_path / "bundle-incomplete"
    monkeypatch.chdir(project_root)

    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0

    standalone_dir = tmp_path / "standalone-incomplete"
    shutil.copytree(export_dir, standalone_dir)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_bundle(standalone_dir)
    res_verify = runner.invoke(app, ["evidence", "verify", str(standalone_dir)])
    assert res_verify.exit_code == 0, f"INCOMPLETE verify failed: {res_verify.stdout}"
    stdout = _strip_ansi(res_verify.stdout)
    assert "INCOMPLETE" in stdout
    assert qualification_id in stdout

    bundle = verify_evidence_bundle(standalone_dir)
    assert bundle.report.verdict is Verdict.INCOMPLETE
    assert _snapshot_bundle(standalone_dir) == before


# --- Step 5.2: Multi-run byte preservation and guard integrity -----------------


def test_standalone_verify_preserves_bytes_across_repeated_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "source-project"
    qualification_id = "check-repeated"
    _create_synthetic_check(project_root, qualification_id=qualification_id)

    export_dir = tmp_path / "bundle"
    monkeypatch.chdir(project_root)
    res_export = runner.invoke(
        app, ["evidence", "export", qualification_id, "--out", str(export_dir)]
    )
    assert res_export.exit_code == 0

    standalone_dir = tmp_path / "bundle-repeated"
    shutil.copytree(export_dir, standalone_dir)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(project_root)
    shutil.rmtree(export_dir)

    _install_offline_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    initial_snapshot = _snapshot_bundle(standalone_dir)

    for i in range(3):
        res = runner.invoke(app, ["evidence", "verify", str(standalone_dir)])
        assert res.exit_code == 0, f"run {i} failed: {res.stdout}"
        current_snapshot = _snapshot_bundle(standalone_dir)
        assert current_snapshot == initial_snapshot, f"mutation detected on invocation {i}"


def test_offline_guard_enforces_failure_on_socket_or_process_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_offline_guards(monkeypatch)

    with pytest.raises(AssertionError, match="offline violation"):
        socket.socket().connect(("127.0.0.1", 80))

    with pytest.raises(AssertionError, match="offline violation"):
        subprocess.run(["echo", "hello"], check=False)
