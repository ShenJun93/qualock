import pytest

from qualock.project_setup.models import ProjectCapabilities, ProtectionLevel, PythonRunner
from qualock.project_setup.packs import recommend_protections
from qualock.project_setup.runners import python_command


def ids(capabilities: ProjectCapabilities, level: ProtectionLevel) -> list[str]:
    return [item.id for item in recommend_protections(capabilities, level)]


def test_uv_python_commands_are_frozen_and_no_sync() -> None:
    capabilities = ProjectCapabilities(
        python=True,
        pytest=True,
        python_runner=PythonRunner.UV,
    )

    command = python_command(capabilities, "-m", "pytest", "-q")

    assert command == [
        "uv",
        "run",
        "--no-sync",
        "--",
        "python",
        "-m",
        "pytest",
        "-q",
    ]


def test_poetry_python_command_uses_project_runner() -> None:
    capabilities = ProjectCapabilities(
        python=True,
        python_runner=PythonRunner.POETRY,
    )

    command = python_command(capabilities, "-m", "compileall", "-q", "src")

    assert command == ["poetry", "run", "python", "-m", "compileall", "-q", "src"]


def test_venv_python_command_uses_detected_executable() -> None:
    capabilities = ProjectCapabilities(
        python=True,
        python_runner=PythonRunner.VENV,
        python_environment=".venv",
        python_executable=".venv/bin/python",
    )

    assert python_command(capabilities, "-m", "pytest") == [
        ".venv/bin/python",
        "-m",
        "pytest",
    ]


def test_no_runner_raises_instead_of_using_qualock_python() -> None:
    capabilities = ProjectCapabilities(
        python=True,
        python_runner=PythonRunner.NONE,
    )

    with pytest.raises(ValueError, match="project Python runner is not available"):
        python_command(capabilities, "-m", "pytest")


def test_no_runner_omits_python_protections() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        pytest=True,
        python_targets=("src",),
        python_runner=PythonRunner.NONE,
    )

    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)

    assert [item.id for item in protections] == ["git-diff-check"]


def test_django_recommended_adds_manage_check_through_runner() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        django=True,
        python_runner=PythonRunner.VENV,
        python_environment=".venv",
        python_executable=".venv/bin/python",
    )

    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    django = next(item for item in protections if item.id == "django-check")

    assert django.command == [".venv/bin/python", "manage.py", "check"]


def test_django_check_is_not_added_at_minimal_level() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        django=True,
        python_targets=("src",),
        python_runner=PythonRunner.VENV,
        python_environment=".venv",
        python_executable=".venv/bin/python",
    )

    assert "django-check" not in ids(capabilities, ProtectionLevel.MINIMAL)


def test_framework_labels_do_not_invent_commands() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        node=True,
        fastapi=True,
        nextjs=True,
        react=True,
        vite=True,
        typescript=True,
    )

    assert ids(capabilities, ProtectionLevel.RECOMMENDED) == ["git-diff-check"]


def test_typescript_uses_existing_typecheck_script_only_at_strong() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        node=True,
        typescript=True,
        npm_scripts=("typecheck",),
    )

    assert "npm-typecheck" not in ids(capabilities, ProtectionLevel.RECOMMENDED)
    strong = recommend_protections(capabilities, ProtectionLevel.STRONG)
    typecheck = next(item for item in strong if item.id == "npm-typecheck")
    assert typecheck.command == ["npm", "run", "typecheck"]
