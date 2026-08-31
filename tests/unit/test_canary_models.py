from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.canary.models import CanarySpec


def valid_data(tmp_path: Path) -> dict:
    grader = tmp_path / "grader.patch"
    grader.write_text("diff --git a/x b/x\n", encoding="utf-8")
    return {
        "schema_version": 1,
        "id": "sample",
        "name": "Sample canary",
        "repository": {
            "url": "https://github.com/example/repo.git",
            "base_sha": "a" * 40,
        },
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix the bug without editing tests.",
        "setup": ["python -m pip install -e ."],
        "agent": {"timeout_seconds": 120},
        "grader": {
            "patch": str(grader),
            "command": ["python -m pytest .qualock-grader/test_regression.py -q"],
        },
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    }


def test_valid_canary_schema(tmp_path: Path) -> None:
    canary = CanarySpec.model_validate(valid_data(tmp_path))
    assert canary.id == "sample"
    assert canary.runtime.image == "python:3.12-slim"
    assert canary.agent.timeout_seconds == 120


def test_rejects_unknown_schema_version(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["schema_version"] = 2
    with pytest.raises(ValidationError):
        CanarySpec.model_validate(data)


def test_rejects_non_positive_timeout(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["agent"]["timeout_seconds"] = 0
    with pytest.raises(ValidationError):
        CanarySpec.model_validate(data)


def test_rejects_empty_grader_command(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["grader"]["command"] = []
    with pytest.raises(ValidationError):
        CanarySpec.model_validate(data)
