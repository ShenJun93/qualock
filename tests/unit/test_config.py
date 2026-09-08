from pathlib import Path

import pytest
from pydantic import ValidationError

import qualock.config.models as config_models
from qualock.config.io import ConfigError, load_config, write_default_config
from qualock.config.models import QualockConfig


def test_default_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_default_config(path)
    assert path.read_text(encoding="utf-8") == (
        "schema_version: 1\n"
        "agent:\n"
        "  name: codex\n"
        "model:\n"
        "  id: gpt-5.6-terra\n"
        "  snapshot: null\n"
        "  reasoning_effort: high\n"
        "qualification:\n"
        "  repetitions: 3\n"
        "integrity:\n"
        "  reject_web_search: true\n"
        "  reject_mcp_calls: true\n"
        "  reject_protected_path_changes: true\n"
        "canary_globs:\n"
        "- .qualock/canaries/*.yaml\n"
        "protections: []\n"
    )
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


def test_config_accepts_antigravity_agent() -> None:
    config = QualockConfig.model_validate({"agent": {"name": "antigravity"}})
    assert config.agent.name == "antigravity"


def test_accepts_gemini_provider_default_contract() -> None:
    config = QualockConfig.model_validate(
        {
            "agent": {"name": "gemini"},
            "model": {
                "id": "gemini-3.5-flash",
                "reasoning_effort": "provider-default",
            },
        }
    )

    config_models.validate_agent_model_contract(config)


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_gemini_rejects_qualock_reasoning_efforts(effort: str) -> None:
    config = QualockConfig.model_validate(
        {
            "agent": {"name": "gemini"},
            "model": {"id": "gemini-3.5-flash", "reasoning_effort": effort},
        }
    )

    with pytest.raises(ValueError, match="Gemini requires reasoning_effort"):
        config_models.validate_agent_model_contract(config)


@pytest.mark.parametrize("agent", ["codex", "claude", "antigravity"])
def test_non_gemini_rejects_provider_default_reasoning_effort(agent: str) -> None:
    config = QualockConfig.model_validate(
        {
            "agent": {"name": agent},
            "model": {"id": "model", "reasoning_effort": "provider-default"},
        }
    )

    with pytest.raises(ValueError, match="only supported by Gemini"):
        config_models.validate_agent_model_contract(config)


def test_gemini_requires_explicit_model_instead_of_untouched_default() -> None:
    config = QualockConfig.model_validate(
        {
            "agent": {"name": "gemini"},
            "model": {"reasoning_effort": "provider-default"},
        }
    )

    with pytest.raises(ValueError, match="explicit Gemini CLI model"):
        config_models.validate_agent_model_contract(config)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "agent": {"name": "gemini"},
            "model": {"id": "gemini-3.5-flash", "reasoning_effort": "high"},
        },
        {"model": {"reasoning_effort": "provider-default"}},
    ],
)
def test_load_config_wraps_agent_model_contract_errors(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    path = tmp_path / "config.yaml"
    import yaml

    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid Qualock config"):
        load_config(path)


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
