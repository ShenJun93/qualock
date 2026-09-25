"""The behavioural-record schema validates its own examples and rejects its fixtures.

Asserting only "the bad fixtures fail" is too weak: most of them now trip several rules at
once, so a regression in the one rule a fixture exists to pin would stay green. Each rejection
therefore asserts the diagnostic that fixture is named after.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
EXAMPLES = sorted((SCHEMA_DIR / "examples").glob("*.json"))
REJECTIONS = sorted((SCHEMA_DIR / "tests").glob("bad-*.json"))
WARNINGS = sorted((SCHEMA_DIR / "tests").glob("warn-*.json"))

# Each fixture must emit the diagnostic it is named for, not merely some diagnostic.
EXPECTED_DIAGNOSTIC = {
    "bad-attempt-count-mismatch": "but perVariant lists",
    "bad-bisect-without-firstbad": "requires observation.firstBad",
    "bad-bisect-without-variants": "requires variants[]",
    "bad-firstbad-contradicts-observations": "shows the behaviour present",
    "bad-image-ref-not-digest": "containerImageDigest",
    "bad-missing-claimscope": "missing required field 'claimScope'",
    "bad-paired-without-canaries": "requires 'canaries'",
    "bad-truncated-hash": "subject.hashes[0].content",
    "bad-undeclared-canary": "references canaries not declared",
    "bad-verdict-not-in-vocabulary": "observation.verdict",
    "warn-single-attempt-block": "consider INCOMPLETE",
    "warn-undisclosed-seccomp": "seccomp is unconfined",
}


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qualock_schema_validate", SCHEMA_DIR / "validate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = _load_validator()


def diagnose(path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one record, mirroring what the CLI prints."""
    import json

    root = json.loads(validate.SCHEMA.read_text())
    record = json.loads(path.read_text())
    errors: list[str] = []
    warnings: list[str] = []
    validate.check(record, root, root, "$", errors)
    validate.conditionals(record, root, errors)
    validate.semantics(record, warnings, errors)
    return errors, warnings


def test_fixture_sets_are_not_empty() -> None:
    """A glob that silently matches nothing would make every test below vacuous."""
    assert EXAMPLES, "no example records found"
    assert REJECTIONS, "no bad-* fixtures found"
    assert WARNINGS, "no warn-* fixtures found"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_records_validate(path: Path) -> None:
    errors, _ = diagnose(path)
    assert errors == []


@pytest.mark.parametrize("path", REJECTIONS, ids=lambda p: p.stem)
def test_rejected_records_emit_their_own_diagnostic(path: Path) -> None:
    errors, _ = diagnose(path)
    assert errors, "fixture was accepted but must be rejected"
    expected = EXPECTED_DIAGNOSTIC[path.stem]
    assert any(expected in e for e in errors), (
        f"{path.stem} no longer reports {expected!r}; it failed only for other reasons: {errors}"
    )


@pytest.mark.parametrize("path", WARNINGS, ids=lambda p: p.stem)
def test_warned_records_pass_but_warn(path: Path) -> None:
    errors, warnings = diagnose(path)
    assert errors == [], "a warn-* fixture must pass validation"
    expected = EXPECTED_DIAGNOSTIC[path.stem]
    assert any(expected in w for w in warnings), (
        f"{path.stem} no longer warns {expected!r}; warnings were: {warnings}"
    )


def test_every_fixture_has_a_declared_diagnostic() -> None:
    """A new fixture without an entry here would otherwise be tested by name only."""
    declared = set(EXPECTED_DIAGNOSTIC)
    present = {p.stem for p in REJECTIONS + WARNINGS}
    assert present == declared, f"undeclared: {present - declared}; stale: {declared - present}"
