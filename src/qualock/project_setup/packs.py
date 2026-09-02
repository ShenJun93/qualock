from __future__ import annotations

from qualock.config.models import ProjectProtectionConfig

from .models import ProjectCapabilities, ProtectionLevel
from .runners import python_command


def _protection(
    identifier: str,
    name: str,
    command: list[str],
    timeout_seconds: int,
) -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id=identifier,
        name=name,
        command=command,
        timeout_seconds=timeout_seconds,
    )


def _project_python_command(
    capabilities: ProjectCapabilities,
    *args: str,
) -> list[str] | None:
    try:
        return python_command(capabilities, *args)
    except ValueError:
        return None


def _recommended(capabilities: ProjectCapabilities) -> list[ProjectProtectionConfig]:
    items: list[ProjectProtectionConfig] = []

    pytest_command = _project_python_command(capabilities, "-m", "pytest", "-q")
    if capabilities.pytest and pytest_command is not None:
        items.append(
            _protection(
                "pytest",
                "Tests still pass",
                pytest_command,
                180,
            )
        )

    compile_command = _project_python_command(
        capabilities,
        "-m",
        "compileall",
        "-q",
        *capabilities.python_targets,
    )
    if capabilities.python and capabilities.python_targets and compile_command is not None:
        items.append(
            _protection(
                "python-compile",
                "Python code still compiles",
                compile_command,
                120,
            )
        )

    django_command = _project_python_command(capabilities, "manage.py", "check")
    if capabilities.django and django_command is not None:
        items.append(
            _protection(
                "django-check",
                "Django system check still passes",
                django_command,
                120,
            )
        )

    if "test" in capabilities.npm_scripts:
        items.append(
            _protection(
                "npm-test",
                "JavaScript tests still pass",
                ["npm", "test"],
                180,
            )
        )
    if "build" in capabilities.npm_scripts:
        items.append(
            _protection(
                "npm-build",
                "Frontend build still works",
                ["npm", "run", "build"],
                180,
            )
        )
    if capabilities.git:
        items.append(
            _protection(
                "git-diff-check",
                "Git patch has no whitespace errors",
                ["git", "diff", "HEAD", "--check"],
                30,
            )
        )
    return items


def recommend_protections(
    capabilities: ProjectCapabilities,
    level: ProtectionLevel,
) -> tuple[ProjectProtectionConfig, ...]:
    recommended = _recommended(capabilities)
    if not recommended:
        return ()

    if level is ProtectionLevel.MINIMAL:
        priority = (
            "pytest",
            "npm-test",
            "npm-build",
            "python-compile",
            "git-diff-check",
        )
        by_id = {item.id: item for item in recommended}
        for identifier in priority:
            if identifier in by_id:
                return (by_id[identifier],)
        return ()

    items = list(recommended)
    if level is ProtectionLevel.STRONG:
        if "lint" in capabilities.npm_scripts:
            items.append(
                _protection(
                    "npm-lint",
                    "JavaScript lint still passes",
                    ["npm", "run", "lint"],
                    120,
                )
            )
        if "typecheck" in capabilities.npm_scripts:
            items.append(
                _protection(
                    "npm-typecheck",
                    "TypeScript type check still passes",
                    ["npm", "run", "typecheck"],
                    120,
                )
            )

    deduplicated: dict[str, ProjectProtectionConfig] = {}
    for item in items:
        deduplicated.setdefault(item.id, item)
    return tuple(deduplicated.values())
