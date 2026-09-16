from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

STRICT_YAML_MAX_DEPTH = 64
STRICT_YAML_MAX_NODES = 10_000

_STR_TAG = "tag:yaml.org,2002:str"
_MERGE_TAG = "tag:yaml.org,2002:merge"
_MERGE_KEY = "<<"


class StrictYamlError(ValueError):
    pass


def compose_document(text: str) -> Node | None:
    try:
        return yaml.compose(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise StrictYamlError(f"failed to parse yaml: {exc}") from exc


def _is_merge_key(key_node: Node) -> bool:
    return isinstance(key_node, ScalarNode) and (
        key_node.tag == _MERGE_TAG or key_node.value == _MERGE_KEY
    )


def validate_node_graph(root: Node) -> None:
    """Iteratively validate an entire composed yaml node graph.

    Rejects aliases/shared nodes, excessive depth or node counts,
    non-scalar-string mapping keys, merge keys, and duplicate mapping keys
    (even when the duplicated values are identical).
    """
    seen_node_ids: set[int] = set()
    stack: list[tuple[Node, int]] = [(root, 0)]
    node_count = 0
    while stack:
        node, depth = stack.pop()
        if depth > STRICT_YAML_MAX_DEPTH:
            raise StrictYamlError(f"yaml exceeds max depth of {STRICT_YAML_MAX_DEPTH}")
        node_id = id(node)
        if node_id in seen_node_ids:
            raise StrictYamlError("yaml aliases or shared nodes are not permitted")
        seen_node_ids.add(node_id)
        node_count += 1
        if node_count > STRICT_YAML_MAX_NODES:
            raise StrictYamlError(f"yaml exceeds max node count of {STRICT_YAML_MAX_NODES}")

        if isinstance(node, MappingNode):
            seen_keys: set[str] = set()
            for key_node, value_node in node.value:
                if _is_merge_key(key_node):
                    raise StrictYamlError("yaml merge keys ('<<') are not permitted")
                if not isinstance(key_node, ScalarNode) or key_node.tag != _STR_TAG:
                    tag = getattr(key_node, "tag", type(key_node).__name__)
                    raise StrictYamlError(f"unsupported mapping key tag: {tag}")
                key = key_node.value
                if key == "":
                    raise StrictYamlError("mapping keys must be non-empty")
                if key in seen_keys:
                    raise StrictYamlError(f"duplicate mapping key: {key}")
                seen_keys.add(key)
                stack.append((key_node, depth + 1))
                stack.append((value_node, depth + 1))
        elif isinstance(node, SequenceNode):
            for item in node.value:
                stack.append((item, depth + 1))
        elif isinstance(node, ScalarNode):
            continue
        else:
            raise StrictYamlError(f"unsupported yaml node type: {type(node).__name__}")


@dataclass(frozen=True)
class CoverageDeclarationDetection:
    literal_count: int
    merge_reachable: bool


def detect_root_coverage_declaration(root: Node) -> CoverageDeclarationDetection:
    """Bounded, non-materializing check for a root-level ``coverage`` key.

    Only inspects the root mapping's own key/value pairs plus the
    merge/anchor graph reachable from any root-level merge (``<<``) keys, so
    it never walks the rest of the document.
    """
    if not isinstance(root, MappingNode):
        return CoverageDeclarationDetection(literal_count=0, merge_reachable=False)

    literal_count = 0
    merge_value_nodes: list[Node] = []
    for key_node, value_node in root.value:
        if (
            isinstance(key_node, ScalarNode)
            and key_node.tag == _STR_TAG
            and key_node.value == "coverage"
        ):
            literal_count += 1
        if _is_merge_key(key_node):
            merge_value_nodes.append(value_node)

    merge_reachable = bool(merge_value_nodes) and _merge_graph_declares_key(
        merge_value_nodes, "coverage"
    )
    return CoverageDeclarationDetection(
        literal_count=literal_count, merge_reachable=merge_reachable
    )


def _merge_graph_declares_key(start_nodes: list[Node], key: str) -> bool:
    seen_node_ids: set[int] = set()
    stack: list[tuple[Node, int]] = [(node, 1) for node in start_nodes]
    node_count = 0
    while stack:
        node, depth = stack.pop()
        if depth > STRICT_YAML_MAX_DEPTH:
            raise StrictYamlError(f"yaml exceeds max depth of {STRICT_YAML_MAX_DEPTH}")
        node_id = id(node)
        if node_id in seen_node_ids:
            raise StrictYamlError("yaml aliases or shared nodes are not permitted")
        seen_node_ids.add(node_id)
        node_count += 1
        if node_count > STRICT_YAML_MAX_NODES:
            raise StrictYamlError(f"yaml exceeds max node count of {STRICT_YAML_MAX_NODES}")

        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                if (
                    isinstance(key_node, ScalarNode)
                    and key_node.tag == _STR_TAG
                    and key_node.value == key
                ):
                    return True
                if _is_merge_key(key_node):
                    stack.append((value_node, depth + 1))
        elif isinstance(node, SequenceNode):
            for item in node.value:
                stack.append((item, depth + 1))
    return False
