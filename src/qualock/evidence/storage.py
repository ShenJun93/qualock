import json
from dataclasses import asdict
from pathlib import Path

from qualock.qualification.models import AttemptResult, QualificationResult
from qualock.report.render import render_json, render_markdown


class ArtifactExistsError(FileExistsError):
    pass


def write_baseline_artifacts(
    base_dir: Path,
    qualification_id: str,
    baseline_version: str,
    attempts_by_canary: dict[str, list[AttemptResult]],
) -> Path:
    root = base_dir / qualification_id
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ArtifactExistsError(f"qualification already exists: {root}") from exc

    payload = {
        "qualification_id": qualification_id,
        "baseline_version": baseline_version,
        "canaries": {
            canary_id: [asdict(attempt) for attempt in attempts]
            for canary_id, attempts in attempts_by_canary.items()
        },
    }
    (root / "baseline.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def write_qualification_artifacts(base_dir: Path, result: QualificationResult) -> Path:
    root = base_dir / result.qualification_id
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ArtifactExistsError(f"qualification already exists: {root}") from exc

    payload = render_json(result)
    (root / "report.md").write_text(render_markdown(result), encoding="utf-8")
    (root / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "qualification.json").write_text(
        json.dumps(
            {
                "qualification_id": result.qualification_id,
                "baseline_version": result.baseline_version,
                "candidate_version": result.candidate_version,
                "run_order": result.run_order,
                "verdict": result.verdict.value,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root
