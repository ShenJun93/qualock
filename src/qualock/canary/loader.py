from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from qualock.change_targeting.yaml_strict import (
    StrictYamlError,
    compose_document,
    detect_root_coverage_declaration,
    validate_node_graph,
)

from .models import CanarySpec


class CanaryLoadError(ValueError):
    pass


def _check_coverage_strictness(path: Path, text: str) -> None:
    try:
        root = compose_document(text)
        if root is None:
            return
        detection = detect_root_coverage_declaration(root)
        if detection.literal_count > 1:
            raise StrictYamlError("top-level 'coverage' key is declared more than once")
        if detection.literal_count == 1:
            validate_node_graph(root)
        elif detection.merge_reachable:
            raise StrictYamlError(
                "'coverage' is only reachable via a yaml merge; coverage metadata "
                "must be an explicit literal root field"
            )
    except StrictYamlError as exc:
        raise CanaryLoadError(f"invalid canary {path}: {exc}") from exc


def load_canary(path: Path) -> CanarySpec:
    path = path.resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CanaryLoadError(f"failed to load canary {path}: {exc}") from exc

    _check_coverage_strictness(path, text)

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
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
