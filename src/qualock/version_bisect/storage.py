import json
import uuid
from pathlib import Path
from typing import Protocol

from qualock.version_bisect.models import BisectStep, BisectStop


class BisectSummaryStore(Protocol):
    def create(
        self,
        *,
        bisect_id: str,
        baseline: str,
        upper: str,
        candidates: tuple[str, ...],
        steps: tuple[BisectStep, ...],
        last_good: str,
        first_bad: str | None,
        stop: BisectStop | None,
    ) -> Path: ...

    def save(
        self,
        *,
        bisect_id: str,
        baseline: str,
        upper: str,
        candidates: tuple[str, ...],
        steps: tuple[BisectStep, ...],
        last_good: str,
        first_bad: str | None,
        stop: BisectStop | None,
    ) -> None: ...


class FileBisectSummaryStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def create(
        self,
        *,
        bisect_id: str,
        baseline: str,
        upper: str,
        candidates: tuple[str, ...],
        steps: tuple[BisectStep, ...],
        last_good: str,
        first_bad: str | None,
        stop: BisectStop | None,
    ) -> Path:
        run_dir = self._root / bisect_id
        run_dir.mkdir(parents=True, exist_ok=False)
        _replace_summary(
            run_dir / "summary.json",
            _payload(
                bisect_id=bisect_id,
                baseline=baseline,
                upper=upper,
                candidates=candidates,
                steps=steps,
                last_good=last_good,
                first_bad=first_bad,
                stop=stop,
            ),
        )
        return run_dir

    def save(
        self,
        *,
        bisect_id: str,
        baseline: str,
        upper: str,
        candidates: tuple[str, ...],
        steps: tuple[BisectStep, ...],
        last_good: str,
        first_bad: str | None,
        stop: BisectStop | None,
    ) -> None:
        run_dir = self._root / bisect_id
        if not run_dir.is_dir():
            raise FileNotFoundError(run_dir)
        _replace_summary(
            run_dir / "summary.json",
            _payload(
                bisect_id=bisect_id,
                baseline=baseline,
                upper=upper,
                candidates=candidates,
                steps=steps,
                last_good=last_good,
                first_bad=first_bad,
                stop=stop,
            ),
        )


def _replace_summary(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _payload(
    bisect_id: str,
    baseline: str,
    upper: str,
    candidates: tuple[str, ...],
    steps: tuple[BisectStep, ...],
    last_good: str,
    first_bad: str | None,
    stop: BisectStop | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "bisect_id": bisect_id,
        "baseline_version": baseline,
        "upper_version": upper,
        "candidates": list(candidates),
        "steps": [
            {
                "version": step.version,
                "qualification_id": step.qualification_id,
                "verdict": step.verdict.value,
            }
            for step in steps
        ],
        "last_known_good": last_good,
        "first_bad": first_bad,
        "stop_reason": stop.value if stop is not None else None,
    }
