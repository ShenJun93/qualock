"""Strict, frozen, closed-schema models for the V1 offline evidence bundle contract.

This module owns parsing shape only: file names, byte caps, and field bounds
for the payloads a bundle may contain. It does not recompute or verify
qualification policy semantics; that is owned by the offline verifier.
"""

from enum import Enum
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from qualock.evidence.provenance import EvidenceProvenance, RuntimeAgentIdentity
from qualock.qualification.models import Verdict

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SOURCE_SHA_PATTERN = r"^[0-9a-f]{40}$"
_PREPARED_IMAGE_DIGEST_PATTERN = r"^(|sha256:[0-9a-f]{64})$"

# --- Fixed V1 bundle filenames -------------------------------------------------

MANIFEST_FILENAME = "manifest.json"
REPORT_FILENAME = "report.json"
QUALIFICATION_FILENAME = "qualification.json"
BASELINE_LOCK_FILENAME = "baseline.lock"
PROVENANCE_FILENAME = "provenance.json"
CANARIES_FILENAME = "canaries.json"
PRICING_FILENAME = "pricing.json"

REQUIRED_PAYLOAD_FILENAMES: tuple[str, ...] = (
    REPORT_FILENAME,
    QUALIFICATION_FILENAME,
    BASELINE_LOCK_FILENAME,
    PROVENANCE_FILENAME,
    CANARIES_FILENAME,
)
OPTIONAL_PAYLOAD_FILENAMES: tuple[str, ...] = (PRICING_FILENAME,)
BUNDLE_FILENAMES: frozenset[str] = frozenset(
    (MANIFEST_FILENAME, *REQUIRED_PAYLOAD_FILENAMES, *OPTIONAL_PAYLOAD_FILENAMES)
)

# --- Exact V1 per-file byte caps ------------------------------------------------

_MIB = 1024 * 1024
MANIFEST_MAX_BYTES = 2 * _MIB
REPORT_MAX_BYTES = 32 * _MIB
QUALIFICATION_MAX_BYTES = 4 * _MIB
BASELINE_LOCK_MAX_BYTES = 4 * _MIB
PROVENANCE_MAX_BYTES = 8 * _MIB
CANARIES_MAX_BYTES = 8 * _MIB
PRICING_MAX_BYTES = 32 * _MIB

FILE_MAX_BYTES: dict[str, int] = {
    MANIFEST_FILENAME: MANIFEST_MAX_BYTES,
    REPORT_FILENAME: REPORT_MAX_BYTES,
    QUALIFICATION_FILENAME: QUALIFICATION_MAX_BYTES,
    BASELINE_LOCK_FILENAME: BASELINE_LOCK_MAX_BYTES,
    PROVENANCE_FILENAME: PROVENANCE_MAX_BYTES,
    CANARIES_FILENAME: CANARIES_MAX_BYTES,
    PRICING_FILENAME: PRICING_MAX_BYTES,
}

# --- Exact V1 field/collection bounds -------------------------------------------

TEXT_FIELD_MAX_BYTES = 4096
REPOSITORY_URL_MAX_BYTES = 2048
MAX_CANARY_RECORDS = 4096
MAX_ATTEMPTS_PER_EXECUTION = 8192


class EvidenceBundleReason(str, Enum):
    MALFORMED_MANIFEST = "malformed_manifest"
    UNSAFE_PATH = "unsafe_path"
    UNSAFE_REPOSITORY_URL = "unsafe_repository_url"
    INVENTORY_MISMATCH = "inventory_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED_PAYLOAD = "malformed_payload"
    IDENTITY_MISMATCH = "identity_mismatch"
    INVALID_GEMINI_SUPPORT = "invalid_gemini_support"
    CANARY_PROVENANCE_MISMATCH = "canary_provenance_mismatch"
    ATTEMPT_LAYOUT_MISMATCH = "attempt_layout_mismatch"
    COMPLETENESS_MISMATCH = "completeness_mismatch"
    VERDICT_MISMATCH = "verdict_mismatch"


class EvidenceBundleError(ValueError):
    """Raised for a fixed, stable evidence-bundle failure category.

    ``label`` must be a fixed logical file/field name, never raw JSON, event
    content, or another arbitrary bundle-controlled string.
    """

    def __init__(self, reason: EvidenceBundleReason, label: str) -> None:
        super().__init__(f"evidence bundle {reason.value}: {label}")
        self.reason = reason
        self.label = label


def _utf8_byte_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _bound_text(max_bytes: int) -> AfterValidator:
    def _validate(value: str) -> str:
        if _utf8_byte_length(value) > max_bytes:
            raise ValueError("text field exceeds maximum byte length")
        return value

    return AfterValidator(_validate)


BoundedText = Annotated[str, Field(min_length=1), _bound_text(TEXT_FIELD_MAX_BYTES)]
BoundedRepositoryUrl = Annotated[
    str, Field(min_length=1), _bound_text(REPOSITORY_URL_MAX_BYTES)
]
Sha256Hex = Annotated[str, Field(pattern=_SHA256_PATTERN)]
SourceSha = Annotated[str, Field(pattern=_SOURCE_SHA_PATTERN)]
PreparedImageDigest = Annotated[str, Field(pattern=_PREPARED_IMAGE_DIGEST_PATTERN)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


_CONTROL_CHARS = frozenset(chr(code) for code in range(0x20)) | {chr(0x7F)}


def publication_safe_repository_url(value: str) -> str:
    """Return ``value`` unchanged if it is a V1 publication-safe repository URL.

    Never sanitizes or rewrites the input; fails closed with
    ``EvidenceBundleReason.UNSAFE_REPOSITORY_URL`` for every other form,
    including local paths, ``file:`` URLs, and SSH/SCP locators.
    """
    if not isinstance(value, str) or _utf8_byte_length(value) > REPOSITORY_URL_MAX_BYTES:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    if not value or any(char in _CONTROL_CHARS for char in value):
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise EvidenceBundleError(
            EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url"
        ) from exc
    if parsed.scheme not in ("http", "https"):
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    if parsed.username is not None or parsed.password is not None:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    if not hostname:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    if parsed.query or parsed.fragment:
        raise EvidenceBundleError(EvidenceBundleReason.UNSAFE_REPOSITORY_URL, "repository_url")
    return value


# --- Public report projection ---------------------------------------------------


class PublicUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    cache_write_input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    reasoning_output_tokens: NonNegativeInt
    observed: bool


class PublicAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    side: BoundedText
    repetition: PositiveInt
    success: bool
    valid: bool
    duration_ms: NonNegativeInt
    usage: PublicUsage
    events_sha256: Sha256Hex


class PublicExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    canary_id: BoundedText
    critical: bool
    prepared_image_digest: PreparedImageDigest
    attempts: Annotated[tuple[PublicAttempt, ...], Field(max_length=MAX_ATTEMPTS_PER_EXECUTION)]
    baseline_valid: NonNegativeInt
    baseline_successes: NonNegativeInt
    candidate_valid: NonNegativeInt
    candidate_successes: NonNegativeInt
    verdict: Verdict
    reason: BoundedText


class BundleCompleteness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    attempts_expected: NonNegativeInt
    attempts_used: NonNegativeInt
    max_attempts: PositiveInt | None = None
    max_tokens: PositiveInt | None = None
    observed_tokens: NonNegativeInt | None = None
    all_canaries_complete: bool


class PublicReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    qualification_id: BoundedText
    baseline_version: BoundedText
    candidate_version: BoundedText
    verdict: Verdict
    reasons: Annotated[tuple[BoundedText, ...], Field(max_length=MAX_CANARY_RECORDS)]
    run_order: Annotated[
        tuple[tuple[BoundedText, BoundedText, PositiveInt], ...],
        Field(max_length=MAX_ATTEMPTS_PER_EXECUTION),
    ]
    executions: Annotated[tuple[PublicExecution, ...], Field(max_length=MAX_CANARY_RECORDS)]
    completeness: BundleCompleteness


class PublicQualification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    qualification_id: BoundedText
    baseline_version: BoundedText
    candidate_version: BoundedText
    verdict: Verdict
    run_order: Annotated[
        tuple[tuple[BoundedText, BoundedText, PositiveInt], ...],
        Field(max_length=MAX_ATTEMPTS_PER_EXECUTION),
    ]
    completeness: BundleCompleteness


# --- Bundle-local strict mirrors of baseline.lock -------------------------------


class PublicAgentPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: BoundedText
    version: BoundedText
    binary_sha256: Sha256Hex
    support_sha256: Sha256Hex | None = None


class PublicModelPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: BoundedText
    snapshot: BoundedText | None = None
    reasoning_effort: BoundedText


class PublicCanaryStability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    valid_runs: NonNegativeInt
    successes: NonNegativeInt


class PublicBaselineLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = Field(strict=True, ge=1, le=1)
    created_at: BoundedText
    agent: PublicAgentPin
    model: PublicModelPin
    qualock_version: BoundedText
    suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    canaries: Annotated[
        dict[str, PublicCanaryStability], Field(max_length=MAX_CANARY_RECORDS)
    ]


# --- Public canary metadata projection (canaries.json) --------------------------


class PublicCanary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    canary_id: BoundedText
    critical: bool
    repository_url: BoundedRepositoryUrl
    repository_url_sha256: Sha256Hex
    base_sha: SourceSha
    canary_fingerprint_sha256: Sha256Hex
    prepared_image_digest: PreparedImageDigest
    repetitions: PositiveInt


class PublicCanaries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = Field(strict=True, ge=1, le=1)
    canaries: Annotated[tuple[PublicCanary, ...], Field(max_length=MAX_CANARY_RECORDS)]

    @model_validator(mode="after")
    def _require_unique_canary_ids(self) -> "PublicCanaries":
        seen: set[str] = set()
        for canary in self.canaries:
            if canary.canary_id in seen:
                raise ValueError("duplicate canary_id in canaries.json")
            seen.add(canary.canary_id)
        return self


# --- Manifest -------------------------------------------------------------------


class BundleFileRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sha256: Sha256Hex
    size_bytes: NonNegativeInt


class ManifestCanaryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    critical: bool
    repository_url: BoundedRepositoryUrl
    repository_url_sha256: Sha256Hex
    base_sha: SourceSha
    canary_fingerprint_sha256: Sha256Hex
    prepared_image_digest: PreparedImageDigest
    repetitions: PositiveInt
    baseline_valid: NonNegativeInt
    baseline_successes: NonNegativeInt
    candidate_valid: NonNegativeInt
    candidate_successes: NonNegativeInt
    verdict: Verdict


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = Field(strict=True, ge=1, le=1)
    created_at: BoundedText
    run_qualock_version: BoundedText
    exporter_qualock_version: BoundedText
    qualification_id: BoundedText
    baseline_version: BoundedText
    candidate_version: BoundedText
    verdict: Verdict
    baseline_identity: RuntimeAgentIdentity
    candidate_identity: RuntimeAgentIdentity
    model: PublicModelPin
    baseline_lock_sha256: Sha256Hex
    suite_sha256: Sha256Hex
    config_sha256: Sha256Hex
    run_order_sha256: Sha256Hex
    completeness: BundleCompleteness
    canaries: Annotated[
        dict[str, ManifestCanaryRecord], Field(max_length=MAX_CANARY_RECORDS)
    ]
    files: Annotated[
        dict[str, BundleFileRecord], Field(max_length=len(REQUIRED_PAYLOAD_FILENAMES) + 1)
    ]


    @model_validator(mode="after")
    def _require_gemini_support_fingerprint(self) -> "EvidenceManifest":
        for identity in (self.baseline_identity, self.candidate_identity):
            if identity.name == "gemini" and identity.support_sha256 is None:
                raise ValueError(
                    "gemini runtime identity requires a non-null support fingerprint"
                )
        return self


class VerifiedEvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    manifest: EvidenceManifest
    manifest_sha256: Sha256Hex
    report: PublicReport
    qualification: PublicQualification
    baseline_lock: PublicBaselineLock
    provenance: EvidenceProvenance
    canaries: PublicCanaries
