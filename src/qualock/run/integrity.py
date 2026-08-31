import fnmatch
import posixpath
from pathlib import PurePosixPath
from collections.abc import Sequence


class IntegrityPathError(ValueError):
    pass


def normalize_repo_path(path: str) -> PurePosixPath:
    candidate = path.replace("\\", "/")
    if candidate.startswith("/"):
        raise IntegrityPathError(f"absolute path is outside repository: {path}")
    normalized = posixpath.normpath(candidate)
    if normalized in {".", ""}:
        return PurePosixPath(".")
    if normalized == ".." or normalized.startswith("../"):
        raise IntegrityPathError(f"path escapes repository: {path}")
    return PurePosixPath(normalized)


def protected_path_violations(
    changed: Sequence[str], protected: Sequence[str]
) -> list[str]:
    violations: list[str] = []
    for raw_path in changed:
        normalized = normalize_repo_path(raw_path).as_posix()
        if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in protected):
            violations.append(normalized)
    return violations
