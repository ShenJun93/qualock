from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol

from platformdirs import user_state_dir
from pydantic import ValidationError

from qualock.baseline.models import BaselineLock
from qualock.evidence.fingerprint import sha256_canonical

from .models import MonitorState


class MonitorStateStore(Protocol):
    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        raise NotImplementedError

    def save(self, root: Path, state: MonitorState) -> None:
        raise NotImplementedError


def project_key(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve())).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def baseline_sha256(lock: BaselineLock) -> str:
    return sha256_canonical(lock.model_dump(mode="json"))


class FileMonitorStateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (
            Path(user_state_dir("qualock")) / "release-monitor" / "projects"
        )

    def path_for(self, root: Path) -> Path:
        return self.base_dir / f"{project_key(root)}.json"

    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        path = self.path_for(root)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, None
        except (OSError, UnicodeError) as exc:
            return None, f"release monitor state ignored: {exc}"
        try:
            return MonitorState.model_validate_json(raw), None
        except ValidationError as exc:
            return None, f"release monitor state ignored: {exc}"

    def save(self, root: Path, state: MonitorState) -> None:
        path = self.path_for(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
