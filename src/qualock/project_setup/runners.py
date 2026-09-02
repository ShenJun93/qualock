from __future__ import annotations

from .models import ProjectCapabilities, PythonRunner


def python_command(capabilities: ProjectCapabilities, *args: str) -> list[str]:
    if capabilities.python_runner is PythonRunner.UV:
        return ["uv", "run", "--no-sync", "--", "python", *args]
    if capabilities.python_runner is PythonRunner.POETRY:
        return ["poetry", "run", "python", *args]
    if capabilities.python_runner is PythonRunner.VENV:
        if not capabilities.python_executable:
            raise ValueError("project Python runner is not available")
        return [capabilities.python_executable, *args]
    raise ValueError("project Python runner is not available")
