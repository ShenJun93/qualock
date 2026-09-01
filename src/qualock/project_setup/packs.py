from __future__ import annotations

import sys

from qualock.config.models import ProjectProtectionConfig

from .models import ProjectCapabilities, ProtectionLevel


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


def _recommended(capabilities: ProjectCapabilities) -> list[ProjectProtectionConfig]:
    items: list[ProjectProtectionConfig] = []
    if capabilities.pytest:
        items.append(
            _protection(
                "pytest",
                "Tests still pass",
                [sys.executable, "-m", "pytest", "-q"],
                180,
            )
        )
    if capabilities.python:
        items.append(
            _protection(
                "python-compile",
                "Python code still compiles",
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    *capabilities.python_targets,
                ],
                120,
            )
        )
    if "test" in capabilities.npm_scripts:
        items.append(_protection("npm-test", "JavaScript tests still pass", ["npm", "test"], 180))
    if "build" in capabilities.npm_scripts:
        items.append(
            _protection("npm-build", "Frontend build still works", ["npm", "run", "build"], 180)
        )
    if capabilities.git:
        items.append(
            _protection("git-diff-check", "Git patch has no whitespace errors", ["git", "diff", "--check"], 30)
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
        priority = ("pytest", "npm-test", "npm-build", "python-compile", "git-diff-check")
        by_id = {item.id: item for item in recommended}
        for identifier in priority:
            if identifier in by_id:
                return (by_id[identifier],)
        return ()

    items = list(recommended)
    if level is ProtectionLevel.STRONG:
        if "lint" in capabilities.npm_scripts:
            items.append(
                _protection("npm-lint", "JavaScript lint still passes", ["npm", "run", "lint"], 120)
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
