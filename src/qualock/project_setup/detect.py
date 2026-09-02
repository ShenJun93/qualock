from __future__ import annotations

import configparser
import json
import os
import re
import tomllib
from pathlib import Path

from .models import ProjectCapabilities, PythonRunner

KNOWN_NPM_SCRIPTS = ("test", "build", "lint", "typecheck")
PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
PYTHON_TARGETS = ("src", "tests", "app")
LOCAL_VENVS = (".venv", "venv")
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


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


def _normalize_dependency_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DEPENDENCY_NAME.match(value.strip())
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def _dependency_list_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        name
        for item in value
        if (name := _normalize_dependency_name(item)) is not None
    }


def _mapping_dependency_names(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    return {
        name
        for key in value
        if (name := _normalize_dependency_name(str(key))) is not None
    }


def _pyproject_dependency_names(pyproject: dict[str, object]) -> set[str]:
    names: set[str] = set()
    project = pyproject.get("project")
    if isinstance(project, dict):
        names.update(_dependency_list_names(project.get("dependencies")))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                names.update(_dependency_list_names(group))

    dependency_groups = pyproject.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for group in dependency_groups.values():
            names.update(_dependency_list_names(group))

    tool = pyproject.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        names.update(_mapping_dependency_names(poetry.get("dependencies")))
        names.update(_mapping_dependency_names(poetry.get("dev-dependencies")))
        groups = poetry.get("group")
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, dict):
                    names.update(_mapping_dependency_names(group.get("dependencies")))
    names.discard("python")
    return names


def _requirements_dependency_names(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.glob("requirements*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            candidate = line.split("#", 1)[0].strip()
            if candidate.startswith(("-", "http:", "https:", "git+")):
                continue
            name = _normalize_dependency_name(candidate)
            if name is not None:
                names.add(name)
    return names


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


def _tool_table(pyproject: dict[str, object], name: str) -> dict[object, object] | None:
    tool = pyproject.get("tool")
    value = tool.get(name) if isinstance(tool, dict) else None
    return value if isinstance(value, dict) else None


def _venv_python_path() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def _valid_local_venv(root: Path) -> tuple[str, str] | None:
    relative_python = _venv_python_path()
    for environment in LOCAL_VENVS:
        env_path = root / environment
        if not (env_path / "pyvenv.cfg").is_file():
            continue
        executable = env_path / relative_python
        if executable.is_file():
            return environment, executable.relative_to(root).as_posix()
    return None


def _detect_python_runner(
    root: Path,
    pyproject: dict[str, object],
) -> tuple[PythonRunner, str | None, str | None]:
    if (root / "uv.lock").is_file() or _tool_table(pyproject, "uv") is not None:
        return PythonRunner.UV, None, None
    if (root / "poetry.lock").is_file() or _tool_table(pyproject, "poetry") is not None:
        return PythonRunner.POETRY, None, None
    local = _valid_local_venv(root)
    if local is not None:
        environment, executable = local
        return PythonRunner.VENV, environment, executable
    return PythonRunner.NONE, None, None


def detect_project(root: Path) -> ProjectCapabilities:
    pyproject = _load_pyproject(root)
    pyproject_dependencies = _pyproject_dependency_names(pyproject)
    requirements_dependencies = _requirements_dependency_names(root)
    python_dependencies = pyproject_dependencies | requirements_dependencies

    uv_project = (root / "uv.lock").is_file() or _tool_table(pyproject, "uv") is not None
    poetry_project = (root / "poetry.lock").is_file() or _tool_table(pyproject, "poetry") is not None
    python = (
        any((root / marker).is_file() for marker in PYTHON_MARKERS)
        or bool(tuple(root.glob("requirements*.txt")))
        or uv_project
        or poetry_project
    )

    tool_config = pyproject.get("tool")
    pytest_config = tool_config.get("pytest") if isinstance(tool_config, dict) else None
    explicit_pytest = (
        (root / "pytest.ini").is_file()
        or (root / "conftest.py").is_file()
        or isinstance(pytest_config, dict)
        or _ini_mentions_pytest(root)
    )
    pytest = explicit_pytest or "pytest" in python_dependencies

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

    python_runner, python_environment, python_executable = _detect_python_runner(root, pyproject)

    package = _load_package_json(root)
    node = package is not None
    scripts_raw = package.get("scripts", {}) if package is not None else {}
    scripts = scripts_raw if isinstance(scripts_raw, dict) else {}
    npm_scripts = tuple(name for name in KNOWN_NPM_SCRIPTS if name in scripts)

    node_dependencies: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        values = package.get(field, {}) if package is not None else {}
        node_dependencies.update(_mapping_dependency_names(values))

    return ProjectCapabilities(
        git=(root / ".git").exists(),
        python=python,
        pytest=pytest,
        node=node,
        react="react" in node_dependencies,
        vite="vite" in node_dependencies,
        django="django" in python_dependencies and (root / "manage.py").is_file(),
        fastapi="fastapi" in python_dependencies,
        nextjs="next" in node_dependencies,
        typescript="typescript" in node_dependencies or (root / "tsconfig.json").is_file(),
        npm_scripts=npm_scripts,
        python_targets=python_targets,
        python_runner=python_runner,
        python_environment=python_environment,
        python_executable=python_executable,
    )
