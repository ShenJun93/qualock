from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import QualockConfig


class ConfigError(ValueError):
    pass


def load_config(path: Path) -> QualockConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return QualockConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid Qualock config {path}: {exc}") from exc


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    config = QualockConfig()
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
