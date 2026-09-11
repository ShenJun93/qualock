from __future__ import annotations

from qualock.evidence.bundle_models import VerifiedEvidenceBundle
from qualock.evidence.export import ExportedEvidenceBundle


def render_evidence_export(bundle: ExportedEvidenceBundle) -> str:
    lines = [
        "QuaLock Evidence Bundle Export",
        "",
        f"Qualification: {bundle.qualification_id}",
        f"Destination:   {bundle.path}",
        f"Manifest SHA:  {bundle.manifest_sha256}",
    ]
    return "\n".join(lines) + "\n"


def render_evidence_verify(bundle: VerifiedEvidenceBundle) -> str:
    manifest = bundle.manifest
    verdict_str = (
        manifest.verdict.value.upper()
        if hasattr(manifest.verdict, "value")
        else str(manifest.verdict).upper()
    )
    lines = [
        "QuaLock Evidence Bundle Verification",
        "",
        f"Qualification:    {manifest.qualification_id}",
        f"Schema version:   {manifest.schema_version}",
        f"Baseline version: {manifest.baseline_version}",
        f"Candidate version: {manifest.candidate_version}",
        f"Verdict:          {verdict_str}",
        f"Manifest SHA:     {bundle.manifest_sha256}",
    ]
    return "\n".join(lines) + "\n"


render_export_terminal = render_evidence_export
render_verify_terminal = render_evidence_verify
