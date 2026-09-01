import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from qualock.cli import app
from qualock.project_protection.commands import execute_protect, execute_verify
from qualock.project_protection.models import ProtectionStatus

runner = CliRunner()


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

    result = execute_protect(tmp_path, operation_id="protect-test", created_at="2026-09-01T00:00:00Z")

    assert result.status is ProtectionStatus.PASS
    assert (tmp_path / ".qualock/project.lock").is_file()
    assert (tmp_path / ".qualock/results/protect-test/report.json").is_file()


def test_execute_protect_refuses_unhealthy_baseline(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    write_config(tmp_path, file_health_command())

    result = execute_protect(tmp_path, operation_id="protect-bad")

    assert result.status is ProtectionStatus.FAIL
    assert not (tmp_path / ".qualock/project.lock").exists()
    assert (tmp_path / ".qualock/results/protect-bad/report.json").is_file()


def test_verify_uses_locked_definition_and_blocks_regression(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Checkout still works")
    execute_protect(tmp_path, operation_id="protect-test")

    (tmp_path / "health.txt").write_text("broken\n", encoding="utf-8")
    write_config(tmp_path, [sys.executable, "-c", "raise SystemExit(0)"], name="Tampered config")

    result = execute_verify(tmp_path, operation_id="verify-test", created_at="2026-09-01T00:10:00Z")

    assert result.status is ProtectionStatus.FAIL
    assert result.runs[0].name == "Checkout still works"
    assert (tmp_path / ".qualock/results/verify-test/report.json").is_file()


def test_cli_protect_and_verify_use_plain_language_and_exit_codes(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "health.txt").write_text("ok\n", encoding="utf-8")
    write_config(tmp_path, file_health_command(), name="Login still works")
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
    monkeypatch.chdir(tmp_path)
    protected = runner.invoke(app, ["protect"])
    assert protected.exit_code == 0

    executable.unlink()
    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 4
    assert "CHECK COULD NOT FINISH" in result.stdout
    assert "Executable health check" in result.stdout
