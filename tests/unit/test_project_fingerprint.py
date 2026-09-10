import hashlib
from pathlib import Path
from typing import Any

from qualock.canary.models import CanarySpec
from qualock.config.models import QualockConfig
from qualock.evidence.fingerprint import sha256_canonical
from qualock.project import (
    _canary_fingerprint_payload,
    canary_fingerprint,
    config_fingerprint,
    suite_fingerprint,
)


def make_canary(tmp_path: Path, patch_content: str, **overrides: Any) -> CanarySpec:
    patch = tmp_path / "grader.patch"
    patch.write_text(patch_content, encoding="utf-8")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "id": "sample",
        "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix it",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(patch), "command": ["pytest -q"]},
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    }
    payload.update(overrides)
    return CanarySpec.model_validate(payload)


def test_suite_fingerprint_uses_grader_contents_not_absolute_path(tmp_path: Path) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir(); right_dir.mkdir()
    left = make_canary(left_dir, "same")
    right = make_canary(right_dir, "same")
    assert suite_fingerprint([left]) == suite_fingerprint([right])


def test_suite_fingerprint_changes_when_hidden_grader_changes(tmp_path: Path) -> None:
    one = tmp_path / "one"; two = tmp_path / "two"
    one.mkdir(); two.mkdir()
    assert suite_fingerprint([make_canary(one, "a")]) != suite_fingerprint([make_canary(two, "b")])


def test_project_protections_do_not_change_agent_qualification_config_fingerprint() -> None:
    base = QualockConfig.model_validate({})
    with_protection = QualockConfig.model_validate(
        {
            "protections": [
                {
                    "id": "tests",
                    "name": "Tests pass",
                    "command": ["python", "-m", "pytest", "-q"],
                }
            ]
        }
    )

    assert config_fingerprint(base) == config_fingerprint(with_protection)


def test_canary_fingerprint_is_machine_path_independent(tmp_path: Path) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir(); right_dir.mkdir()
    left = make_canary(left_dir, "same")
    right = make_canary(right_dir, "same")
    assert canary_fingerprint(left) == canary_fingerprint(right)


def test_canary_fingerprint_changes_when_a_definition_field_changes(tmp_path: Path) -> None:
    one = tmp_path / "one"; two = tmp_path / "two"
    one.mkdir(); two.mkdir()
    unchanged = make_canary(one, "same")
    changed = make_canary(two, "same", task="Fix it differently")
    assert canary_fingerprint(unchanged) != canary_fingerprint(changed)


def test_canary_fingerprint_changes_when_grader_patch_bytes_change(tmp_path: Path) -> None:
    one = tmp_path / "one"; two = tmp_path / "two"
    one.mkdir(); two.mkdir()
    assert canary_fingerprint(make_canary(one, "a")) != canary_fingerprint(make_canary(two, "b"))


def test_canary_fingerprint_equals_hash_of_its_payload_helper(tmp_path: Path) -> None:
    canary = make_canary(tmp_path, "content")
    assert canary_fingerprint(canary) == sha256_canonical(_canary_fingerprint_payload(canary))


def test_canary_fingerprint_payload_hashes_grader_bytes_not_path(tmp_path: Path) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir(); right_dir.mkdir()
    left = make_canary(left_dir, "same")
    right = make_canary(right_dir, "same")
    left_payload = _canary_fingerprint_payload(left)
    right_payload = _canary_fingerprint_payload(right)
    assert left_payload == right_payload
    assert "patch" not in left_payload["grader"]
    assert left_payload["grader"]["patch_sha256"] == hashlib.sha256(b"same").hexdigest()


def test_suite_fingerprint_hashes_sorted_full_canary_payloads_not_a_digest_list(
    tmp_path: Path,
) -> None:
    one_dir = tmp_path / "one"; two_dir = tmp_path / "two"
    one_dir.mkdir(); two_dir.mkdir()
    one = make_canary(one_dir, "a", id="one")
    two = make_canary(two_dir, "b", id="two")

    expected = sha256_canonical(
        [
            _canary_fingerprint_payload(canary)
            for canary in sorted([one, two], key=lambda item: item.id)
        ]
    )

    assert suite_fingerprint([two, one]) == expected
    # Proves suite_fingerprint is not simply hashing the list of per-canary digests.
    digest_list_hash = sha256_canonical(
        sorted([canary_fingerprint(one), canary_fingerprint(two)])
    )
    assert suite_fingerprint([two, one]) != digest_list_hash
