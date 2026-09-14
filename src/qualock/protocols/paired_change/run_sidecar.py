from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from qualock.evidence.fingerprint import canonical_json
from qualock.qualification.models import QualificationResult
from qualock.run.models import AttemptRunTrace

from .design import derive_changeset
from .fingerprint import digest_model
from .models import (
    AgentDependencyStateV1,
    AttemptProtocolContextV1,
    CanaryRunEvidenceV1,
    PairedChangeRunV1,
    PairEvidenceV1,
    ProtocolDesignV1,
)

_DEFAULT_MAX_BYTES = 4_194_304


class PairedChangeRunError(Exception):
    pass


def _side_literal(value: str) -> Literal["baseline", "candidate"]:
    if value == "baseline":
        return "baseline"
    if value == "candidate":
        return "candidate"
    raise PairedChangeRunError(f"unknown attempt side: {value!r}")


def _normalize_prepared_target_sha256(digest: str) -> str | None:
    candidate = digest.removeprefix("sha256:")
    if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
        return candidate
    return None


def _attempt_context(
    item: AttemptRunTrace, *, protocol_design_sha256: str
) -> AttemptProtocolContextV1:
    return AttemptProtocolContextV1(
        side=_side_literal(item.side),
        repetition=item.repetition,
        events_sha256=item.events_sha256,
        protocol_design_sha256=protocol_design_sha256,
        started_offset_ms=item.started_offset_ms,
        finished_offset_ms=item.finished_offset_ms,
        isolation_instance_sha256=item.context.isolation_instance_sha256,
        preparation_sha256=item.context.profiles.preparation_sha256,
        isolation_sha256=item.context.profiles.isolation_sha256,
        resource_sha256=item.context.profiles.resource_sha256,
        runtime_sha256=item.context.profiles.runtime_sha256,
    )


def build_paired_change_run(
    *,
    protocol_design: ProtocolDesignV1,
    protocol_design_sha256: str,
    qualification_id: str,
    baseline_state: AgentDependencyStateV1,
    candidate_state: AgentDependencyStateV1,
    result: QualificationResult,
    trace: Sequence[AttemptRunTrace],
) -> PairedChangeRunV1:
    if digest_model(protocol_design) != protocol_design_sha256:
        raise PairedChangeRunError("protocol design digest does not match frozen design")
    if qualification_id != result.qualification_id:
        raise PairedChangeRunError("qualification ID does not match qualification result")

    fingerprints_by_canary_id = {
        canary.canary_id: canary.canary_fingerprint_sha256 for canary in protocol_design.canaries
    }
    prepared_by_canary_id = {
        execution.canary_id: execution.prepared_image_digest for execution in result.executions
    }

    traces_by_canary: dict[str, list[AttemptRunTrace]] = {}
    for item in trace:
        if item.trace_design_sha256 != protocol_design_sha256:
            raise PairedChangeRunError(
                f"attempt trace for canary {item.canary_id!r} repetition {item.repetition} "
                "is not bound to the frozen protocol design digest"
            )
        traces_by_canary.setdefault(item.canary_id, []).append(item)

    public_attempts_by_canary: dict[str, dict[tuple[str, int], str]] = {}
    for execution in result.executions:
        public_attempts: dict[tuple[str, int], str] = {}
        for attempt in execution.attempts:
            key = (attempt.side, attempt.repetition)
            if key in public_attempts:
                raise PairedChangeRunError(
                    f"canary {execution.canary_id!r} has duplicate public attempt {key!r}"
                )
            public_attempts[key] = hashlib.sha256(attempt.events_jsonl.encode()).hexdigest()
        public_attempts_by_canary[execution.canary_id] = public_attempts

    for canary_id, canary_trace in traces_by_canary.items():
        expected_public_attempts = public_attempts_by_canary.get(canary_id)
        if expected_public_attempts is None:
            raise PairedChangeRunError(
                f"canary {canary_id!r} has attempt evidence but no qualification result execution"
            )
        trace_attempts = {
            (item.side, item.repetition): item.events_sha256 for item in canary_trace
        }
        if (
            len(trace_attempts) != len(canary_trace)
            or trace_attempts != expected_public_attempts
        ):
            raise PairedChangeRunError(
                f"canary {canary_id!r} trace does not match public qualification attempts"
            )

    expected_repetitions = set(range(1, protocol_design.repetitions + 1))
    for execution in result.executions:
        if not execution.attempts:
            continue
        actual_repetitions = {
            item.repetition for item in traces_by_canary.get(execution.canary_id, ())
        }
        if actual_repetitions != expected_repetitions:
            raise PairedChangeRunError(
                f"canary {execution.canary_id!r} does not have exactly repetitions "
                f"1..{protocol_design.repetitions}"
            )

    canaries: list[CanaryRunEvidenceV1] = []
    for canary_id in sorted(traces_by_canary):
        canary_fingerprint_sha256 = fingerprints_by_canary_id.get(canary_id)
        if canary_fingerprint_sha256 is None:
            raise PairedChangeRunError(
                f"canary {canary_id!r} has attempt evidence but no frozen protocol design entry"
            )
        try:
            prepared_image_digest = prepared_by_canary_id[canary_id]
        except KeyError as exc:
            raise PairedChangeRunError(
                f"canary {canary_id!r} has attempt evidence but no qualification result execution"
            ) from exc

        by_repetition: dict[int, list[AttemptRunTrace]] = {}
        for item in traces_by_canary[canary_id]:
            by_repetition.setdefault(item.repetition, []).append(item)

        pairs: list[PairEvidenceV1] = []
        for repetition in sorted(by_repetition):
            sides = by_repetition[repetition]
            if len(sides) != 2 or {item.side for item in sides} != {"baseline", "candidate"}:
                raise PairedChangeRunError(
                    f"canary {canary_id!r} repetition {repetition} does not have exactly one "
                    "baseline and one candidate attempt"
                )
            first, second = (
                _attempt_context(item, protocol_design_sha256=protocol_design_sha256)
                for item in sides
            )
            pairs.append(PairEvidenceV1(repetition=repetition, attempts=(first, second)))

        canaries.append(
            CanaryRunEvidenceV1(
                canary_id=canary_id,
                canary_fingerprint_sha256=canary_fingerprint_sha256,
                prepared_target_sha256=_normalize_prepared_target_sha256(prepared_image_digest),
                pairs=tuple(pairs),
            )
        )

    changeset = derive_changeset(baseline_state, candidate_state)

    return PairedChangeRunV1(
        schema_version=1,
        protocol_id=protocol_design.protocol_id,
        protocol_digest=protocol_design.protocol_digest,
        protocol_design=protocol_design,
        protocol_design_sha256=protocol_design_sha256,
        qualification_id=qualification_id,
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        changeset_sha256=digest_model(changeset),
        canaries=tuple(canaries),
    )


def write_paired_change_run(path: Path, value: PairedChangeRunV1) -> Path:
    payload = canonical_json(value.model_dump(mode="json")) + b"\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise PairedChangeRunError("paired-change run artifact already exists") from exc
    except OSError as exc:
        raise PairedChangeRunError("paired-change run artifact could not be written") from exc
    return path


def read_paired_change_run(
    path: Path, *, max_bytes: int = _DEFAULT_MAX_BYTES
) -> PairedChangeRunV1:
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise PairedChangeRunError("paired-change run artifact is unavailable") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise PairedChangeRunError("paired-change run artifact is unavailable")
            if metadata.st_size > max_bytes:
                raise PairedChangeRunError(
                    "paired-change run artifact exceeds maximum size"
                )
            try:
                raw = handle.read(max_bytes + 1)
            except OSError as exc:
                raise PairedChangeRunError(
                    "paired-change run artifact is unavailable"
                ) from exc
    except OSError as exc:
        raise PairedChangeRunError("paired-change run artifact is unavailable") from exc
    if len(raw) > max_bytes:
        raise PairedChangeRunError("paired-change run artifact exceeds maximum size")
    try:
        text = raw.decode("utf-8")
        return PairedChangeRunV1.model_validate_json(text)
    except (UnicodeDecodeError, ValidationError) as exc:
        raise PairedChangeRunError("paired-change run artifact is invalid") from exc
