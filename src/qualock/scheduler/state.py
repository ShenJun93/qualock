from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from platformdirs import user_state_dir
from pydantic import ValidationError

from .models import ScheduleRegistration


class RegistrationLoadKind(str, Enum):
    MISSING = "missing"
    VALID = "valid"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class RegistrationLoad:
    kind: RegistrationLoadKind
    registration: ScheduleRegistration | None = None
    detail: str | None = None


class RegistrationStore(Protocol):
    def project_dir(self, project_key: str) -> Path: ...

    def registration_path(self, project_key: str) -> Path: ...

    def log_path(self, project_key: str) -> Path: ...

    def load(self, project_key: str) -> RegistrationLoad: ...

    def save(self, registration: ScheduleRegistration) -> None: ...

    def delete(self, project_key: str) -> None: ...


class FileRegistrationStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (
            Path(user_state_dir("qualock")) / "release-scheduler" / "projects"
        )

    def project_dir(self, project_key: str) -> Path:
        return self.base_dir / project_key

    def registration_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "registration.json"

    def log_path(self, project_key: str) -> Path:
        return self.project_dir(project_key) / "runs.log"

    def load(self, project_key: str) -> RegistrationLoad:
        if re.fullmatch(r"[0-9a-f]{64}", project_key) is None:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail="invalid project key")
        path = self.registration_path(project_key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return RegistrationLoad(RegistrationLoadKind.MISSING)
        except (OSError, UnicodeError) as exc:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail=str(exc))
        try:
            registration = ScheduleRegistration.model_validate_json(raw)
        except ValidationError as exc:
            return RegistrationLoad(RegistrationLoadKind.CORRUPT, detail=str(exc))
        if registration.project_key != project_key:
            return RegistrationLoad(
                RegistrationLoadKind.CORRUPT,
                detail="registration project key does not match state path",
            )
        return RegistrationLoad(RegistrationLoadKind.VALID, registration=registration)

    def save(self, registration: ScheduleRegistration) -> None:
        directory = self.project_dir(registration.project_key)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.registration_path(registration.project_key)
        temporary = directory / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(registration.model_dump_json() + "\n", encoding="utf-8")
            if os.name != "nt":
                directory.chmod(0o700)
                temporary.chmod(0o600)
            os.replace(temporary, destination)
            if os.name != "nt":
                destination.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, project_key: str) -> None:
        self.registration_path(project_key).unlink(missing_ok=True)
