from __future__ import annotations

import configparser
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
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_package_json(root: Path) -> dict[str, object] | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _dependency_string_is_pytest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized == "pytest" or normalized.startswith((
        "pytest[",
        "pytest<",
        "pytest>",
        "pytest=",
        "pytest!",
        "pytest~",
        "pytest ",
        "pytest;",
    ))


def _dependency_list_mentions_pytest(value: object) -> bool:
    return isinstance(value, list) and any(_dependency_string_is_pytest(item) for item in value)


def _mapping_has_pytest_key(value: object) -> bool:
    return isinstance(value, dict) and any(str(key).lower() == "pytest" for key in value)


def _pyproject_mentions_pytest(pyproject: dict[str, object]) -> bool:
    project = pyproject.get("project")
    if isinstance(project, dict):
        if _dependency_list_mentions_pytest(project.get("dependencies")):
            return True
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict) and any(
            _dependency_list_mentions_pytest(group) for group in optional.values()
        ):
            return True

    dependency_groups = pyproject.get("dependency-groups")
    if isinstance(dependency_groups, dict) and any(
        _dependency_list_mentions_pytest(group) for group in dependency_groups.values()
    ):
        return True

    tool = pyproject.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        if _mapping_has_pytest_key(poetry.get("dependencies")):
            return True
        if _mapping_has_pytest_key(poetry.get("dev-dependencies")):
            return True
        groups = poetry.get("group")
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, dict) and _mapping_has_pytest_key(group.get("dependencies")):
                    return True
    return False


def _requirements_mention_pytest(root: Path) -> bool:
    for path in root.glob("requirements*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if any(_dependency_string_is_pytest(line.split("#", 1)[0]) for line in lines):
            return True
    return False


def _ini_mentions_pytest(root: Path) -> bool:
    for filename, section in (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")):
        path = root / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
        except (OSError, UnicodeDecodeError, configparser.Error):
            continue
        if parser.has_section(section):
            return True
    return False


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
    pytest = (
        explicit_pytest
        or _pyproject_mentions_pytest(pyproject)
        or _requirements_mention_pytest(root)
        or _ini_mentions_pytest(root)
    )

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
