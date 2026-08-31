from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import CanarySpec


class CanaryLoadError(ValueError):
    pass


def load_canary(path: Path) -> CanarySpec:
    path = path.resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CanaryLoadError(f"failed to load canary {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CanaryLoadError(f"canary {path} must contain a YAML mapping")

    grader = raw.get("grader")
    if isinstance(grader, dict) and isinstance(grader.get("patch"), str):
        patch = Path(grader["patch"])
        if not patch.is_absolute():
            patch = (path.parent / patch).resolve()
        grader["patch"] = str(patch)

    try:
        canary = CanarySpec.model_validate(raw)
    except ValidationError as exc:
        raise CanaryLoadError(f"invalid canary {path}: {exc}") from exc

    if not canary.grader.patch.is_file():
        raise CanaryLoadError(f"grader patch does not exist: {canary.grader.patch}")
    return canary


def load_suite(paths: Sequence[Path]) -> list[CanarySpec]:
    canaries = [load_canary(path) for path in paths]
    seen: set[str] = set()
    for canary in canaries:
        if canary.id in seen:
            raise CanaryLoadError(f"duplicate canary id: {canary.id}")
        seen.add(canary.id)
    return canaries
