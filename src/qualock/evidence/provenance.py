from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import qualock
from qualock.agents.base import AgentBinary
from qualock.agents.support_integrity import agent_support_fingerprint
from qualock.baseline.models import BaselineLock
from qualock.canary.models import CanarySpec
from qualock.config.models import QualockConfig
from qualock.evidence.fingerprint import canonical_json, sha256_canonical
from qualock.project import canary_fingerprint
from qualock.qualification.models import QualificationResult

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SOURCE_SHA_PATTERN = r"^[0-9a-f]{40}$"
_PREPARED_IMAGE_DIGEST_PATTERN = r"^(|sha256:[0-9a-f]{64})$"


class EvidenceProvenanceError(Exception):
    pass


class RuntimeAgentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    binary_sha256: str = Field(pattern=_SHA256_PATTERN)
    support_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class ProvenanceModelPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1)
    snapshot: str | None = None
    reasoning_effort: str = Field(min_length=1)


class CanaryRuntimeProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    canary_id: str = Field(min_length=1)
    canary_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    repository_url_sha256: str = Field(pattern=_SHA256_PATTERN)
    repository_base_sha: str = Field(pattern=_SOURCE_SHA_PATTERN)
    prepared_image_digest: str = Field(pattern=_PREPARED_IMAGE_DIGEST_PATTERN)


class EvidenceProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1]
    qualification_id: str = Field(min_length=1)
    run_qualock_version: str = Field(min_length=1)
    baseline_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_identity: RuntimeAgentIdentity
    candidate_identity: RuntimeAgentIdentity
    model: ProvenanceModelPin
    repetitions: int = Field(gt=0)
    run_order_sha256: str = Field(pattern=_SHA256_PATTERN)
    canaries: tuple[CanaryRuntimeProvenance, ...]

    @model_validator(mode="after")
    def _require_gemini_support_fingerprint(self) -> "EvidenceProvenance":
        for identity in (self.baseline_identity, self.candidate_identity):
            if identity.name == "gemini" and identity.support_sha256 is None:
                raise ValueError(
                    "gemini runtime identity requires a non-null support fingerprint"
                )
        return self


def build_evidence_provenance(
    *,
    lock: BaselineLock,
    baseline_binary: AgentBinary,
    candidate_binary: AgentBinary,
    config: QualockConfig,
    canaries: Sequence[CanarySpec],
    result: QualificationResult,
) -> EvidenceProvenance:
    executions_by_canary_id = {execution.canary_id: execution for execution in result.executions}
    canary_entries = tuple(
        CanaryRuntimeProvenance(
            canary_id=canary.id,
            canary_fingerprint_sha256=canary_fingerprint(canary),
            repository_url_sha256=sha256_canonical(canary.repository.url),
            repository_base_sha=canary.repository.base_sha,
            prepared_image_digest=executions_by_canary_id[canary.id].prepared_image_digest,
        )
        for canary in sorted(canaries, key=lambda item: item.id)
    )
    return EvidenceProvenance(
        schema_version=1,
        qualification_id=result.qualification_id,
        run_qualock_version=qualock.__version__,
        baseline_lock_sha256=sha256_canonical(lock.model_dump(mode="json")),
        baseline_identity=RuntimeAgentIdentity(
            name=config.agent.name,
            version=baseline_binary.version,
            binary_sha256=baseline_binary.sha256,
            support_sha256=agent_support_fingerprint(baseline_binary),
        ),
        candidate_identity=RuntimeAgentIdentity(
            name=config.agent.name,
            version=candidate_binary.version,
            binary_sha256=candidate_binary.sha256,
            support_sha256=agent_support_fingerprint(candidate_binary),
        ),
        model=ProvenanceModelPin(
            id=config.model.id,
            snapshot=config.model.snapshot,
            reasoning_effort=config.model.reasoning_effort,
        ),
        repetitions=config.qualification.repetitions,
        run_order_sha256=sha256_canonical(result.run_order),
        canaries=canary_entries,
    )


def write_evidence_provenance(path: Path, value: EvidenceProvenance) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value.model_dump(mode="json")) + b"\n"
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise EvidenceProvenanceError("evidence provenance artifact already exists") from exc
    return path


def read_evidence_provenance(path: Path, *, max_bytes: int = 1_048_576) -> EvidenceProvenance:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvidenceProvenanceError("evidence provenance artifact is unavailable") from exc
    if size > max_bytes:
        raise EvidenceProvenanceError("evidence provenance artifact exceeds maximum size")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceProvenanceError("evidence provenance artifact is unavailable") from exc
    try:
        return EvidenceProvenance.model_validate_json(text)
    except ValidationError as exc:
        raise EvidenceProvenanceError("evidence provenance artifact is invalid") from exc
