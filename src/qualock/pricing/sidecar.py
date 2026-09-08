import json
import os
import tempfile
from pathlib import Path


def write_pricing_sidecar(qualification_dir: Path, payload: dict[str, object]) -> Path:
    final_path = qualification_dir / "pricing.json"
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")

    fd, temp_name = tempfile.mkstemp(
        prefix=".pricing.", suffix=".tmp", dir=qualification_dir
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return final_path
