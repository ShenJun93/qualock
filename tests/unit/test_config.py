from pathlib import Path

import pytest
from pydantic import ValidationError

from qualock.config.io import ConfigError, load_config, write_default_config
from qualock.config.models import QualockConfig


def test_default_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_default_config(path)
    config = load_config(path)
    assert isinstance(config, QualockConfig)
    assert config.agent.name == "codex"
    assert config.model.id == "gpt-5.6-terra"
    assert config.qualification.repetitions == 3
    assert config.integrity.reject_web_search is True


def test_config_accepts_claude_agent() -> None:
    config = QualockConfig.model_validate(
        {"agent": {"name": "claude"}, "model": {"id": "sonnet"}}
    )
    assert config.agent.name == "claude"
    assert config.model.effective_model == "sonnet"


def test_invalid_config_is_wrapped(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_snapshot_is_the_effective_model_when_pinned() -> None:
    config = QualockConfig.model_validate(
        {"model": {"id": "gpt-5.3-codex", "snapshot": "gpt-5.3-codex-2026-08-20"}}
    )
    assert config.model.effective_model == "gpt-5.3-codex-2026-08-20"


def test_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ValidationError):
        QualockConfig.model_validate({"model": {"reasoning_effort": "turbo"}})


def test_project_protections_default_to_empty() -> None:
    config = QualockConfig.model_validate({})
    assert config.protections == []


def test_project_protection_validates_friendly_command() -> None:
    config = QualockConfig.model_validate(
        {
            "protections": [
                {
                    "id": "tests",
                    "name": "Tests still pass",
                    "command": ["python", "-m", "pytest", "-q"],
                    "timeout_seconds": 120,
                }
            ]
        }
    )
    protection = config.protections[0]
    assert protection.id == "tests"
    assert protection.name == "Tests still pass"
    assert protection.command == ["python", "-m", "pytest", "-q"]
    assert protection.timeout_seconds == 120


def test_project_protection_rejects_empty_command() -> None:
    with pytest.raises(ValidationError):
        QualockConfig.model_validate(
            {"protections": [{"id": "tests", "name": "Tests", "command": []}]}
        )
