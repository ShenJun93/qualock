from pathlib import Path

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode, Node

from .errors import ChangeTargetingInputError
from .models import ChangeSignalV0, TargetContextV0
from .yaml_strict import StrictYamlError, compose_document, validate_node_graph

PLANNER_INPUT_MAX_BYTES = 1024 * 1024


def _construct_native(node: Node) -> object:
    constructor = yaml.constructor.SafeConstructor()
    constructor.constructed_objects = {}
    constructor.recursive_objects = {}
    constructor.state_generators = []
    constructor.deep_construct = True
    return constructor.construct_document(node)


def _read_bounded_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(PLANNER_INPUT_MAX_BYTES + 1)
    except OSError as exc:
        raise ChangeTargetingInputError(f"failed to read {path}: {exc}") from exc

    if len(raw) > PLANNER_INPUT_MAX_BYTES:
        raise ChangeTargetingInputError(
            f"{path} exceeds max input size of {PLANNER_INPUT_MAX_BYTES} bytes"
        )

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChangeTargetingInputError(f"{path} is not valid utf-8: {exc}") from exc


def _load_strict_document(path: Path) -> dict[str, object]:
    text = _read_bounded_text(path)

    try:
        root = compose_document(text)
        if root is None or not isinstance(root, MappingNode):
            raise StrictYamlError(f"{path} must contain a top-level YAML mapping")
        validate_node_graph(root)
        data = _construct_native(root)
    except StrictYamlError as exc:
        raise ChangeTargetingInputError(f"invalid input {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ChangeTargetingInputError(f"{path} must contain a YAML mapping")
    return data


def load_change_signal(path: Path) -> ChangeSignalV0:
    data = _load_strict_document(path)
    try:
        return ChangeSignalV0.model_validate(data)
    except ValidationError as exc:
        raise ChangeTargetingInputError(f"invalid change signal {path}: {exc}") from exc


def load_target_context(path: Path) -> TargetContextV0:
    data = _load_strict_document(path)
    try:
        return TargetContextV0.model_validate(data)
    except ValidationError as exc:
        raise ChangeTargetingInputError(f"invalid target context {path}: {exc}") from exc
