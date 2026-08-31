import json
from pathlib import Path

from .models import BaselineLock


class BaselineStaleError(ValueError):
    pass


def write_baseline_lock(path: Path, lock: BaselineLock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = lock.model_dump(mode="json")
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def read_baseline_lock(path: Path) -> BaselineLock:
    return BaselineLock.model_validate_json(path.read_text(encoding="utf-8"))


def assert_suite_fresh(lock: BaselineLock, suite_sha256: str, config_sha256: str) -> None:
    if lock.suite_sha256 != suite_sha256:
        raise BaselineStaleError(
            f"suite fingerprint changed: {lock.suite_sha256} != {suite_sha256}"
        )
    if lock.config_sha256 != config_sha256:
        raise BaselineStaleError(
            f"config fingerprint changed: {lock.config_sha256} != {config_sha256}"
        )
