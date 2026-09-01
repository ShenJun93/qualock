import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def write_project_evidence(
    results_dir: Path,
    operation_id: str,
    *,
    kind: str,
    result: BaseModel,
) -> Path:
    root = results_dir / operation_id
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "kind": kind,
        "result": result.model_dump(mode="json"),
    }
    (root / "report.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return root
