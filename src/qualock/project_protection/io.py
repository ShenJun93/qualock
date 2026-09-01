import json
from pathlib import Path

from .models import ProjectLock


def write_project_lock(path: Path, lock: ProjectLock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(lock.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_project_lock(path: Path) -> ProjectLock:
    return ProjectLock.model_validate_json(path.read_text(encoding="utf-8"))
