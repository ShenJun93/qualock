import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qualock.cli import app
from qualock.project_setup.commands import SetupReadinessError, apply_setup_plan
from qualock.project_setup.models import (
    EnvironmentReadiness,
    ProjectCapabilities,
    ProtectionLevel,
    ReadinessStatus,
    SetupPlan,
)
from qualock.run.process import ProcessResult

runner = CliRunner()


def init_git_with_commit(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    (root / "README.md").write_text("healthy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)




def patch_ready_process(monkeypatch) -> None:
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: ProcessResult(
            exit_code=0,
            stdout="",
            stderr="",
            elapsed_seconds=0.01,
            timed_out=False,
        ),
    )

def patch_signing_key(root: Path, monkeypatch) -> Path:
    key_path = root.parent / f"{root.name}-setup-signing.key"
    monkeypatch.setattr(
        "qualock.project_protection.signing.default_signing_key_path",
        lambda: key_path,
    )
    return key_path


def test_setup_previews_python_recommendations_and_cancel_has_zero_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_with_commit(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['pytest>=8']\n", encoding="utf-8")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").write_text("", encoding="utf-8")
    (tmp_path / ".venv/pyvenv.cfg").write_text("home = test" + chr(10), encoding="utf-8")
    patch_ready_process(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup"], input="n\n")

    assert result.exit_code == 0
    assert "Detected: Python, pytest, venv, Git" in result.stdout
    assert "Tests still pass" in result.stdout
    assert "Python code still compiles" in result.stdout
    assert "Setup cancelled" in result.stdout
    assert not (tmp_path / ".qualock").exists()


def test_setup_yes_creates_config_and_signed_lock_for_generic_git_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_with_commit(tmp_path)
    key_path = patch_signing_key(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 0
    assert "Detected: Git" in result.stdout
    assert "PROTECTED" in result.stdout
    raw_config = yaml.safe_load((tmp_path / ".qualock/config.yaml").read_text(encoding="utf-8"))
    assert [item["id"] for item in raw_config["protections"]] == ["git-diff-check"]
    raw_lock = json.loads((tmp_path / ".qualock/project.lock").read_text(encoding="utf-8"))
    assert raw_lock["schema_version"] == 2
    assert key_path.is_file()


def test_setup_failing_baseline_keeps_config_and_invalidates_old_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_with_commit(tmp_path)
    patch_signing_key(tmp_path, monkeypatch)
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    (qdir / "project.lock").write_text("old signed lock", encoding="utf-8")
    (tmp_path / "README.md").write_text("trailing whitespace   \n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 4
    assert "NOT PROTECTED" in result.stdout
    assert "NEEDS SETUP" not in result.stdout
    assert (tmp_path / ".qualock/config.yaml").is_file()
    assert not (tmp_path / ".qualock/project.lock").exists()


def test_setup_strong_shows_available_node_lint_and_typecheck(tmp_path: Path, monkeypatch) -> None:
    init_git_with_commit(tmp_path)
    package = {
        "scripts": {
            "test": "echo test",
            "build": "echo build",
            "lint": "echo lint",
            "typecheck": "echo typecheck",
        },
        "dependencies": {"react": "19.0.0"},
        "devDependencies": {"vite": "7.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"node", "npm"} else None,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--level", "strong"], input="n\n")

    assert result.exit_code == 0
    assert "React" in result.stdout
    assert "Vite" in result.stdout
    assert "JavaScript lint still passes" in result.stdout
    assert "TypeScript type check still passes" in result.stdout
    assert not (tmp_path / ".qualock").exists()


def test_setup_unsupported_project_exits_3_without_mutation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 3
    assert "could not detect a supported project" in result.stdout.lower()
    assert not (tmp_path / ".qualock").exists()


def test_setup_python_project_without_git_exits_3_before_mutation(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 3
    assert "git repository" in result.stdout.lower()
    assert not (tmp_path / ".qualock").exists()


def test_setup_git_repository_without_commit_exits_3_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 3
    assert "committed head" in result.stdout.lower()
    assert not (tmp_path / ".qualock").exists()


def test_setup_unready_uv_environment_exits_4_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_with_commit(tmp_path)
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    pyproject = (
        "[project]" + chr(10)
        + "name='demo'" + chr(10)
        + "dependencies=['pytest>=8']" + chr(10)
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 4
    assert "NEEDS SETUP" in result.stdout
    assert "uv sync" in result.stdout
    assert "QuaLock did not change your project." in result.stdout
    assert not (tmp_path / ".qualock").exists()


def test_apply_setup_plan_rejects_unready_before_mutation(tmp_path: Path) -> None:
    plan = SetupPlan(
        capabilities=ProjectCapabilities(git=True),
        level=ProtectionLevel.RECOMMENDED,
        protections=(),
        readiness=EnvironmentReadiness(status=ReadinessStatus.NEEDS_SETUP),
    )

    with pytest.raises(SetupReadinessError, match="environment is not ready"):
        apply_setup_plan(tmp_path, plan)

    assert not (tmp_path / ".qualock").exists()
