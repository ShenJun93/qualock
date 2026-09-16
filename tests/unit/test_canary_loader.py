import os
import subprocess
import sys
from pathlib import Path

import pytest

from qualock.canary.loader import CanaryLoadError, load_canary, load_suite


def write_canary(path: Path, *, canary_id: str = "one", grader_name: str = "grader.patch") -> None:
    path.write_text(
        f"""schema_version: 1
id: {canary_id}
name: Example
repository:
  url: https://github.com/example/repo.git
  base_sha: {'a' * 40}
runtime:
  image: python:3.12-slim
task: Fix the bug.
setup:
  - python -m pip install -e .
agent:
  timeout_seconds: 120
grader:
  patch: {grader_name}
  command:
    - python -m pytest grader.py -q
constraints:
  protected_paths:
    - tests/**
critical: true
""",
        encoding="utf-8",
    )


def test_load_canary_resolves_relative_grader_path(tmp_path: Path) -> None:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)

    canary = load_canary(yaml_path)

    assert canary.grader.patch == grader.resolve()


def test_load_canary_rejects_malformed_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "canary.yaml"
    yaml_path.write_text("schema_version: [", encoding="utf-8")
    with pytest.raises(CanaryLoadError):
        load_canary(yaml_path)


def test_load_canary_rejects_missing_grader(tmp_path: Path) -> None:
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    with pytest.raises(CanaryLoadError, match="grader"):
        load_canary(yaml_path)


def test_load_suite_rejects_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "grader.patch").write_text("patch", encoding="utf-8")
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    write_canary(first, canary_id="same")
    write_canary(second, canary_id="same")
    with pytest.raises(CanaryLoadError, match="duplicate"):
        load_suite([first, second])


def test_load_canary_parses_paired_change_metadata(tmp_path: Path) -> None:
    grader = tmp_path / 'grader.patch'
    grader.write_text('patch', encoding='utf-8')
    yaml_path = tmp_path / 'canary.yaml'
    write_canary(yaml_path)
    yaml_path.write_text(yaml_path.read_text(encoding='utf-8') + '\npaired_change:\n  material_dimensions:\n    - AGENT_BINARY\n    - AGENT_SUPPORT\n  max_pair_gap_ms: 5000\n', encoding='utf-8')
    canary = load_canary(yaml_path)
    assert canary.paired_change is not None
    assert canary.paired_change.material_dimensions == ('AGENT_BINARY', 'AGENT_SUPPORT')
    assert canary.paired_change.max_pair_gap_ms == 5000


def _setup_grader(tmp_path: Path) -> None:
    (tmp_path / "grader.patch").write_text("patch", encoding="utf-8")


def test_load_canary_parses_coverage_metadata(tmp_path: Path) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "    context_requirements:\n"
        "      execution.mode: container\n",
        encoding="utf-8",
    )
    canary = load_canary(yaml_path)
    assert len(canary.coverage) == 1
    assert canary.coverage[0].contract_id == "command.execution"
    assert canary.coverage[0].context_requirements == {"execution.mode": "container"}


def test_load_canary_rejects_duplicate_context_requirements_key_with_different_values(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "    context_requirements:\n"
        "      execution.mode: container\n"
        "      execution.mode: linux-host\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="duplicate"):
        load_canary(yaml_path)


def test_load_canary_rejects_duplicate_context_requirements_key_with_same_value(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "    context_requirements:\n"
        "      execution.mode: container\n"
        "      execution.mode: container\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="duplicate"):
        load_canary(yaml_path)


def test_load_canary_rejects_repeated_top_level_coverage_key(tmp_path: Path) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "coverage:\n"
        "  - contract_id: tool.inventory\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="coverage"):
        load_canary(yaml_path)


def test_load_canary_rejects_merge_key_anywhere_in_coverage_enabled_document(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "extra_base: &extra_base\n"
        "  note: unrelated\n"
        "extra:\n"
        "  <<: *extra_base\n"
        "  other: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="merge"):
        load_canary(yaml_path)


def test_load_canary_rejects_merge_injected_coverage_without_literal_root_key(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\nbase: &base\n"
        "  coverage:\n"
        "    - contract_id: command.execution\n"
        "<<: *base\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="coverage"):
        load_canary(yaml_path)


def test_load_canary_rejects_recursive_alias_graph_in_coverage_enabled_document(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        "shared: &shared\n"
        "  nested: *shared\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="alias|shared"):
        load_canary(yaml_path)


def test_load_canary_rejects_nesting_deeper_than_max_depth(tmp_path: Path) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    deep = "1"
    for _ in range(80):
        deep = f"[{deep}]"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        f"deep: {deep}\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="depth"):
        load_canary(yaml_path)


def test_load_canary_rejects_more_than_max_nodes(tmp_path: Path) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    wide = "[" + ", ".join(str(i) for i in range(15_000)) + "]"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "\ncoverage:\n"
        "  - contract_id: command.execution\n"
        f"wide: {wide}\n",
        encoding="utf-8",
    )
    with pytest.raises(CanaryLoadError, match="node"):
        load_canary(yaml_path)


def test_load_canary_keeps_legacy_last_wins_behavior_for_duplicate_unrelated_key(
    tmp_path: Path,
) -> None:
    _setup_grader(tmp_path)
    yaml_path = tmp_path / "canary.yaml"
    write_canary(yaml_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + "\ncritical: false\n",
        encoding="utf-8",
    )
    canary = load_canary(yaml_path)
    assert canary.critical is False
    assert canary.coverage == ()


def test_fresh_interpreter_imports_canary_models_then_cli_without_cycle() -> None:
    repo_src = Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_src) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import qualock.canary.models; import qualock.cli"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
