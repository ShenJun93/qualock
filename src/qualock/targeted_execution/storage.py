import hashlib
import json
from pathlib import Path

from qualock.change_targeting.models import CoverageAssessmentV0
from qualock.evidence.fingerprint import canonical_json
from qualock.qualification.models import QualificationResult
from qualock.report.render import render_json, render_markdown

from .models import (
    TargetedExecutionError,
    TargetedQualificationV1,
    TargetedReportV1,
    TargetedRunV1,
)


def targeted_results_dir(root: Path) -> Path:
    return root / "targeted"


def _validate_qualification_id(qid: str) -> None:
    if (
        not qid
        or Path(qid).name != qid
        or qid in {".", ".."}
        or "/" in qid
        or "\\" in qid
    ):
        raise TargetedExecutionError("unsafe targeted qualification_id")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_targeted_qualification_artifacts(
    base_dir: Path,
    *,
    result: QualificationResult,
    assessment: CoverageAssessmentV0,
    selected_sources: tuple[str, ...],
    agent_display_name: str,
) -> Path:
    qid = result.qualification_id
    _validate_qualification_id(qid)
    root = targeted_results_dir(base_dir) / qid
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise TargetedExecutionError(
            f"targeted qualification already exists: {root}"
        ) from exc
    except OSError as exc:
        raise TargetedExecutionError(
            f"targeted qualification directory could not be created: {root}"
        ) from exc

    report = TargetedReportV1(
        selected_sources=selected_sources,
        assessment=assessment,
        result=render_json(result),
    )
    qualification = TargetedQualificationV1(
        qualification_id=result.qualification_id,
        baseline_version=result.baseline_version,
        candidate_version=result.candidate_version,
        verdict=result.verdict,
        run_order=result.run_order,
        max_attempts=result.max_attempts,
        max_tokens=result.max_tokens,
        attempts_used=result.attempts_used,
        observed_tokens=result.observed_tokens,
        selected_sources=selected_sources,
    )

    markdown = (
        "# QuaLock targeted qualification\n\n"
        "> Scope: selected sources only. This is not a full-suite safety decision.\n\n"
        + render_markdown(result, agent_display_name=agent_display_name)
    )
    (root / "targeted-report.md").write_text(markdown, encoding="utf-8")
    _write_json(root / "targeted-report.json", report.model_dump(mode="json"))
    _write_json(
        root / "targeted-qualification.json",
        qualification.model_dump(mode="json"),
    )
    return root


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_targeted_run(path: Path, value: TargetedRunV1) -> Path:
    payload = canonical_json(value.model_dump(mode="json")) + b"\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise TargetedExecutionError("targeted run artifact already exists") from exc
    except OSError as exc:
        raise TargetedExecutionError("targeted run artifact could not be written") from exc
    return path
