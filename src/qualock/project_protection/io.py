import json
from pathlib import Path

from pydantic import ValidationError

from .models import ProjectLock, SignedProjectLock
from .signing import ProjectLockIntegrityError, sign_project_lock, verify_project_lock


def write_project_lock(path: Path, lock: ProjectLock, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = sign_project_lock(lock, key)
    path.write_text(
        json.dumps(envelope.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_project_lock(path: Path, key: bytes) -> ProjectLock:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectLockIntegrityError("project protection lock is malformed") from exc
    if isinstance(raw, dict) and raw.get("schema_version") == 1 and "protections" in raw:
        raise ProjectLockIntegrityError(
            "project protection lock is unsigned; establish a trusted known-good state and run qualock protect again"
        )
    try:
        envelope = SignedProjectLock.model_validate(raw)
    except ValidationError as exc:
        raise ProjectLockIntegrityError("project protection lock is malformed or unsigned") from exc
    return verify_project_lock(envelope, key)
