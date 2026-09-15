"""Canonical catalog/chain digest helpers for first-bad/v1."""

from __future__ import annotations

from qualock.evidence.fingerprint import sha256_canonical

from .models import FirstBadChainEvidenceV1


def digest_catalog(versions: tuple[str, ...]) -> str:
    return sha256_canonical(list(versions))


def digest_chain_evidence(evidence: FirstBadChainEvidenceV1) -> str:
    return sha256_canonical(evidence.model_dump(mode="json", exclude={"chain_sha256"}))
