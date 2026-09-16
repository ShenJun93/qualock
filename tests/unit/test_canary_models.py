from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.canary.models import CanarySpec, RuntimeSpec


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


def test_runtime_defaults_to_container() -> None:
    runtime = RuntimeSpec(image="python:3.12")
    assert runtime.execution == "container"
    assert runtime.image == "python:3.12"


def test_linux_host_runtime_rejects_container_image() -> None:
    with pytest.raises(ValidationError):
        RuntimeSpec(execution="linux-host", image="python:3.12")


def test_linux_host_runtime_allows_no_image() -> None:
    assert RuntimeSpec(execution="linux-host").image is None


def test_paired_change_metadata_is_optional_for_legacy_canary(tmp_path: Path) -> None:
    canary = CanarySpec.model_validate(valid_data(tmp_path))
    assert canary.paired_change is None


def test_paired_change_metadata_parses_material_dimensions_and_gap(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["paired_change"] = {
        "material_dimensions": ["AGENT_BINARY", "AGENT_SUPPORT"],
        "max_pair_gap_ms": 5000,
    }
    canary = CanarySpec.model_validate(data)
    assert canary.paired_change is not None
    assert canary.paired_change.material_dimensions == ("AGENT_BINARY", "AGENT_SUPPORT")
    assert canary.paired_change.max_pair_gap_ms == 5000


@pytest.mark.parametrize(
    'paired_change',
    [
        {'material_dimensions': [], 'max_pair_gap_ms': 5000},
        {'material_dimensions': ['AGENT_BINARY', 'AGENT_BINARY'], 'max_pair_gap_ms': 5000},
        {'material_dimensions': ['UNKNOWN_DIMENSION'], 'max_pair_gap_ms': 5000},
        {'material_dimensions': ['AGENT_BINARY'], 'max_pair_gap_ms': 0},
    ],
)
def test_paired_change_metadata_rejects_invalid_values(tmp_path: Path, paired_change: dict[str, object]) -> None:
    data = valid_data(tmp_path)
    data['paired_change'] = paired_change
    with pytest.raises(ValidationError):
        CanarySpec.model_validate(data)


def test_coverage_metadata_is_optional_for_legacy_canary(tmp_path: Path) -> None:
    canary = CanarySpec.model_validate(valid_data(tmp_path))
    assert canary.coverage == ()


def test_coverage_metadata_parses_explicit_context_requirements(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = [
        {
            "contract_id": "command.execution",
            "context_requirements": {"execution.mode": "container"},
        }
    ]
    canary = CanarySpec.model_validate(data)
    assert len(canary.coverage) == 1
    assert canary.coverage[0].contract_id == "command.execution"
    assert canary.coverage[0].context_requirements == {"execution.mode": "container"}


def test_coverage_context_requirements_default_to_empty_dict(tmp_path: Path) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = [{"contract_id": "command.execution"}]
    canary = CanarySpec.model_validate(data)
    assert canary.coverage[0].context_requirements == {}


def test_coverage_rejects_unknown_extra_field_that_could_make_scope_unconditional(
    tmp_path: Path,
) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = [
        {
            "contract_id": "command.execution",
            "context_requirement": {"execution.mode": "container"},
        }
    ]
    with pytest.raises(ValidationError, match="context_requirement"):
        CanarySpec.model_validate(data)


def test_coverage_allows_two_individually_valid_declarations_for_same_contract(
    tmp_path: Path,
) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = [
        {
            "contract_id": "command.execution",
            "context_requirements": {"execution.mode": "container"},
        },
        {
            "contract_id": "command.execution",
            "context_requirements": {"execution.mode": "linux-host"},
        },
    ]
    canary = CanarySpec.model_validate(data)
    assert len(canary.coverage) == 2
    assert {item.context_requirements["execution.mode"] for item in canary.coverage} == {
        "container",
        "linux-host",
    }


@pytest.mark.parametrize(
    "coverage",
    [
        [{"contract_id": "unknown.contract"}],
        [{"contract_id": "command.execution", "context_requirements": {"key": 1.5}}],
        [{"contract_id": "command.execution", "context_requirements": {"key": ["a"]}}],
        [{"contract_id": "command.execution", "context_requirements": {"key": {"nested": 1}}}],
        [{"contract_id": "command.execution", "context_requirements": {"": "value"}}],
    ],
)
def test_coverage_rejects_invalid_declarations(
    tmp_path: Path, coverage: list[dict[str, object]]
) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = coverage
    with pytest.raises(ValidationError):
        CanarySpec.model_validate(data)
