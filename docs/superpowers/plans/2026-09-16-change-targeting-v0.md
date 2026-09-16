# Change Targeting V0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure, fail-closed Change Targeting V0 planner that maps explicit upstream change signals onto explicit canary coverage, reports READY / NOT_APPLICABLE / INCOMPLETE deterministically, and never treats generic qualification success as proof of coverage.

**Architecture:** Add a focused `qualock.change_targeting` package for contract registry, validated planner models, canonical normalization/digests, tri-state requirement matching, deterministic minimal source selection, safe signal/context loading, rendering, and a side-effect-free command service. Existing canaries gain optional behavioral coverage metadata, but that metadata is deliberately excluded from legacy canary/suite fingerprints so baseline/check/monitor behavior remains unchanged. The CLI only plans; it never calls an agent/provider or mutates baseline/results.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, Typer, existing `qualock.evidence.fingerprint` canonical JSON helpers, pytest, Ruff, MyPy.

**Spec:** `docs/superpowers/specs/2026-09-16-change-targeting-v0-design.md`

## Global Constraints

- No dependency installation.
- No authenticated provider or real-agent qualification is required or permitted for V0 correctness.
- No release-monitor, paired-change, first-bad, Evidence Bundle, baseline-lock, or existing qualification semantic changes.
- Existing canaries without `coverage` remain valid and contribute no targeting coverage.
- Planner signal/context YAML rejects every duplicate mapping key before materialization; canary YAML applies the same rule inside the `coverage` subtree while leaving unrelated legacy YAML behavior unchanged.
- Adding/changing `coverage` metadata must not alter legacy `canary_fingerprint()` or `suite_fingerprint()` values.
- Unknown applicability/coverage fails closed as `INCOMPLETE`; it is never collapsed into `MISMATCH` or `COVERAGE_GAP`.
- `READY` means only “sufficient declared coverage exists to begin qualification”; it is never an upgrade-safety verdict.
- CLI exit `2` is reserved for an actual `NOT_APPLICABLE` assessment; target-change parser/usage failures must exit `3`.
- The planner must be pure and deterministic: no network, provider, agent, binary inspection, historical-result lookup, baseline mutation, or results mutation.
- Root `README.md` and `ROADMAP.md` remain untouched.
- Every implementation task follows red-green TDD and receives fresh independent review; unresolved Critical or Important findings block task closure.
- Do not fix unrelated Ruff/MyPy debt.
- No push / PR / merge / tag / release / publish without explicit user authorization.
- Reuse existing direct dependency `packaging>=24` for version validation; do not install anything.
- Before Task 1, verify `/home/pacmap/qualock-easy/.venv/bin/python --version` succeeds; if that canonical interpreter is unavailable, stop rather than install or silently switch environments.
- No shared four-agent identity alias exists in the current codebase, so V0 intentionally mirrors the same four-value Literal used by `AgentConfig` rather than broadening unrelated agent-model APIs.

## File Structure

Create one focused package:

```text
src/qualock/change_targeting/
  __init__.py       intentionally empty parent API (`__all__ = ()`); no command-layer imports
  contracts.py      small behavioral-contract registry + validation
  errors.py         validated-input/configuration error type
  models.py         V0 input/output models and enums
  canonical.py      duplicate normalization + canonical payload/digest helpers
  yaml_strict.py    reusable pre-materialization duplicate-key validation
  matching.py       exact scalar requirement MATCH/MISMATCH/UNKNOWN
  planner.py        relevance, coverage, unresolved aggregation, set cover
  io.py             bounded YAML input loading with duplicate-key semantics
  commands.py       project-level pure orchestration into the planner
  render.py         stable terminal text only
  cli_support.py    target-change-specific Typer usage-error remapping
```

Tests mirror responsibilities:

```text
tests/unit/test_change_targeting_models.py
tests/unit/test_change_targeting_canonical.py
tests/unit/test_change_targeting_matching.py
tests/unit/test_change_targeting_planner.py
tests/unit/test_change_targeting_io.py
tests/unit/test_change_targeting_commands.py
tests/unit/test_change_targeting_cli.py
tests/integration/test_change_targeting_managed_shell.py
```

Existing files modified only where required:

```text
src/qualock/canary/models.py
src/qualock/canary/loader.py
src/qualock/project.py
src/qualock/cli.py
tests/unit/test_canary_models.py
tests/unit/test_canary_loader.py
tests/unit/test_project_fingerprint.py
```

---

### Task 1: Core contracts, validated V0 models, and canonical digests

**Files:**
- Create: `src/qualock/change_targeting/__init__.py`
- Create: `src/qualock/change_targeting/contracts.py`
- Create: `src/qualock/change_targeting/errors.py`
- Create: `src/qualock/change_targeting/models.py`
- Create: `src/qualock/change_targeting/canonical.py`
- Create: `tests/unit/test_change_targeting_models.py`
- Create: `tests/unit/test_change_targeting_canonical.py`

**Interfaces:**
- Produces: parent `qualock.change_targeting.__all__ == ()`; callers import concrete submodules directly so package initialization never pulls in project/canary/command layers.
- Produces: `BEHAVIORAL_CONTRACTS: frozenset[str]`
- Produces: `validate_contract_id(value: str) -> str`
- Produces: `ChangeTargetingInputError(ValueError)` used for all validated user/config input failures, including contradictory coverage declarations.
- Produces: `ScalarValue = StrictStr | StrictBool | StrictInt | None`
- Produces: `ChangeImpactV0`, `ChangeSignalV0`, `TargetContextV0`, `CoverageDeclarationV0`
- Produces: `AssessmentStatus`, `IncompleteReason`, `UncoveredV0`, `UnresolvedV0`, `CoverageAssessmentV0`
- Produces: `normalize_signal()`, `normalize_context()`, `normalize_coverage()`, `digest_*()` helpers used by Task 3.

- [ ] **Step 1: Write failing registry/model tests**

Create tests that establish the exact public contract:

```python
import pytest
from pydantic import ValidationError

from qualock.change_targeting.contracts import BEHAVIORAL_CONTRACTS
from qualock.change_targeting.models import ChangeSignalV0, TargetContextV0


def test_initial_behavioral_contract_registry_is_small_and_exact() -> None:
    assert BEHAVIORAL_CONTRACTS == frozenset(
        {"command.execution", "tool.inventory", "mcp.visibility", "approval.semantics"}
    )


def test_signal_rejects_unknown_contract() -> None:
    with pytest.raises(ValidationError, match="unknown behavioral contract"):
        ChangeSignalV0.model_validate(
            {
                "schema_version": 0,
                "agent": "codex",
                "baseline_version": "0.149.1",
                "candidate_version": "0.150.1",
                "impacts": [{"contract_id": "made.up", "scope_requirements": {}}],
            }
        )


def test_context_rejects_float_and_nested_values() -> None:
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"x": 1.5}})
    with pytest.raises(ValidationError):
        TargetContextV0.model_validate({"schema_version": 0, "facts": {"x": [1]}})
```

Also cover unsupported schema version, unsupported agent identity, malformed versions, empty impacts, empty `source_id`, empty/non-string context keys, required 64-lowercase-hex digests, assessment-state invariants, unresolved source-ID invariants, forbidden extra fields, and only scalar JSON values (`str`, `bool`, strict `int`, `null`). Include a strict-scalar regression proving boolean `true` and integer `1` remain distinct values. Add `import qualock.change_targeting as ct; assert ct.__all__ == ()`; the parent package must not import `.io`, `.commands`, `.render`, `.cli_support`, or any module that imports `qualock.project`/`qualock.canary`.

- [ ] **Step 2: Run model tests and verify RED**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_models.py -q
```

Expected: collection/import failure because the package does not exist yet.

- [ ] **Step 3: Implement contract registry and Pydantic models minimally**

Use a registry validator rather than a `Literal` for contract IDs so later registry expansion does not require changing every model annotation:

```python
# contracts.py
BEHAVIORAL_CONTRACTS = frozenset(
    {"command.execution", "tool.inventory", "mcp.visibility", "approval.semantics"}
)


def validate_contract_id(value: str) -> str:
    if value not in BEHAVIORAL_CONTRACTS:
        raise ValueError(f"unknown behavioral contract: {value}")
    return value
```

Model shapes must include:

```python
class SourceProvenanceV0(BaseModel):
    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class ChangeImpactV0(BaseModel):
    contract_id: str
    scope_requirements: dict[str, ScalarValue] = Field(default_factory=dict)


class ChangeSignalV0(BaseModel):
    schema_version: Literal[0]
    agent: Literal["codex", "claude", "antigravity", "gemini"]
    baseline_version: str
    candidate_version: str
    impacts: tuple[ChangeImpactV0, ...] = Field(min_length=1)
    source: SourceProvenanceV0 | None = None


class TargetContextV0(BaseModel):
    schema_version: Literal[0]
    facts: dict[str, ScalarValue] = Field(default_factory=dict)


class CoverageDeclarationV0(BaseModel):
    source_id: str = Field(min_length=1)
    source_kind: Literal["canary"] = "canary"
    contract_id: str
    context_requirements: dict[str, ScalarValue] = Field(default_factory=dict)


class AssessmentStatus(str, Enum):
    READY = "READY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"


class IncompleteReason(str, Enum):
    COVERAGE_GAP = "COVERAGE_GAP"
    TARGET_CONTEXT_UNKNOWN = "TARGET_CONTEXT_UNKNOWN"
    COVERAGE_CONTEXT_UNKNOWN = "COVERAGE_CONTEXT_UNKNOWN"


class UncoveredV0(BaseModel):
    contract_id: str
    reason: Literal[IncompleteReason.COVERAGE_GAP] = IncompleteReason.COVERAGE_GAP


class UnresolvedV0(BaseModel):
    contract_id: str
    reason: Literal[
        IncompleteReason.TARGET_CONTEXT_UNKNOWN,
        IncompleteReason.COVERAGE_CONTEXT_UNKNOWN,
    ]
    missing_context_keys: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()


class CoverageAssessmentV0(BaseModel):
    schema_version: Literal[0] = 0
    signal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: AssessmentStatus
    relevant_contracts: tuple[str, ...] = ()
    selected_sources: tuple[str, ...] = ()
    uncovered: tuple[UncoveredV0, ...] = ()
    unresolved: tuple[UnresolvedV0, ...] = ()
```

Implement `src/qualock/change_targeting/__init__.py` as only `__all__: tuple[str, ...] = ()`; do not re-export even leaf helpers from the parent package in V0. All production callers import concrete submodules directly.

Validate every behavioral `contract_id` through `validate_contract_id()`. Validate version strings with `packaging.version.Version` while preserving the original string value. Reject empty/non-string context/requirement keys and use strict scalar types so booleans remain distinct from integers and `1.5`, lists, and objects cannot be coerced into valid facts. All Change Targeting V0 input/output models use `ConfigDict(extra="forbid")`; output models are also frozen where practical so misspelled fields cannot be silently discarded.

Make assessment/output models frozen where practical. Add model-level invariants so impossible outputs fail validation: `READY` requires non-empty relevant contracts and selected sources with empty `uncovered`/`unresolved`; `NOT_APPLICABLE` requires all four companion collections empty; `INCOMPLETE` requires empty `selected_sources` and at least one `uncovered` or `unresolved`. `TARGET_CONTEXT_UNKNOWN` unresolved rows require empty `source_ids`; `COVERAGE_CONTEXT_UNKNOWN` rows require non-empty `source_ids`; `missing_context_keys` must be non-empty on unresolved rows; `relevant_contracts`, `selected_sources`, missing keys, and source IDs are unique lexical tuples; `uncovered` contains at most one row per contract and is lexically ordered; `unresolved` is unique and ordered by `(contract_id, reason)`.

- [ ] **Step 4: Run model tests and verify GREEN**

Run the same targeted test command; expected all model tests PASS.

- [ ] **Step 5: Write failing canonicalization/digest tests**

Tests must prove:

```python
assert normalize_signal(signal_with_duplicate_impacts) == normalize_signal(signal_without_duplicates)
assert digest_signal(left_reordered) == digest_signal(right_reordered)
assert digest_context(context_a) == digest_context(context_same_facts_different_order)
assert digest_coverage(declarations_a) == digest_coverage(declarations_reordered_with_exact_duplicates)
```

Also prove `signal_sha256` changes when inert `source.ref` changes, because the spec hashes the complete normalized signal, and prove `coverage_sha256` hashes **all coverage declarations loaded for the project run**, including declarations unrelated to the current signal. Add a failing case where two declarations share the same normalized `source_id + contract_id + context key` but assert conflicting values; normalization must raise `ChangeTargetingInputError` instead of hashing an ambiguous set. Add a canonical-assessment test that constructs the same `CoverageAssessmentV0` twice and asserts `canonical_assessment_bytes()` emits compact UTF-8 JSON with recursively lexically sorted object keys, canonically ordered arrays, and no trailing newline.

- [ ] **Step 6: Run canonical tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_canonical.py -q
```

Expected: missing canonical helpers.

- [ ] **Step 7: Implement deterministic normalization and digests**

Use existing `qualock.evidence.fingerprint.canonical_json` / `sha256_canonical` rather than creating another JSON encoder. Normalize exact duplicate impacts/declarations away, raise `ChangeTargetingInputError` for conflicting coverage assertions on the same normalized `source_id + contract_id + context key`, sort requirement/fact maps by key for payload generation, and sort records with stable tuples:

```python
impact_sort_key = (impact.contract_id, canonical_requirements_bytes)
coverage_sort_key = (
    declaration.source_kind,
    declaration.source_id,
    declaration.contract_id,
    canonical_requirements_bytes,
)
```

The canonical helper signatures and concrete digest composition are:

```python
def normalize_signal(signal: ChangeSignalV0) -> ChangeSignalV0:
    by_key: dict[tuple[str, bytes], ChangeImpactV0] = {}
    for impact in signal.impacts:
        requirements = dict(sorted(impact.scope_requirements.items()))
        normalized = impact.model_copy(update={"scope_requirements": requirements})
        key = (normalized.contract_id, canonical_json(requirements))
        by_key.setdefault(key, normalized)
    return signal.model_copy(update={"impacts": tuple(by_key[key] for key in sorted(by_key))})


def normalize_context(context: TargetContextV0) -> TargetContextV0:
    return context.model_copy(update={"facts": dict(sorted(context.facts.items()))})


def normalize_coverage(
    declarations: Sequence[CoverageDeclarationV0],
) -> tuple[CoverageDeclarationV0, ...]:
    asserted: dict[tuple[str, str, str, str], ScalarValue] = {}
    by_key: dict[tuple[str, str, str, bytes], CoverageDeclarationV0] = {}
    for declaration in declarations:
        requirements = dict(sorted(declaration.context_requirements.items()))
        normalized = declaration.model_copy(update={"context_requirements": requirements})
        for context_key, value in requirements.items():
            assertion_key = (
                normalized.source_kind,
                normalized.source_id,
                normalized.contract_id,
                context_key,
            )
            if (
                assertion_key in asserted
                and canonical_json(asserted[assertion_key]) != canonical_json(value)
            ):
                raise ChangeTargetingInputError("conflicting coverage declaration")
            asserted[assertion_key] = value
        key = (
            normalized.source_kind,
            normalized.source_id,
            normalized.contract_id,
            canonical_json(requirements),
        )
        by_key.setdefault(key, normalized)
    return tuple(by_key[key] for key in sorted(by_key))


def digest_signal(signal: ChangeSignalV0) -> str:
    return sha256_canonical(normalize_signal(signal).model_dump(mode="json"))


def digest_context(context: TargetContextV0) -> str:
    return sha256_canonical(normalize_context(context).model_dump(mode="json"))


def digest_coverage(declarations: Sequence[CoverageDeclarationV0]) -> str:
    payload = [item.model_dump(mode="json") for item in normalize_coverage(declarations)]
    return sha256_canonical(payload)


def canonical_assessment_bytes(assessment: CoverageAssessmentV0) -> bytes:
    return canonical_json(assessment.model_dump(mode="json"))
```

Do not use `CoverageAssessmentV0.model_dump_json()` as the canonical byte format; Pydantic preserves declaration order rather than the spec-required lexical object-key order.

Import `canonical_json` and `sha256_canonical` from `qualock.evidence.fingerprint`, and `ChangeTargetingInputError` from `errors.py`. Keep contradiction detection here so Task 4 and future callers share one fail-closed implementation. The normalized coverage sort/dedup tuple includes `source_kind` even though V0 currently permits only `canary`, preserving the declared extension boundary.

- [ ] **Step 8: Run Task 1 tests and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_change_targeting_models.py \
  tests/unit/test_change_targeting_canonical.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/change_targeting \
  tests/unit/test_change_targeting_models.py \
  tests/unit/test_change_targeting_canonical.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock/change_targeting
```

Expected: targeted tests PASS; no new Ruff/MyPy errors.

- [ ] **Step 9: Fresh independent review, resolve blockers, then commit**

Review Task 1 diff against the canonical spec. Critical/Important findings must be fixed and re-reviewed. Then:

```bash
git add src/qualock/change_targeting tests/unit/test_change_targeting_models.py tests/unit/test_change_targeting_canonical.py
git commit -m "feat: add change targeting models"
```

### Task 2: Canary coverage metadata, strict YAML opt-in, and legacy fingerprint compatibility

**Files:**
- Modify: `src/qualock/canary/models.py`
- Modify: `src/qualock/canary/loader.py`
- Create: `src/qualock/change_targeting/yaml_strict.py`
- Modify: `src/qualock/project.py`
- Modify: `tests/unit/test_canary_models.py`
- Modify: `tests/unit/test_canary_loader.py`
- Modify: `tests/unit/test_project_fingerprint.py`

**Interfaces:**
- Consumes directly from submodules: `qualock.change_targeting.contracts.validate_contract_id` and `qualock.change_targeting.models.ScalarValue`; do not import these through package `__init__`, avoiding partial-import cycles with `project -> canary.models`.
- Produces: `CanaryCoverageSpec` with `ConfigDict(extra="forbid")`.
- Produces: optional `CanarySpec.coverage: tuple[CanaryCoverageSpec, ...]`.
- Produces: strict YAML-node validation helpers later reused by Task 4.
- Preserves: legacy canaries without `coverage` keep existing load behavior; targeting-only `coverage` is excluded from `canary_fingerprint()` / `suite_fingerprint()` just like `paired_change`.

- [ ] **Step 1: Write failing canary coverage-model tests**

Add exact tests before implementation:

```python
def test_coverage_metadata_is_optional_for_legacy_canary(tmp_path: Path) -> None:
    canary = CanarySpec.model_validate(valid_data(tmp_path))
    assert canary.coverage == ()


def test_coverage_rejects_unknown_extra_field_that_could_make_scope_unconditional(
    tmp_path: Path,
) -> None:
    data = valid_data(tmp_path)
    data["coverage"] = [
        {
            "contract_id": "command.execution",
            "context_requirement": {"execution.mode": "container"},
        }
    ]
    with pytest.raises(ValidationError, match="context_requirement"):
        CanarySpec.model_validate(data)
```

Also add a valid explicit `context_requirements` case and rejection cases for unknown contracts, floats, lists, nested objects, empty keys, and unknown extra fields. Prove two individually valid declarations for the same contract can be represented; cross-declaration conflict detection remains centralized in Task 1 after `source_id=canary.id` is attached.

- [ ] **Step 2: Run canary model tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_canary_models.py -q
```

Expected: `coverage`/`CanaryCoverageSpec` do not exist yet.

- [ ] **Step 3: Implement `CanaryCoverageSpec` and optional coverage tuple minimally**

Use direct submodule imports and forbid extras:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator

from qualock.change_targeting.contracts import validate_contract_id
from qualock.change_targeting.models import ScalarValue


class CanaryCoverageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    context_requirements: dict[str, ScalarValue] = Field(default_factory=dict)

    @field_validator("contract_id")
    @classmethod
    def validate_contract(cls, value: str) -> str:
        return validate_contract_id(value)


# Add directly after existing paired_change in CanarySpec.
coverage: tuple[CanaryCoverageSpec, ...] = ()
```

Reuse the Task 1 scalar/key validators instead of creating coercing variants. Do not add a second cross-declaration contradiction algorithm here. Import only from `qualock.change_targeting.contracts` and `qualock.change_targeting.models`, never from the parent package; parent `__init__.py` stays empty throughout V0.

- [ ] **Step 4: Run model tests and verify GREEN**

Run the Step 2 command; expected all canary-model tests PASS.

- [ ] **Step 5: Write failing strict-YAML loader tests before any parser implementation**

Add loader cases for a canary that **opts into `coverage`**:

```yaml
coverage:
  - contract_id: command.execution
    context_requirements:
      execution.mode: container
```

Then add separate failing cases proving `CanaryLoadError` for:

- the same `context_requirements` key repeated with different values;
- the same key repeated with the same value;
- repeated top-level `coverage` keys;
- YAML merge key `<<` anywhere in a coverage-enabled document;
- a root-level merge/anchor that would materialize `coverage` without a literal root `coverage` key;
- a recursive/shared alias graph in a coverage-enabled document;
- nesting deeper than `STRICT_YAML_MAX_DEPTH`;
- more than `STRICT_YAML_MAX_NODES` nodes.

Add one backward-compatibility control: a legacy canary **without** `coverage` and with a duplicate unrelated legacy key keeps the current last-wins `yaml.safe_load()` behavior. This scopes strictness to the new metadata opt-in and does not retroactively reject historical canaries. Add a fresh-interpreter subprocess regression that imports `qualock.canary.models` and then `qualock.cli`; both imports must succeed, protecting the empty-parent-package rule from circular-import regressions.

- [ ] **Step 6: Run loader tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_canary_loader.py -q
```

Expected: new duplicate/merge/alias/depth tests fail because the loader currently calls `yaml.safe_load()` directly.

- [ ] **Step 7: Implement bounded strict YAML-node validation and coverage-enabled loader hook**

Create `yaml_strict.py` with explicit limits:

```python
STRICT_YAML_MAX_DEPTH = 64
STRICT_YAML_MAX_NODES = 10_000


class StrictYamlError(ValueError):
    pass
```

Implement an **iterative** node-graph validator over `yaml.nodes.Node` so validation itself cannot recurse forever. Required behavior:

1. maintain `seen_node_ids`; encountering the same node object twice is an alias/shared-node error;
2. maintain `(node, depth)` stack; `depth > 64` is an error;
3. increment node count; `> 10_000` is an error;
4. for each `MappingNode`, every key must be a scalar string key;
5. reject merge keys by tag `tag:yaml.org,2002:merge` or literal key `<<`;
6. reject every duplicate mapping key before materialization, even when values are identical;
7. reject unsupported/custom key tags rather than coercing them.

Catch `yaml.YAMLError` and `RecursionError` from `yaml.compose()` and convert them to `StrictYamlError`.

In `canary.loader`:

- compose the raw text once; use a bounded iterative detection pass over the **root mapping and only the merge/anchor graph reachable from root merge keys** to determine whether `coverage` is declared directly or could be injected by a merge. The detection pass uses the same depth/node/seen-node safeguards and never materializes YAML;
- if neither a literal root `coverage` key nor merge-reachable `coverage` exists, preserve the existing `yaml.safe_load()` path unchanged for genuinely legacy documents;
- if a literal root `coverage` key exists, strict-validate the **whole document** before `safe_load()`; because coverage is new opt-in metadata, fail-closed strictness is allowed for that document;
- if `coverage` is only merge-reachable, reject with `CanaryLoadError` — V0 requires coverage metadata to be an explicit literal root field and does not accept merge-injected coverage;
- if more than one top-level `coverage` key exists, reject before materialization;
- wrap `StrictYamlError` as `CanaryLoadError`.

- [ ] **Step 8: Run strict loader tests and verify GREEN**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_canary_models.py tests/unit/test_canary_loader.py -q
```

Expected: all PASS, including the legacy-without-coverage compatibility control.

- [ ] **Step 9: Write failing fingerprint-compatibility test**

```python
def test_coverage_metadata_does_not_change_legacy_canary_or_suite_fingerprint(
    tmp_path: Path,
) -> None:
    left = tmp_path / "legacy"
    right = tmp_path / "covered"
    left.mkdir()
    right.mkdir()
    legacy = make_canary(left, "same")
    covered = make_canary(
        right,
        "same",
        coverage=[
            {
                "contract_id": "command.execution",
                "context_requirements": {"execution.mode": "container"},
            }
        ],
    )
    assert canary_fingerprint(legacy) == canary_fingerprint(covered)
    assert suite_fingerprint([legacy]) == suite_fingerprint([covered])
```

Before changing `project.py`, build `expected_legacy` in the test from the historical fingerprint payload shape directly: `legacy.model_dump(mode="json", exclude={"paired_change", "coverage"})`, then apply the existing grader `patch_sha256` substitution exactly as the pre-V0 helper does and hash it with `sha256_canonical`. After the production change assert both `canary_fingerprint(legacy)` and `canary_fingerprint(covered)` equal that independently constructed digest. Do **not** compute `expected_legacy` by calling `_canary_fingerprint_payload()` after the change; that would only re-test the implementation against itself.

- [ ] **Step 10: Run fingerprint test and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_project_fingerprint.py::test_coverage_metadata_does_not_change_legacy_canary_or_suite_fingerprint -q
```

Expected: FAIL because `_canary_fingerprint_payload()` currently excludes `paired_change` only.

- [ ] **Step 11: Exclude targeting-only metadata from legacy fingerprint payload**

Change exactly this compatibility boundary:

```python
item = canary.model_dump(mode="json")
item.pop("paired_change", None)
item.pop("coverage", None)
```

Leave grader-path replacement, grader-byte hashing, and every other fingerprint input unchanged.

- [ ] **Step 12: Run Task 2 tests and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_canary_models.py \
  tests/unit/test_canary_loader.py \
  tests/unit/test_project_fingerprint.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/canary/models.py src/qualock/canary/loader.py \
  src/qualock/change_targeting/yaml_strict.py src/qualock/project.py \
  tests/unit/test_canary_models.py tests/unit/test_canary_loader.py \
  tests/unit/test_project_fingerprint.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy \
  src/qualock/canary/models.py src/qualock/canary/loader.py \
  src/qualock/change_targeting/yaml_strict.py src/qualock/project.py
```

- [ ] **Step 13: Fresh independent review, resolve blockers, then commit**

```bash
git add src/qualock/canary/models.py src/qualock/canary/loader.py \
  src/qualock/change_targeting/yaml_strict.py src/qualock/project.py \
  tests/unit/test_canary_models.py tests/unit/test_canary_loader.py \
  tests/unit/test_project_fingerprint.py
git commit -m "feat: declare behavioral canary coverage"
```

---

### Task 3: Tri-state requirement matching and deterministic coverage planner

**Files:**
- Create: `src/qualock/change_targeting/matching.py`
- Create: `src/qualock/change_targeting/planner.py`
- Create: `tests/unit/test_change_targeting_matching.py`
- Create: `tests/unit/test_change_targeting_planner.py`

**Interfaces:**
- Consumes Task 1 models/digests.
- Produces: `RequirementMatch` enum with `MATCH`, `MISMATCH`, `UNKNOWN`.
- Produces: `match_requirements(requirements, context) -> RequirementMatchResult` including exact missing keys.
- Produces: `assess_change(signal, target_context, declarations) -> CoverageAssessmentV0`.

- [ ] **Step 1: Write exhaustive failing requirement-map tests**

Pin the map rule exactly:

```python
@pytest.mark.parametrize(
    ("requirements", "facts", "status", "missing"),
    [
        ({}, {}, RequirementMatch.MATCH, ()),
        ({"a": True}, {"a": True}, RequirementMatch.MATCH, ()),
        ({"a": True}, {"a": False}, RequirementMatch.MISMATCH, ()),
        ({"a": True}, {}, RequirementMatch.UNKNOWN, ("a",)),
        (
            {"a": True, "b": 1},
            {"a": False},
            RequirementMatch.MISMATCH,
            (),
        ),
        (
            {"a": True, "b": 1},
            {"a": True},
            RequirementMatch.UNKNOWN,
            ("b",),
        ),
    ],
)
def test_requirement_matching(requirements, facts, status, missing):
    context = TargetContextV0(schema_version=0, facts=facts)
    result = match_requirements(requirements, context)
    assert result.status is status
    assert result.missing_context_keys == missing
```

The `MISMATCH` case wins over missing keys in the same requirement map, exactly as the spec states. Add strict-scalar cases where required `True` versus observed `1`, and required `1` versus observed `True`, both yield `MISMATCH`; implement equality as same scalar type plus equal value (or canonical-JSON equality), never bare Python `==`.

- [ ] **Step 2: Run matcher tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_matching.py -q
```

- [ ] **Step 3: Implement `matching.py` minimally**

Use exact scalar equality only. No regex, coercion, ranges, expressions, or truthiness matching.

- [ ] **Step 4: Run matcher tests and verify GREEN**

Run the same command; expected PASS.

- [ ] **Step 5: Write failing planner tests for every spec state**

At minimum cover:

```text
MATCH + compatible coverage                    -> READY
all impacts MISMATCH                           -> NOT_APPLICABLE
MATCH + no declaration                         -> INCOMPLETE/COVERAGE_GAP
MATCH + only MISMATCH declarations             -> INCOMPLETE/COVERAGE_GAP
MATCH + UNKNOWN declaration, no MATCH          -> INCOMPLETE/COVERAGE_CONTEXT_UNKNOWN
MATCH + MISMATCH + UNKNOWN declarations        -> INCOMPLETE/COVERAGE_CONTEXT_UNKNOWN
UNKNOWN signal relevance                       -> INCOMPLETE/TARGET_CONTEXT_UNKNOWN
MATCH sibling + UNKNOWN same contract           -> INCOMPLETE, retain unresolved UNKNOWN
```

Assert `uncovered` only contains proven relevant gaps and `unresolved` only contains unknown applicability/coverage. Assert missing keys and source IDs are sorted and duplicate unresolved rows aggregate by set union.

- [ ] **Step 6: Add failing minimal-source selection tests**

Use the spec example:

```python
# A covers command.execution, B covers tool.inventory, C covers both.
assessment = assess_change(signal, context, declarations)
assert assessment.status is AssessmentStatus.READY
assert assessment.selected_sources == ("canary-c",)
```

Add equal-cardinality candidates and require lexical tuple tie-break. Assert canonical ordering of `relevant_contracts`, `selected_sources`, `uncovered`, and `unresolved`, and prove two semantically equivalent reordered input sets produce byte-identical `canonical_assessment_bytes()` output. Never use `model_dump_json()` as the canonical-wire assertion.

Add the exact managed-scope applicability case from the spec: a `command.execution` impact requiring `codex.managed.unified_exec=false` evaluated against target context with `codex.managed.unified_exec=true` must yield `NOT_APPLICABLE` with all companion collections empty.

- [ ] **Step 7: Run planner/selection tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_planner.py -q
```

Expected: planner/selection tests fail because `planner.py` / `assess_change()` is not implemented yet. Preserve this RED output in the task report.

- [ ] **Step 8: Implement planner with pure exhaustive set cover**

Planner order:

```text
1. normalize inputs
2. compute signal/context/coverage digests
3. classify every impact relevance
4. if any impact UNKNOWN, record TARGET_CONTEXT_UNKNOWN
5. for MATCH contracts, classify declarations
6. record COVERAGE_CONTEXT_UNKNOWN or COVERAGE_GAP as specified
7. if unresolved/gaps exist -> INCOMPLETE
8. if all impacts MISMATCH -> NOT_APPLICABLE
9. otherwise choose deterministic minimum source set -> READY
```

For selection, enumerate source combinations from cardinality `1..N`, stop at the first cardinality with a covering solution, and choose the lexicographically smallest sorted tuple. Expected V0 source counts are intentionally small.

- [ ] **Step 9: Run Task 3 tests and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_change_targeting_matching.py \
  tests/unit/test_change_targeting_planner.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/change_targeting/matching.py \
  src/qualock/change_targeting/planner.py \
  tests/unit/test_change_targeting_matching.py \
  tests/unit/test_change_targeting_planner.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock/change_targeting
```

- [ ] **Step 10: Fresh independent review, resolve blockers, then commit**

```bash
git add src/qualock/change_targeting/matching.py src/qualock/change_targeting/planner.py \
  tests/unit/test_change_targeting_matching.py tests/unit/test_change_targeting_planner.py
git commit -m "feat: assess targeted behavioral coverage"
```

---

### Task 4: Bounded signal/context I/O and project-level planning command

**Files:**
- Create: `src/qualock/change_targeting/io.py`
- Create: `src/qualock/change_targeting/commands.py`
- Create: `tests/unit/test_change_targeting_io.py`
- Create: `tests/unit/test_change_targeting_commands.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 canary coverage, Task 3 planner.
- Consumes: `ChangeTargetingInputError` and the strict YAML-node helpers created in Tasks 1-2.
- Produces: `load_change_signal(path: Path) -> ChangeSignalV0`.
- Produces: `load_target_context(path: Path) -> TargetContextV0`.
- Produces: `coverage_declarations(canaries: Sequence[CanarySpec]) -> tuple[CoverageDeclarationV0, ...]`.
- Produces: `execute_target_change(root: Path, signal_path: Path, context_path: Path) -> CoverageAssessmentV0`.

- [ ] **Step 1: Write failing bounded-input and duplicate-key tests**

Use a 1 MiB input ceiling for each explicit planner file:

```python
PLANNER_INPUT_MAX_BYTES = 1024 * 1024
```

Tests must prove:

- missing file -> `ChangeTargetingInputError`;
- payload larger than limit -> error before YAML model validation;
- malformed YAML -> error;
- non-mapping top level -> error;
- any duplicate mapping key -> error, including two occurrences with the same scalar value;
- `true` versus `1` on a duplicate key is also rejected (the duplicate itself is sufficient);
- nested requirement-map duplicates are rejected identically;
- YAML merge key `<<` is rejected before materialization;
- shared/recursive aliases are rejected rather than traversed indefinitely;
- depth/node limits from `yaml_strict.py` fail closed;
- non-string mapping keys are rejected rather than coerced;
- unknown contracts / schema errors surface as `ChangeTargetingInputError` with no partial model.

Do not use `yaml.safe_load()` directly for planner inputs before strict validation. Reuse Task 2 `yaml_strict` to reject duplicate keys, merge keys, aliases/shared-node graphs, excessive depth/node counts, and invalid mapping keys across the entire signal/context document before materialization; only then call safe YAML construction and Pydantic validation.

- [ ] **Step 2: Run I/O tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_io.py -q
```

- [ ] **Step 3: Implement bounded inert YAML loading**

I/O must only read bytes, parse YAML, and validate models. It must never follow `source.ref`, open URLs, execute commands, or import dynamic handlers.

Read at most `PLANNER_INPUT_MAX_BYTES + 1` bytes and reject oversize content deterministically.

- [ ] **Step 4: Run I/O tests and verify GREEN**

Run the same test command; expected PASS.

- [ ] **Step 5: Write failing project-command tests**

Construct project canaries in-memory/temporary files and prove declaration conversion:

```python
assert coverage_declarations(canaries) == (
    CoverageDeclarationV0(
        source_id="canary-a",
        source_kind="canary",
        contract_id="command.execution",
        context_requirements={"execution.mode": "container"},
    ),
)
```

Add command tests where one canary contains two declarations for the same contract/key with conflicting values; after `source_id=canary.id` is attached, `coverage_declarations()` must fail closed through shared `normalize_coverage()` by raising `ChangeTargetingInputError`, never a bare `ValueError`. This is a hard invalid-input/configuration condition for the CLI and must ultimately map to exit `3`.

Then monkeypatch loaders/planner to establish the command call graph. Add a guard:

```python
def forbidden(*args, **kwargs):
    raise AssertionError("target-change must not execute providers or qualification")
```

Monkeypatch known execution entry points (`qualock.commands.execute_check`, runner/provider helpers if imported) and confirm `execute_target_change()` never touches them.

- [ ] **Step 6: Run project-command tests and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_commands.py -q
```

Expected: tests fail because `coverage_declarations()` / `execute_target_change()` is not implemented yet. Preserve this RED output in the task report.

- [ ] **Step 7: Implement `coverage_declarations()` and `execute_target_change()`**

Command flow is exactly:

```python
def execute_target_change(root: Path, signal_path: Path, context_path: Path) -> CoverageAssessmentV0:
    signal = load_change_signal(signal_path)
    context = load_target_context(context_path)
    _config, canaries = load_project(root)
    declarations = coverage_declarations(canaries)
    return assess_change(signal, context, declarations)
```

`coverage_declarations()` must include **all declarations from all loaded project canaries**, sorted canonically. This is the “complete normalized coverage-declaration set” bound into `coverage_sha256`; unrelated declarations therefore intentionally affect the coverage digest even if they do not affect the decision.

- [ ] **Step 8: Run Task 4 tests and static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_change_targeting_io.py \
  tests/unit/test_change_targeting_commands.py -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/change_targeting/io.py src/qualock/change_targeting/commands.py \
  tests/unit/test_change_targeting_io.py tests/unit/test_change_targeting_commands.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock/change_targeting
```

- [ ] **Step 9: Fresh independent review, resolve blockers, then commit**

```bash
git add src/qualock/change_targeting/io.py src/qualock/change_targeting/commands.py \
  tests/unit/test_change_targeting_io.py tests/unit/test_change_targeting_commands.py
git commit -m "feat: load change targeting inputs"
```

---

### Task 5: Stable terminal rendering and fail-closed CLI exit semantics

**Files:**
- Create: `src/qualock/change_targeting/render.py`
- Create: `src/qualock/change_targeting/cli_support.py`
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_change_targeting_cli.py`

**Interfaces:**
- Consumes: `execute_target_change()` and `CoverageAssessmentV0`.
- Produces: `render_coverage_assessment(assessment: CoverageAssessmentV0) -> str`.
- Produces: `_InputErrorExit3TyperCommand(TyperCommand)` whose `parse_args()` remaps command-local `click.UsageError` to exit `3`.
- Produces CLI: `qualock target-change SIGNAL --context CONTEXT`.
- Exit contract: `READY=0`, `NOT_APPLICABLE=2`, `INCOMPLETE=4`, invalid input/config/parser usage=`3`, unexpected operational error=`1`.
- Scope note: root/group-level Click errors that occur **before** dispatch to `target-change` retain existing application behavior; only the new command's own parser/usage path is remapped. Exit `2` is reserved within the `target-change` command path for an actual `NOT_APPLICABLE` assessment.

- [ ] **Step 1: Write all failing CLI/render/fail-open tests before implementation**

Use `CliRunner`. First pin READY/NOT_APPLICABLE/INCOMPLETE exits by monkeypatching `qualock.cli.execute_target_change`:

```python
result = runner.invoke(
    app,
    ["target-change", "signal.yaml", "--context", "context.yaml"],
)
assert result.exit_code == 0
```

Repeat with assessment fixtures for exit `2` and `4`.

Pin command-local parser failures:

```python
assert runner.invoke(app, ["target-change", "signal.yaml"]).exit_code == 3
assert runner.invoke(
    app,
    ["target-change", "signal.yaml", "--context", "context.yaml", "--unknown"],
).exit_code == 3
```

Add a real invalid-signal test, not a monkeypatched service test: write a signal with `contract_id: made.up` plus a valid context file, invoke `target-change`, and assert exit `3`. Loading the signal must fail before project/provider work.

Add a real contradictory-canary-coverage project case: one canary declares two `command.execution` rows with the same context key and conflicting values. Supply valid signal/context files, invoke `target-change`, and assert exit `3`, proving `ChangeTargetingInputError` reaches the CLI boundary rather than becoming operational exit `1`.

Before invocation, snapshot all regular files under the temporary project. After each successful planner-path invocation, assert no baseline/result file was created or mutated. Monkeypatch provider-facing entry points (`qualock.commands.execute_check`, agent resolvers, and any provider runner constructor reachable from `cli.py`) to raise `AssertionError` if touched. Also use a signal with an inert `https://` `source.ref` and monkeypatch `httpx.get`, `httpx.Client`, plus any repository HTTP helper reachable from the path to raise if called; `target-change` must never dereference provenance URLs.

- [ ] **Step 2: Write exact stable-render tests before implementation**

Use full 64-character digest fixtures and require plain text independent of Rich width:

```text
QuaLock Change Targeting

Status: READY
Signal SHA256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Target context SHA256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Coverage SHA256: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
Relevant contracts: command.execution
Selected sources: managed-shell-probe
Coverage gaps: none
Unresolved: none
```

For `INCOMPLETE`, render every `uncovered` and `unresolved` row on deterministic sorted lines, including missing keys and source IDs. For `NOT_APPLICABLE`, all companion collections render as `none`.

- [ ] **Step 3: Run the new CLI test file and verify RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_change_targeting_cli.py -q
```

Expected: collection/command/render failures because none of Task 5 exists yet. Keep this RED output in the task report.

- [ ] **Step 4: Implement renderer, usage-error remapper, and CLI command minimally**

`cli_support.py`:

```python
import click
from typer.core import TyperCommand


class _InputErrorExit3TyperCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as exc:
            exc.exit_code = 3
            raise
```

`cli.py` command shape:

```python
@app.command("target-change", cls=_InputErrorExit3TyperCommand)
def target_change_command(
    signal: Path = typer.Argument(..., metavar="SIGNAL"),
    context: Path = typer.Option(..., "--context", metavar="CONTEXT"),
) -> None:
    try:
        assessment = execute_target_change(Path.cwd(), signal, context)
    except (ChangeTargetingInputError, ConfigError, CanaryLoadError, FileNotFoundError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except Exception:
        console.print("change targeting failed", markup=False)
        raise typer.Exit(1) from None

    typer.echo(render_coverage_assessment(assessment), nl=False)
    if assessment.status is AssessmentStatus.NOT_APPLICABLE:
        raise typer.Exit(2)
    if assessment.status is AssessmentStatus.INCOMPLETE:
        raise typer.Exit(4)
```

Use `typer.echo(..., nl=False)` intentionally instead of the repo's usual `console.print`: the renderer already returns final plain text, and avoiding Rich wrapping is required for a stable CI presentation contract. Do not print unexpected exception details.

- [ ] **Step 5: Run Task 5 tests and verify GREEN**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_change_targeting_cli.py tests/unit/test_cli.py -q
```

Expected: all Task 5 and pre-existing CLI tests PASS. Confirm the no-provider/no-write guards stayed armed during the real target-change cases.

- [ ] **Step 6: Run Task 5 static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check \
  src/qualock/change_targeting/render.py \
  src/qualock/change_targeting/cli_support.py \
  src/qualock/cli.py tests/unit/test_change_targeting_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m mypy \
  src/qualock/change_targeting/render.py \
  src/qualock/change_targeting/cli_support.py \
  src/qualock/cli.py
```

- [ ] **Step 7: Fresh independent review, resolve blockers, then commit**

```bash
git add src/qualock/change_targeting/render.py \
  src/qualock/change_targeting/cli_support.py \
  src/qualock/cli.py tests/unit/test_change_targeting_cli.py
git commit -m "feat: expose change targeting planner"
```

---

### Task 6: Historical managed-shell acceptance and full V0 verification

**Files:**
- Create: `tests/integration/test_change_targeting_managed_shell.py`
- Modify only if test evidence requires a V0 bug fix: files from Tasks 1-5, with red-green proof and fresh review for every fix.

**Interfaces:**
- Consumes the public V0 command/planner surface completed by Tasks 1-5.
- Proves the market/reliability spike's central false-confidence regression cannot recur.

- [ ] **Step 1: Write historical acceptance test**

Use the real bundled Click sentinel as legacy coverage input:

```python
click_path = repo_root / "benchmarks/oss-smoke/click-sentinel.yaml"
canary = load_canary(click_path)
assert canary.coverage == ()
```

Build the historical signal/context:

```python
signal = ChangeSignalV0.model_validate(
    {
        "schema_version": 0,
        "agent": "codex",
        "baseline_version": "0.149.1",
        "candidate_version": "0.150.1",
        "impacts": [
            {
                "contract_id": "command.execution",
                "scope_requirements": {
                    "codex.managed.shell_tool": True,
                    "codex.managed.unified_exec": False,
                },
            }
        ],
    }
)
context = TargetContextV0.model_validate(
    {
        "schema_version": 0,
        "facts": {
            "codex.managed.shell_tool": True,
            "codex.managed.unified_exec": False,
        },
    }
)
```

With only declarations derived from the legacy Click canary, require:

```python
assert assessment.status is AssessmentStatus.INCOMPLETE
assert assessment.relevant_contracts == ("command.execution",)
assert assessment.selected_sources == ()
assert [(item.contract_id, item.reason) for item in assessment.uncovered] == [
    ("command.execution", IncompleteReason.COVERAGE_GAP)
]
assert assessment.unresolved == ()
```

This test must not run Codex, Docker, network, or authenticated provider calls.

- [ ] **Step 2: Run historical acceptance test**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/integration/test_change_targeting_managed_shell.py -q
```

If it fails, treat the failure as a V0 implementation bug: retain the failing assertion, apply the minimal fix, rerun RED/GREEN, and fresh-review the fix before proceeding.

- [ ] **Step 3: Add positive dedicated-coverage acceptance**

Create an explicit dedicated declaration:

```python
CoverageDeclarationV0(
    source_id="managed-shell-registration-probe",
    source_kind="canary",
    contract_id="command.execution",
    context_requirements={
        "codex.managed.shell_tool": True,
        "codex.managed.unified_exec": False,
    },
)
```

Require the same signal/context to become `READY` and select only this source.

- [ ] **Step 4: Run complete targeted V0 suite**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_change_targeting_models.py \
  tests/unit/test_change_targeting_canonical.py \
  tests/unit/test_change_targeting_matching.py \
  tests/unit/test_change_targeting_planner.py \
  tests/unit/test_change_targeting_io.py \
  tests/unit/test_change_targeting_commands.py \
  tests/unit/test_change_targeting_cli.py \
  tests/unit/test_canary_models.py \
  tests/unit/test_canary_loader.py \
  tests/unit/test_project_fingerprint.py \
  tests/integration/test_change_targeting_managed_shell.py -q
```

Expected: all PASS.

- [ ] **Step 5: Run repository-wide regression verification**

No dependency installation. Use the existing canonical environment:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m ruff check src tests
/home/pacmap/qualock-easy/.venv/bin/python -m mypy src/qualock
git diff --check
```

For Ruff/MyPy, distinguish pre-existing unrelated debt from new V0 regressions; do not repair unrelated findings.

- [ ] **Step 6: Fresh whole-branch independent review**

Review the entire implementation against `docs/superpowers/specs/2026-09-16-change-targeting-v0-design.md`, with special attention to:

- false implicit coverage;
- UNKNOWN fail-open paths;
- digest determinism;
- duplicate-key parsing;
- legacy fingerprint compatibility;
- CLI exit-code fail-open hazards;
- provider/network execution leakage;
- scope creep into monitor/qualification.

Critical/Important findings block closure and require a fix + scoped re-review.

- [ ] **Step 7: Commit acceptance/verification changes only after clean review**

```bash
git add tests/integration/test_change_targeting_managed_shell.py
# Include implementation files only if Step 2/5 required a reviewed V0 fix.
git commit -m "test: lock change targeting coverage gap"
```

## Completion Gate

Before declaring V0 complete, verify all of the following from fresh command output:

```text
- Spec remains the authority and is unchanged unless separately reviewed.
- Every Task 1-6 commit exists locally.
- Worktree is clean.
- Historical managed-shell legacy suite => INCOMPLETE/COVERAGE_GAP.
- Explicit compatible declaration => READY.
- READY=0, NOT_APPLICABLE=2, INCOMPLETE=4.
- coverage metadata does not change legacy canary/suite fingerprints.
- No provider/network execution occurs in planner paths.
- Full appropriate pytest verification passes.
- No new Ruff/MyPy errors attributable to V0.
- git diff --check is clean.
- Fresh whole-branch reviewer reports zero unresolved Critical/Important findings.
```

Do not push, open a PR, merge, tag, release, or publish as part of this plan.
