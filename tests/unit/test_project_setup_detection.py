import json
import subprocess
from pathlib import Path

from qualock.project_setup.detect import detect_project
from qualock.project_setup.models import ProtectionLevel
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
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    capabilities = detect_project(tmp_path)

    first = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    second = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)

    assert [item.id for item in first] == ["pytest", "python-compile", "git-diff-check"]
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


def test_minimal_selects_single_highest_signal_check(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

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
