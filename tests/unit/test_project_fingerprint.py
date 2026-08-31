from pathlib import Path

from qualock.canary.models import CanarySpec
from qualock.project import suite_fingerprint


def make_canary(tmp_path: Path, patch_content: str) -> CanarySpec:
    patch = tmp_path / "grader.patch"
    patch.write_text(patch_content, encoding="utf-8")
    return CanarySpec.model_validate({
        "schema_version": 1,
        "id": "sample",
        "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix it",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(patch), "command": ["pytest -q"]},
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    })


def test_suite_fingerprint_uses_grader_contents_not_absolute_path(tmp_path: Path) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir(); right_dir.mkdir()
    left = make_canary(left_dir, "same")
    right = make_canary(right_dir, "same")
    assert suite_fingerprint([left]) == suite_fingerprint([right])


def test_suite_fingerprint_changes_when_hidden_grader_changes(tmp_path: Path) -> None:
    one = tmp_path / "one"; two = tmp_path / "two"
    one.mkdir(); two.mkdir()
    assert suite_fingerprint([make_canary(one, "a")]) != suite_fingerprint([make_canary(two, "b")])
