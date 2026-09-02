import json
import subprocess
from pathlib import Path

from qualock.project_setup.detect import detect_project
from qualock.project_setup.models import ProtectionLevel, PythonRunner
from qualock.project_setup.packs import recommend_protections


def init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def test_detects_python_pytest_and_git_without_executing_project_code(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.pytest.ini_options]\naddopts='-q'\n",
        encoding="utf-8",
    )

    capabilities = detect_project(tmp_path)

    assert capabilities.git is True
    assert capabilities.python is True
    assert capabilities.pytest is True
    assert capabilities.python_targets == ("src", "tests")
    assert capabilities.supported is True


def test_detects_react_vite_and_known_npm_scripts(tmp_path: Path) -> None:
    init_git(tmp_path)
    payload = {
        "scripts": {
            "test": "vitest run",
            "build": "vite build",
            "lint": "eslint .",
            "typecheck": "tsc --noEmit",
            "dev": "vite",
        },
        "dependencies": {"react": "19.0.0"},
        "devDependencies": {"vite": "7.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.node is True
    assert capabilities.react is True
    assert capabilities.vite is True
    assert capabilities.npm_scripts == ("test", "build", "lint", "typecheck")


def test_recommended_python_pack_is_deterministic(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['pytest>=8']\n", encoding="utf-8")
    capabilities = detect_project(tmp_path)

    first = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    second = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)

    assert [item.id for item in first] == ["pytest", "python-compile", "git-diff-check"]
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


def test_minimal_selects_single_highest_signal_check(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['pytest>=8']\n", encoding="utf-8")

    protections = recommend_protections(detect_project(tmp_path), ProtectionLevel.MINIMAL)

    assert [item.id for item in protections] == ["pytest"]


def test_strong_node_pack_adds_lint_and_typecheck_only_when_scripts_exist(tmp_path: Path) -> None:
    init_git(tmp_path)
    payload = {
        "scripts": {"test": "vitest run", "build": "vite build", "lint": "eslint ."},
        "dependencies": {"react": "19.0.0"},
        "devDependencies": {"vite": "7.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")

    protections = recommend_protections(detect_project(tmp_path), ProtectionLevel.STRONG)

    assert [item.id for item in protections] == [
        "npm-test",
        "npm-build",
        "git-diff-check",
        "npm-lint",
    ]
    assert "npm-typecheck" not in {item.id for item in protections}


def test_generic_git_project_gets_git_patch_check(tmp_path: Path) -> None:
    init_git(tmp_path)

    protections = recommend_protections(detect_project(tmp_path), ProtectionLevel.RECOMMENDED)

    assert [item.id for item in protections] == ["git-diff-check"]


def test_unknown_non_git_directory_is_unsupported(tmp_path: Path) -> None:
    capabilities = detect_project(tmp_path)

    assert capabilities.supported is False
    assert recommend_protections(capabilities, ProtectionLevel.RECOMMENDED) == ()


def test_python_pack_prefers_project_local_venv_interpreter(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    interpreter = tmp_path / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    (tmp_path / ".venv/pyvenv.cfg").write_text("home = /usr/bin" + chr(10), encoding="utf-8")

    capabilities = detect_project(tmp_path)
    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)

    assert capabilities.python_executable == ".venv/bin/python"
    compile_check = next(item for item in protections if item.id == "python-compile")
    assert compile_check.command[0] == ".venv/bin/python"


def test_git_patch_check_covers_staged_and_unstaged_changes(tmp_path: Path) -> None:
    init_git(tmp_path)

    protections = recommend_protections(detect_project(tmp_path), ProtectionLevel.RECOMMENDED)

    assert protections[0].command == ["git", "diff", "HEAD", "--check"]


def test_node_tests_directory_does_not_imply_pytest_without_python_metadata(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "echo js-tests"}}),
        encoding="utf-8",
    )

    capabilities = detect_project(tmp_path)

    assert capabilities.node is True
    assert capabilities.python is False
    assert capabilities.pytest is False
    assert "pytest" not in {item.id for item in recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)}


def test_python_compile_targets_top_level_package_without_scanning_venv(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    interpreter = tmp_path / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")

    capabilities = detect_project(tmp_path)
    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)

    assert capabilities.python_targets == ("demo",)
    compile_check = next(item for item in protections if item.id == "python-compile")
    assert compile_check.command[-1] == "demo"
    assert ".venv" not in compile_check.command


def test_valid_empty_package_json_still_detects_node(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.node is True
    assert "Node/npm" in capabilities.labels


def test_python_tests_directory_without_pytest_signal_does_not_detect_pytest(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n",
        encoding="utf-8",
    )

    capabilities = detect_project(tmp_path)

    assert capabilities.python is True
    assert capabilities.pytest is False
    assert "pytest" not in {
        item.id
        for item in recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    }


def test_requirements_dependency_detects_pytest(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "requirements-dev.txt").write_text('pytest>=8\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()

    capabilities = detect_project(tmp_path)

    assert capabilities.python is True
    assert capabilities.pytest is True


def test_invalid_utf8_metadata_does_not_crash_detection(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "pyproject.toml").write_bytes(bytes([255]))
    (tmp_path / "package.json").write_bytes(bytes([255]))
    (tmp_path / "requirements-dev.txt").write_bytes(bytes([255]))

    capabilities = detect_project(tmp_path)

    assert capabilities.python is True
    assert capabilities.pytest is False
    assert capabilities.node is False


def test_setup_cfg_pytest_section_is_detected(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.pytest is True


def test_tox_ini_pytest_section_is_detected(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "tox.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.pytest is True


def test_poetry_legacy_dev_dependency_detects_pytest(tmp_path: Path) -> None:
    init_git(tmp_path)
    payload = "[tool.poetry]\nname='demo'\n[tool.poetry.dev-dependencies]\npytest='^8'\n"
    (tmp_path / "pyproject.toml").write_text(payload, encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.python is True
    assert capabilities.pytest is True


def test_uv_runner_wins_over_poetry_and_venv(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin" + chr(10), encoding="utf-8")
    python = venv / "bin/python"
    python.parent.mkdir(exist_ok=True)
    python.write_text("", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.python_runner is PythonRunner.UV


def test_poetry_runner_wins_over_local_venv(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin" + chr(10), encoding="utf-8")
    python = venv / "bin/python"
    python.parent.mkdir(exist_ok=True)
    python.write_text("", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.python_runner is PythonRunner.POETRY


def test_local_venv_requires_pyvenv_cfg_and_python(tmp_path: Path) -> None:
    init_git(tmp_path)
    pyproject = "[project]" + chr(10) + "name='demo'" + chr(10)
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").write_text("", encoding="utf-8")

    assert detect_project(tmp_path).python_runner is PythonRunner.NONE


def test_valid_local_venv_records_relative_environment(tmp_path: Path) -> None:
    init_git(tmp_path)
    pyproject = "[project]" + chr(10) + "name='demo'" + chr(10)
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin" + chr(10), encoding="utf-8")
    (venv / "bin/python").write_text("", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.python_runner is PythonRunner.VENV
    assert capabilities.python_environment == ".venv"


def test_detects_python_frameworks_from_declared_dependencies(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    pyproject = (
        "[project]" + chr(10)
        + "name='demo'" + chr(10)
        + "dependencies=['Django>=5', 'fastapi>=0.115']" + chr(10)
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.django is True
    assert capabilities.fastapi is True
    assert "Django" in capabilities.labels
    assert "FastAPI" in capabilities.labels


def test_django_dependency_without_manage_py_is_labelled_false(tmp_path: Path) -> None:
    init_git(tmp_path)
    pyproject = "[project]" + chr(10) + "name='demo'" + chr(10) + "dependencies=['Django>=5']" + chr(10)
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    assert detect_project(tmp_path).django is False


def test_detects_nextjs_and_typescript_from_package_metadata(tmp_path: Path) -> None:
    init_git(tmp_path)
    payload = {
        "dependencies": {"next": "16.0.0", "react": "19.0.0"},
        "devDependencies": {"typescript": "6.0.0", "vite": "7.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.nextjs is True
    assert capabilities.typescript is True
    assert "Next.js" in capabilities.labels
    assert "TypeScript" in capabilities.labels


def test_tsconfig_alone_detects_typescript_for_node_project(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "package.json").write_text("{}" + chr(10), encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}" + chr(10), encoding="utf-8")

    assert detect_project(tmp_path).typescript is True
