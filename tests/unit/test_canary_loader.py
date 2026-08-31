from pathlib import Path

import pytest

from qualock.canary.loader import CanaryLoadError, load_canary, load_suite


def write_canary(path: Path, *, canary_id: str = "one", grader_name: str = "grader.patch") -> None:
    path.write_text(
        f"""schema_version: 1
id: {canary_id}
name: Example
repository:
  url: https://github.com/example/repo.git
  base_sha: {'a' * 40}
runtime:
  image: python:3.12-slim
task: Fix the bug.
setup:
  - python -m pip install -e .
agent:
  timeout_seconds: 120
grader:
  patch: {grader_name}
  command:
    - python -m pytest grader.py -q
constraints:
  protected_paths:
    - tests/**
critical: true
""",
        encoding="utf-8",
    )


def test_load_canary_resolves_relative_grader_path(tmp_path: Path) -> None:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)

    canary = load_canary(yaml_path)

    assert canary.grader.patch == grader.resolve()


def test_load_canary_rejects_malformed_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "canary.yaml"
    yaml_path.write_text("schema_version: [", encoding="utf-8")
    with pytest.raises(CanaryLoadError):
        load_canary(yaml_path)


def test_load_canary_rejects_missing_grader(tmp_path: Path) -> None:
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    with pytest.raises(CanaryLoadError, match="grader"):
        load_canary(yaml_path)


def test_load_suite_rejects_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "grader.patch").write_text("patch", encoding="utf-8")
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    write_canary(first, canary_id="same")
    write_canary(second, canary_id="same")
    with pytest.raises(CanaryLoadError, match="duplicate"):
        load_suite([first, second])
