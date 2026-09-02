from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from qualock.config.io import ConfigError, load_config, write_default_config
from qualock.config.models import ProjectProtectionConfig, QualockConfig
from qualock.project import project_dir


def ensure_qualock_project(root: Path) -> Path:
    qdir = project_dir(root)
    config_path = qdir / "config.yaml"
    if config_path.exists():
        load_config(config_path)

    (qdir / "canaries").mkdir(parents=True, exist_ok=True)
    (qdir / "results").mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        write_default_config(config_path)

    ignore_path = qdir / ".gitignore"
    if not ignore_path.exists():
        ignore_path.write_text("results/\nwork/\n", encoding="utf-8")

    return config_path


def write_protections(
    config_path: Path,
    protections: Sequence[ProjectProtectionConfig],
) -> None:
    load_config(config_path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid Qualock config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid Qualock config {config_path}: expected a YAML mapping")

    candidate = dict(raw)
    candidate["protections"] = [item.model_dump(mode="json") for item in protections]
    try:
        QualockConfig.model_validate(candidate)
    except ValidationError as exc:
        raise ConfigError(f"invalid Qualock config {config_path}: {exc}") from exc

    config_path.write_text(
        yaml.safe_dump(candidate, sort_keys=False),
        encoding="utf-8",
    )
