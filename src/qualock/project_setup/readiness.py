from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from qualock.config.models import ProjectProtectionConfig
from qualock.run.process import ProcessResult, run_process

from .models import (
    EnvironmentReadiness,
    ProjectCapabilities,
    PythonRunner,
    ReadinessCheck,
    ReadinessStatus,
)

PYTHON_PROBE_CODE = "import sys; print(sys.executable)"


def _check(
    identifier: str,
    name: str,
    status: ReadinessStatus,
    *,
    detail: str | None = None,
    recommendation: str | None = None,
) -> ReadinessCheck:
    return ReadinessCheck(
        id=identifier,
        name=name,
        status=status,
        detail=detail,
        recommendation=recommendation,
    )


def _overall(checks: Sequence[ReadinessCheck]) -> EnvironmentReadiness:
    status = (
        ReadinessStatus.NEEDS_SETUP
        if any(check.status is ReadinessStatus.NEEDS_SETUP for check in checks)
        else ReadinessStatus.READY
    )
    return EnvironmentReadiness(status=status, checks=tuple(checks))


def _safe_run(argv: list[str], root: Path) -> ProcessResult | None:
    try:
        return run_process(argv, cwd=root, timeout_seconds=10)
    except OSError:
        return None


def _result_ready(result: ProcessResult | None) -> bool:
    return (
        result is not None
        and not result.timed_out
        and result.exit_code == 0
    )


def _requires_python(
    capabilities: ProjectCapabilities,
    protections: Sequence[ProjectProtectionConfig],
) -> bool:
    python_ids = {"pytest", "python-compile", "django-check"}
    if any(item.id in python_ids for item in protections):
        return True
    # A missing runner causes Python checks to be omitted from the proposed pack.
    # Preserve the user's protection intent instead of silently degrading to Git-only.
    return (
        capabilities.python_runner is PythonRunner.NONE
        and (
            capabilities.pytest
            or capabilities.django
            or (capabilities.python and bool(capabilities.python_targets))
        )
    )


def _requires_npm(protections: Sequence[ProjectProtectionConfig]) -> bool:
    return any(item.id.startswith("npm-") for item in protections)


def _environment_python(root: Path, environment: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return environment / relative


def _uv_readiness(
    root: Path,
    env: Mapping[str, str],
) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    if shutil.which("uv") is None:
        return [
            _check(
                "uv-tool",
                "uv is available",
                ReadinessStatus.NEEDS_SETUP,
                recommendation="Install uv, then run qualock setup again.",
            )
        ]
    checks.append(_check("uv-tool", "uv is available", ReadinessStatus.READY))

    configured = env.get("UV_PROJECT_ENVIRONMENT", ".venv")
    environment = Path(configured)
    if not environment.is_absolute():
        environment = root / environment
    python = _environment_python(root, environment)
    if not environment.is_dir() or not python.is_file():
        checks.append(
            _check(
                "python-environment",
                "project Python environment is ready",
                ReadinessStatus.NEEDS_SETUP,
                recommendation="uv sync",
            )
        )
        return checks

    result = _safe_run(
        [
            "uv",
            "run",
            "--no-sync",
            "--",
            "python",
            "-c",
            PYTHON_PROBE_CODE,
        ],
        root,
    )
    checks.append(
        _check(
            "python-environment",
            "project Python environment is ready",
            ReadinessStatus.READY if _result_ready(result) else ReadinessStatus.NEEDS_SETUP,
            recommendation=None if _result_ready(result) else "uv sync",
        )
    )
    return checks


def _poetry_readiness(root: Path) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    if shutil.which("poetry") is None:
        return [
            _check(
                "poetry-tool",
                "Poetry is available",
                ReadinessStatus.NEEDS_SETUP,
                recommendation="Install Poetry, then run qualock setup again.",
            )
        ]
    checks.append(_check("poetry-tool", "Poetry is available", ReadinessStatus.READY))

    result = _safe_run(["poetry", "env", "info", "--executable"], root)
    executable: Path | None = None
    if _result_ready(result) and result is not None and result.stdout.strip():
        executable = Path(result.stdout.strip())
        if not executable.is_absolute():
            executable = root / executable

    ready = executable is not None and executable.is_file()
    checks.append(
        _check(
            "python-environment",
            "project Python environment is ready",
            ReadinessStatus.READY if ready else ReadinessStatus.NEEDS_SETUP,
            recommendation=None if ready else "poetry install",
        )
    )
    return checks


def _venv_readiness(
    root: Path,
    capabilities: ProjectCapabilities,
) -> list[ReadinessCheck]:
    executable = capabilities.python_executable
    if not executable:
        return [
            _check(
                "python-runner",
                "project Python runner is available",
                ReadinessStatus.NEEDS_SETUP,
                recommendation="Create a project virtual environment, then run qualock setup again.",
            )
        ]

    path = root / executable
    if not path.is_file():
        return [
            _check(
                "python-environment",
                "project Python environment is ready",
                ReadinessStatus.NEEDS_SETUP,
                recommendation="Recreate the project virtual environment.",
            )
        ]

    result = _safe_run([executable, "-c", PYTHON_PROBE_CODE], root)
    ready = _result_ready(result)
    return [
        _check(
            "python-environment",
            "project Python environment is ready",
            ReadinessStatus.READY if ready else ReadinessStatus.NEEDS_SETUP,
            recommendation=None if ready else "Recreate the project virtual environment.",
        )
    ]


def _python_readiness(
    root: Path,
    capabilities: ProjectCapabilities,
    env: Mapping[str, str],
) -> list[ReadinessCheck]:
    if capabilities.python_runner is PythonRunner.UV:
        return _uv_readiness(root, env)
    if capabilities.python_runner is PythonRunner.POETRY:
        return _poetry_readiness(root)
    if capabilities.python_runner is PythonRunner.VENV:
        return _venv_readiness(root, capabilities)
    return [
        _check(
            "python-runner",
            "project Python runner is available",
            ReadinessStatus.NEEDS_SETUP,
            recommendation="Create a project virtual environment, then run qualock setup again.",
        )
    ]


def _node_readiness(root: Path) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    for tool in ("node", "npm"):
        available = shutil.which(tool) is not None
        checks.append(
            _check(
                f"{tool}-tool",
                f"{tool} is available",
                ReadinessStatus.READY if available else ReadinessStatus.NEEDS_SETUP,
                recommendation=None if available else f"Install {tool}, then run qualock setup again.",
            )
        )

    dependencies_ready = (root / "node_modules").is_dir()
    checks.append(
        _check(
            "node-dependencies",
            "Node dependencies are installed",
            ReadinessStatus.READY if dependencies_ready else ReadinessStatus.NEEDS_SETUP,
            recommendation=None
            if dependencies_ready
            else "Run the project's normal dependency install command.",
        )
    )
    return checks


def check_environment_readiness(
    root: Path,
    capabilities: ProjectCapabilities,
    protections: Sequence[ProjectProtectionConfig],
    *,
    env: Mapping[str, str] | None = None,
) -> EnvironmentReadiness:
    checks: list[ReadinessCheck] = []
    effective_env = os.environ if env is None else env

    if _requires_python(capabilities, protections):
        checks.extend(_python_readiness(root, capabilities, effective_env))
    if _requires_npm(protections):
        checks.extend(_node_readiness(root))

    return _overall(checks)
