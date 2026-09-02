from pathlib import Path

from qualock.config.models import ProjectProtectionConfig
from qualock.project_setup.models import (
    ProjectCapabilities,
    PythonRunner,
    ReadinessStatus,
)
from qualock.project_setup.readiness import (
    PYTHON_PROBE_CODE,
    check_environment_readiness,
)
from qualock.run.process import ProcessResult


def protection(identifier: str, command: list[str]) -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id=identifier,
        name=identifier,
        command=command,
        timeout_seconds=30,
    )


def ok(stdout: str = "") -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        stdout=stdout,
        stderr="",
        elapsed_seconds=0.01,
        timed_out=False,
    )


def failed(stderr: str = "failed") -> ProcessResult:
    return ProcessResult(
        exit_code=1,
        stdout="",
        stderr=stderr,
        elapsed_seconds=0.01,
        timed_out=False,
    )


def test_uv_missing_tool_needs_setup_without_running_probe(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.UV,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: calls.append(list(argv)) or ok(),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["uv", "run"])],
    )

    assert readiness.status is ReadinessStatus.NEEDS_SETUP
    assert calls == []
    assert any(check.id == "uv-tool" for check in readiness.checks)


def test_uv_missing_environment_recommends_sync_without_running_uv(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.UV,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: calls.append(list(argv)) or ok(),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["uv", "run"])],
    )

    assert readiness.status is ReadinessStatus.NEEDS_SETUP
    assert calls == []
    failed_checks = [c for c in readiness.checks if c.status is ReadinessStatus.NEEDS_SETUP]
    assert failed_checks[-1].recommendation == "uv sync"


def test_uv_environment_override_and_probe_use_no_sync(tmp_path: Path, monkeypatch) -> None:
    env_dir = tmp_path / "custom-env"
    python = env_dir / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.UV,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: calls.append(list(argv)) or ok("/tmp/python\n"),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["uv", "run"])],
        env={"UV_PROJECT_ENVIRONMENT": "custom-env"},
    )

    assert readiness.status is ReadinessStatus.READY
    assert calls == [[
        "uv",
        "run",
        "--no-sync",
        "--",
        "python",
        "-c",
        PYTHON_PROBE_CODE,
    ]]


def test_uv_failing_probe_needs_setup(tmp_path: Path, monkeypatch) -> None:
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.UV,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: failed(),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["uv", "run"])],
    )

    assert readiness.status is ReadinessStatus.NEEDS_SETUP


def test_git_only_protection_does_not_probe_python_toolchain(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        python_runner=PythonRunner.UV,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: calls.append(name) or None,
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("git-diff-check", ["git", "diff", "HEAD", "--check"])],
    )

    assert readiness.status is ReadinessStatus.READY
    assert readiness.checks == ()
    assert calls == []


def test_python_protection_intent_without_runner_needs_setup(tmp_path: Path) -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        pytest=True,
        python_targets=("src",),
        python_runner=PythonRunner.NONE,
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("git-diff-check", ["git", "diff", "HEAD", "--check"])],
    )

    assert readiness.status is ReadinessStatus.NEEDS_SETUP
    assert any(check.id == "python-runner" for check in readiness.checks)


def test_poetry_ready_requires_existing_reported_executable(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / ".cache/pypoetry/virtualenvs/demo/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.POETRY,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/poetry" if name == "poetry" else None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: calls.append(list(argv)) or ok(str(executable) + "\n"),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["poetry", "run"])],
    )

    assert readiness.status is ReadinessStatus.READY
    assert calls == [["poetry", "env", "info", "--executable"]]


def test_poetry_missing_environment_recommends_install(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.POETRY,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: "/usr/bin/poetry" if name == "poetry" else None,
    )
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: failed(),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", ["poetry", "run"])],
    )

    failed_checks = [c for c in readiness.checks if c.status is ReadinessStatus.NEEDS_SETUP]
    assert readiness.status is ReadinessStatus.NEEDS_SETUP
    assert failed_checks[-1].recommendation == "poetry install"


def test_local_venv_probe_uses_only_fixed_python_code(tmp_path: Path, monkeypatch) -> None:
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.VENV,
        python_environment=".venv",
        python_executable=".venv/bin/python",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.run_process",
        lambda argv, **kwargs: calls.append(list(argv)) or ok(),
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("pytest", [".venv/bin/python", "-m", "pytest"])],
    )

    assert readiness.status is ReadinessStatus.READY
    assert calls == [[".venv/bin/python", "-c", PYTHON_PROBE_CODE]]


def test_npm_protection_requires_tools_and_node_modules(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(node=True, npm_scripts=("test",))
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"node", "npm"} else None,
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("npm-test", ["npm", "test"])],
    )

    assert readiness.status is ReadinessStatus.NEEDS_SETUP
    assert any(check.id == "node-dependencies" for check in readiness.checks)


def test_node_without_npm_protections_is_not_probed(tmp_path: Path, monkeypatch) -> None:
    capabilities = ProjectCapabilities(node=True)
    calls: list[str] = []
    monkeypatch.setattr(
        "qualock.project_setup.readiness.shutil.which",
        lambda name: calls.append(name) or None,
    )

    readiness = check_environment_readiness(
        tmp_path,
        capabilities,
        [protection("git-diff-check", ["git", "diff", "HEAD", "--check"])],
    )

    assert readiness.status is ReadinessStatus.READY
    assert calls == []
