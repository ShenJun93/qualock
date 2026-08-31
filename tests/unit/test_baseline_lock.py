from pathlib import Path

import pytest

from qualock.baseline.io import BaselineStaleError, assert_suite_fresh, read_baseline_lock, write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, CanaryStability, ModelPin


def make_lock() -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-08-31T00:00:00Z",
        agent=AgentPin(name="codex", version="0.150.0", binary_sha256="abc"),
        model=ModelPin(id="gpt-5.3-codex", snapshot=None, reasoning_effort="high"),
        qualock_version="0.1.0",
        suite_sha256="suite-a",
        config_sha256="config-a",
        canaries={"sample": CanaryStability(valid_runs=3, successes=3)},
    )


def test_baseline_lock_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.lock"
    original = make_lock()
    write_baseline_lock(path, original)
    loaded = read_baseline_lock(path)
    assert loaded == original


def test_stale_suite_is_rejected() -> None:
    with pytest.raises(BaselineStaleError, match="suite"):
        assert_suite_fresh(make_lock(), "suite-b", "config-a")


def test_stale_config_is_rejected() -> None:
    with pytest.raises(BaselineStaleError, match="config"):
        assert_suite_fresh(make_lock(), "suite-a", "config-b")
