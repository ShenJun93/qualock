from pathlib import Path

import pytest

from qualock.change_targeting.errors import ChangeTargetingInputError
from qualock.change_targeting.io import (
    PLANNER_INPUT_MAX_BYTES,
    load_change_signal,
    load_target_context,
)
from qualock.change_targeting.models import ChangeSignalV0, TargetContextV0

VALID_SIGNAL = """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      os.family: linux
"""

VALID_CONTEXT = """schema_version: 0
facts:
  os.family: linux
  execution.mode: container
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_change_signal_returns_model(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", VALID_SIGNAL)
    signal = load_change_signal(path)
    assert isinstance(signal, ChangeSignalV0)
    assert signal.impacts[0].contract_id == "command.execution"


def test_load_target_context_returns_model(tmp_path: Path) -> None:
    path = write(tmp_path / "context.yaml", VALID_CONTEXT)
    context = load_target_context(path)
    assert isinstance(context, TargetContextV0)
    assert context.facts["os.family"] == "linux"


def test_load_change_signal_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(tmp_path / "does-not-exist.yaml")


def test_load_target_context_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(tmp_path / "does-not-exist.yaml")


def test_load_change_signal_rejects_oversize_payload_before_yaml_validation(
    tmp_path: Path,
) -> None:
    padding = "# " + ("a" * (PLANNER_INPUT_MAX_BYTES + 10)) + "\n"
    path = write(tmp_path / "signal.yaml", padding + VALID_SIGNAL)
    assert path.stat().st_size > PLANNER_INPUT_MAX_BYTES
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_target_context_rejects_oversize_payload(tmp_path: Path) -> None:
    padding = "# " + ("a" * (PLANNER_INPUT_MAX_BYTES + 10)) + "\n"
    path = write(tmp_path / "context.yaml", padding + VALID_CONTEXT)
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(path)


def test_load_change_signal_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", "schema_version: [")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_target_context_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = write(tmp_path / "context.yaml", "facts: {")
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(path)


def test_load_change_signal_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", "- 1\n- 2\n")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_scalar_top_level(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", "just a scalar\n")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_duplicate_top_level_key(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        VALID_SIGNAL + "\nagent: claude\n",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_duplicate_key_regardless_of_matching_scalar_value(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path / "signal.yaml",
        VALID_SIGNAL + "\nagent: codex\n",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_duplicate_key_true_versus_one(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      flag: true
      flag: 1
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_nested_requirement_map_duplicate_key(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path / "signal.yaml",
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      os.family: linux
      os.family: linux
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_target_context_rejects_nested_duplicate_key(tmp_path: Path) -> None:
    path = write(
        tmp_path / "context.yaml",
        """schema_version: 0
facts:
  os.family: linux
  os.family: windows
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(path)


def test_load_change_signal_rejects_merge_key(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        VALID_SIGNAL
        + """base: &base
  note: unrelated
extra:
  <<: *base
  other: 1
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_shared_recursive_alias_graph(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        VALID_SIGNAL
        + """shared: &shared
  nested: *shared
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_nesting_deeper_than_max_depth(tmp_path: Path) -> None:
    deep = "1"
    for _ in range(80):
        deep = f"[{deep}]"
    path = write(tmp_path / "signal.yaml", VALID_SIGNAL + f"deep: {deep}\n")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_more_than_max_node_count(tmp_path: Path) -> None:
    wide = "[" + ", ".join(str(i) for i in range(15_000)) + "]"
    path = write(tmp_path / "signal.yaml", VALID_SIGNAL + f"wide: {wide}\n")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_non_string_mapping_key(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      1: linux
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_target_context_rejects_non_string_mapping_key(tmp_path: Path) -> None:
    path = write(
        tmp_path / "context.yaml",
        """schema_version: 0
facts:
  true: linux
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(path)


def test_load_change_signal_rejects_unknown_contract(tmp_path: Path) -> None:
    path = write(
        tmp_path / "signal.yaml",
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: made.up
    scope_requirements: {}
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_change_signal_rejects_unknown_schema_field(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", VALID_SIGNAL + "unexpected: true\n")
    with pytest.raises(ChangeTargetingInputError):
        load_change_signal(path)


def test_load_target_context_rejects_non_scalar_fact_value(tmp_path: Path) -> None:
    path = write(
        tmp_path / "context.yaml",
        """schema_version: 0
facts:
  os.family:
    nested: linux
""",
    )
    with pytest.raises(ChangeTargetingInputError):
        load_target_context(path)


def test_load_change_signal_never_returns_partial_model_on_error(tmp_path: Path) -> None:
    path = write(tmp_path / "signal.yaml", "schema_version: [")
    try:
        load_change_signal(path)
        raise AssertionError("expected ChangeTargetingInputError")
    except ChangeTargetingInputError as exc:
        assert not hasattr(exc, "partial_model")
