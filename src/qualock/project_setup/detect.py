from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .models import ProjectCapabilities

KNOWN_NPM_SCRIPTS = ("test", "build", "lint", "typecheck")
PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
PYTHON_TARGETS = ("src", "tests", "app")
PYTHON_EXECUTABLES = (".venv/bin/python", ".venv/Scripts/python.exe", "venv/bin/python", "venv/Scripts/python.exe")


def _load_pyproject(root: Path) -> dict[str, object]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_package_json(root: Path) -> dict[str, object] | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def detect_project(root: Path) -> ProjectCapabilities:
    pyproject = _load_pyproject(root)
    python = any((root / marker).is_file() for marker in PYTHON_MARKERS) or any(
        root.glob("requirements*.txt")
    )
    tool_config = pyproject.get("tool")
    pytest_config = tool_config.get("pytest") if isinstance(tool_config, dict) else None
    explicit_pytest = (
        (root / "pytest.ini").is_file()
        or (root / "conftest.py").is_file()
        or isinstance(pytest_config, dict)
    )
    pytest = explicit_pytest or (python and (root / "tests").is_dir())

    python_targets = tuple(name for name in PYTHON_TARGETS if (root / name).is_dir())
    if python and not python_targets:
        package_dirs = tuple(
            child.name
            for child in sorted(root.iterdir(), key=lambda path: path.name)
            if child.is_dir()
            and not child.name.startswith(".")
            and (child / "__init__.py").is_file()
        )
        module_files = tuple(
            path.name
            for path in sorted(root.glob("*.py"), key=lambda path: path.name)
            if path.name != "setup.py"
        )
        python_targets = package_dirs + module_files

    python_executable = next(
        (candidate for candidate in PYTHON_EXECUTABLES if (root / candidate).is_file()),
        None,
    )

    package = _load_package_json(root)
    node = package is not None
    scripts_raw = package.get("scripts", {}) if package is not None else {}
    scripts = scripts_raw if isinstance(scripts_raw, dict) else {}
    npm_scripts = tuple(name for name in KNOWN_NPM_SCRIPTS if name in scripts)

    dependencies: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        values = package.get(field, {}) if package is not None else {}
        if isinstance(values, dict):
            dependencies.update(str(key) for key in values)

    return ProjectCapabilities(
        git=(root / ".git").exists(),
        python=python,
        pytest=pytest,
        node=node,
        react="react" in dependencies,
        vite="vite" in dependencies,
        npm_scripts=npm_scripts,
        python_targets=python_targets,
        python_executable=python_executable,
    )
