from pathlib import Path

import pytest
import yaml

from qualock.config.io import ConfigError, load_config
from qualock.config.models import ProjectProtectionConfig
from qualock.project_setup.config import ensure_qualock_project, write_protections


def protection(identifier: str = "smoke") -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id=identifier,
        name="Project still works",
        command=["python", "-c", "raise SystemExit(0)"],
        timeout_seconds=30,
    )


def test_ensure_qualock_project_creates_default_structure(tmp_path: Path) -> None:
    config_path = ensure_qualock_project(tmp_path)

    assert config_path == tmp_path / ".qualock/config.yaml"
    assert config_path.is_file()
    assert (tmp_path / ".qualock/canaries").is_dir()
    assert (tmp_path / ".qualock/results").is_dir()
    assert (tmp_path / ".qualock/.gitignore").read_text(encoding="utf-8") == "results/\nwork/\n"
    assert load_config(config_path).protections == []


def test_write_protections_preserves_unrelated_and_unknown_yaml_keys(tmp_path: Path) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    config_path = qdir / "config.yaml"
    original = {
        "schema_version": 1,
        "agent": {"name": "codex"},
        "model": {"id": "gpt-5.6-terra", "reasoning_effort": "medium"},
        "custom_plugin": {"enabled": True, "note": "keep-me"},
        "protections": [
            {
                "id": "old",
                "name": "Old check",
                "command": ["old-check"],
                "timeout_seconds": 10,
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")

    write_protections(config_path, [protection("new")])

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["agent"] == original["agent"]
    assert raw["model"] == original["model"]
    assert raw["custom_plugin"] == original["custom_plugin"]
    assert [item["id"] for item in raw["protections"]] == ["new"]
    assert load_config(config_path).protections[0].id == "new"


def test_write_protections_rejects_invalid_existing_config_without_mutation(tmp_path: Path) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    config_path = qdir / "config.yaml"
    original = "schema_version: 99\nprotections: []\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigError):
        write_protections(config_path, [protection()])

    assert config_path.read_text(encoding="utf-8") == original


def test_ensure_qualock_project_rejects_invalid_existing_config_before_side_effects(
    tmp_path: Path,
) -> None:
    qdir = tmp_path / ".qualock"
    qdir.mkdir()
    config_path = qdir / "config.yaml"
    original = "schema_version: 99\nprotections: []\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigError):
        ensure_qualock_project(tmp_path)

    assert config_path.read_text(encoding="utf-8") == original
    assert not (qdir / "canaries").exists()
    assert not (qdir / "results").exists()
    assert not (qdir / ".gitignore").exists()
