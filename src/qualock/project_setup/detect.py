from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .models import ProjectCapabilities

KNOWN_NPM_SCRIPTS = ("test", "build", "lint", "typecheck")
PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
PYTHON_TARGETS = ("src", "tests", "app")


def _load_pyproject(root: Path) -> dict[str, object]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_package_json(root: Path) -> dict[str, object]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def detect_project(root: Path) -> ProjectCapabilities:
    pyproject = _load_pyproject(root)
    python = any((root / marker).is_file() for marker in PYTHON_MARKERS) or any(
        root.glob("requirements*.txt")
    )
    pytest = (
        (root / "tests").is_dir()
        or (root / "pytest.ini").is_file()
        or (root / "conftest.py").is_file()
        or isinstance(pyproject.get("tool"), dict)
        and isinstance(pyproject["tool"].get("pytest"), dict)
    )
    python_targets = tuple(name for name in PYTHON_TARGETS if (root / name).is_dir())
    if python and not python_targets:
        python_targets = (".",)

    package = _load_package_json(root)
    node = bool(package)
    scripts_raw = package.get("scripts", {}) if node else {}
    scripts = scripts_raw if isinstance(scripts_raw, dict) else {}
    npm_scripts = tuple(name for name in KNOWN_NPM_SCRIPTS if name in scripts)

    dependencies: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        values = package.get(field, {}) if node else {}
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
    )
