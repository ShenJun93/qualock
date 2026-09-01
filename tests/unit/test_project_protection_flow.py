import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qualock.cli import app
from qualock.project_protection.commands import execute_protect, execute_verify
from qualock.project_protection.models import ProtectionStatus
from qualock.project_protection.signing import ProjectLockIntegrityError

runner = CliRunner()


def signing_key_path(root: Path) -> Path:
    return root.parent / f"{root.name}-project-protection.key"


def patch_default_signing_key(root: Path, monkeypatch) -> Path:
    key_path = signing_key_path(root)
    monkeypatch.setattr(
        "qualock.project_protection.signing.default_signing_key_path",
        lambda: key_path,
    )
    return key_path


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    (root / "README.md").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def write_config(root: Path, command: list[str], *, name: str = "App still works") -> None:
    qdir = root / ".qualock"
    qdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "protections": [
            {
                "id": "app-smoke",
                "name": name,
                "command": command,
                "timeout_seconds": 5,
            }
        ],
    }
    (qdir / "config.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def file_health_command() -> list[str]:
    code = "import pathlib,sys; sys.exit(0 if pathlib.Path('health.txt').read_text().strip() == 'ok' else 1)"
    return [sys.executable, "-c", code]


def test_execute_protect_writes_lock_and_evidence_only_when_healthy(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())

    result = execute_protect(tmp_path, operation_id="protect-test", created_at="2026-09-01T00:00:00Z", key_path=signing_key_path(tmp_path))

    assert result.status is ProtectionStatus.PASS
    assert (tmp_path / ".qualock/project.lock").is_file()
    assert (tmp_path / ".qualock/results/protect-test/report.json").is_file()


def test_execute_protect_refuses_unhealthy_baseline(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())

    result = execute_protect(tmp_path, operation_id="protect-bad", key_path=signing_key_path(tmp_path))

    assert result.status is ProtectionStatus.FAIL
    assert not (tmp_path / ".qualock/project.lock").exists()
    assert (tmp_path / ".qualock/results/protect-bad/report.json").is_file()


def test_verify_uses_locked_definition_and_blocks_regression(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Checkout still works")
    execute_protect(tmp_path, operation_id="protect-test", key_path=signing_key_path(tmp_path))

    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    write_config(tmp_path, [sys.executable, "-c", "raise SystemExit(0)"], name="Tampered config")

    result = execute_verify(tmp_path, operation_id="verify-test", created_at="2026-09-01T00:10:00Z", key_path=signing_key_path(tmp_path))

    assert result.status is ProtectionStatus.FAIL
    assert result.runs[0].name == "Checkout still works"
    assert (tmp_path / ".qualock/results/verify-test/report.json").is_file()


def test_cli_protect_and_verify_use_plain_language_and_exit_codes(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Login still works")
    patch_default_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    protected = runner.invoke(app, ["protect"])
    assert protected.exit_code == 0
    assert "PROTECTED" in protected.stdout
    assert "Login still works" in protected.stdout

    safe = runner.invoke(app, ["verify"])
    assert safe.exit_code == 0
    assert "SAFE TO KEEP" in safe.stdout

    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    broken = runner.invoke(app, ["verify"])
    assert broken.exit_code == 2
    assert "DON'T KEEP THIS CHANGE" in broken.stdout
    assert "Login still works" in broken.stdout


def test_cli_protect_refuses_failing_baseline_with_exit_4(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Health check")
    patch_default_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["protect"])

    assert result.exit_code == 4
    assert "NOT PROTECTED" in result.stdout
    assert not (tmp_path / ".qualock/project.lock").exists()


def test_cli_verify_incomplete_when_locked_executable_disappears(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    executable = tmp_path / "health-check"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    write_config(tmp_path, [str(executable)], name="Executable health check")
    patch_default_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    protected = runner.invoke(app, ["protect"])
    assert protected.exit_code == 0

    executable.unlink()
    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 4
    assert "CHECK COULD NOT FINISH" in result.stdout
    assert "Executable health check" in result.stdout


def test_protect_creates_external_signing_key_only_for_healthy_baseline(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())
    key_path = signing_key_path(tmp_path)

    result = execute_protect(tmp_path, operation_id="protect-signed", key_path=key_path)

    assert result.status is ProtectionStatus.PASS
    assert key_path.is_file()
    assert tmp_path not in key_path.parents
    raw = json.loads((tmp_path / ".qualock/project.lock").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2


def test_verify_rejects_modified_lock_before_tampered_command_runs(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())
    key_path = signing_key_path(tmp_path)
    execute_protect(tmp_path, operation_id="protect-signed", key_path=key_path)

    marker = tmp_path / "tampered-command-ran"
    lock_path = tmp_path / ".qualock/project.lock"
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    raw["lock"]["protections"][0]["command"] = [
        sys.executable,
        "-c",
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')",
    ]
    lock_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProjectLockIntegrityError, match="signature"):
        execute_verify(tmp_path, operation_id="verify-tampered", key_path=key_path)
    assert not marker.exists()


def test_verify_rejects_missing_signing_key(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())
    key_path = signing_key_path(tmp_path)
    execute_protect(tmp_path, operation_id="protect-signed", key_path=key_path)
    key_path.unlink()

    with pytest.raises(ProjectLockIntegrityError, match="missing"):
        execute_verify(tmp_path, operation_id="verify-missing-key", key_path=key_path)


def test_cli_verify_reports_lock_tampering_as_incomplete(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Login still works")
    patch_default_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["protect"]).exit_code == 0

    lock_path = tmp_path / ".qualock/project.lock"
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    raw["lock"]["protections"][0]["name"] = "Tampered check"
    lock_path.write_text(json.dumps(raw), encoding="utf-8")

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 4
    assert "signature" in result.stdout.lower()


def test_cli_verify_without_project_lock_remains_invalid_input(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    write_config(tmp_path, file_health_command())
    patch_default_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 3
    assert "project" in result.stdout and ".lock" in result.stdout
