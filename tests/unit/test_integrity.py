import pytest

from qualock.run.integrity import IntegrityPathError, normalize_repo_path, protected_path_violations


def test_normalizes_dot_segments() -> None:
    assert normalize_repo_path("./src/./app.py").as_posix() == "src/app.py"


def test_rejects_parent_traversal() -> None:
    with pytest.raises(IntegrityPathError):
        normalize_repo_path("../private/grader.patch")


def test_rejects_absolute_paths() -> None:
    with pytest.raises(IntegrityPathError):
        normalize_repo_path("/etc/passwd")


def test_matches_protected_glob_after_normalization() -> None:
    violations = protected_path_violations(
        ["./tests/unit/test_x.py", "src/app.py"],
        ["tests/**", "pyproject.toml"],
    )
    assert violations == ["tests/unit/test_x.py"]


def test_protects_exact_root_file() -> None:
    assert protected_path_violations(["pyproject.toml"], ["pyproject.toml"]) == [
        "pyproject.toml"
    ]
