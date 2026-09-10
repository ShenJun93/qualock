import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import qualock
from qualock.agents.base import AgentBinary, AgentSupportTree
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.canary.models import CanarySpec
from qualock.config.models import AgentConfig, ModelConfig, QualificationConfig, QualockConfig
from qualock.evidence.fingerprint import sha256_canonical
from qualock.evidence.provenance import (
    EvidenceProvenance,
    EvidenceProvenanceError,
    build_evidence_provenance,
    read_evidence_provenance,
    write_evidence_provenance,
)
from qualock.project import canary_fingerprint
from qualock.qualification.models import CanaryExecution, QualificationResult, Verdict


def make_canary(
    tmp_path: Path,
    *,
    canary_id: str = "sample",
    url: str = "https://example.invalid/repo.git",
) -> CanarySpec:
    tmp_path.mkdir(parents=True, exist_ok=True)
    patch = tmp_path / f"{canary_id}.patch"
    patch.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": canary_id,
            "name": "Sample",
            "repository": {"url": url, "base_sha": "a" * 40},
            "runtime": {"execution": "container", "image": "python:3.12-slim"},
            "task": "Fix the regression in the checkout flow.",
            "setup": ["npm install"],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(patch), "command": ["pytest -q"]},
            "constraints": {"protected_paths": ["tests/**"]},
            "critical": True,
        }
    )


def make_binary(agent_name: str, version: str, *, support_trees: tuple = ()) -> AgentBinary:
    return AgentBinary(
        agent_name,
        version,
        Path(f"/fake/{agent_name}/{version}/agent"),
        hashlib.sha256(f"{agent_name}-{version}".encode()).hexdigest(),
        support_trees=support_trees,
    )


def make_lock(*, agent_name: str = "codex", qualock_version: str = "0.0.1-legacy") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-01T00:00:00Z",
        agent=AgentPin(
            name=agent_name,
            version="0.150.0",
            binary_sha256=hashlib.sha256(f"{agent_name}-0.150.0".encode()).hexdigest(),
        ),
        model=ModelPin(id="gpt-5.6-terra", reasoning_effort="high"),
        qualock_version=qualock_version,
        suite_sha256="s" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


def make_config(*, agent_name: str = "codex", repetitions: int = 3) -> QualockConfig:
    if agent_name == "gemini":
        model = ModelConfig(id="gemini-3-pro", snapshot=None, reasoning_effort="provider-default")
    else:
        model = ModelConfig(id="gpt-5.6-terra", snapshot=None, reasoning_effort="high")
    return QualockConfig(
        agent=AgentConfig(name=agent_name),
        model=model,
        qualification=QualificationConfig(repetitions=repetitions),
    )


def make_result(
    *,
    qualification_id: str = "check-q",
    canary_id: str = "sample",
    prepared_image_digest: str = "sha256:" + "b" * 64,
    run_order: tuple = (("sample", "baseline", 1),),
) -> QualificationResult:
    execution = CanaryExecution(
        canary_id=canary_id,
        critical=True,
        prepared_image_digest=prepared_image_digest,
        attempts=(),
        baseline_successes=3,
        candidate_successes=3,
        baseline_valid=3,
        candidate_valid=3,
        verdict=Verdict.PASS,
        reason="ok",
    )
    return QualificationResult(
        qualification_id=qualification_id,
        baseline_version="0.150.0",
        candidate_version="0.151.0",
        verdict=Verdict.PASS,
        executions=(execution,),
        reasons=(),
        run_order=run_order,
    )


def build(
    tmp_path: Path,
    *,
    canary: CanarySpec | None = None,
    lock: BaselineLock | None = None,
    config: QualockConfig | None = None,
    result: QualificationResult | None = None,
    baseline_binary: AgentBinary | None = None,
    candidate_binary: AgentBinary | None = None,
) -> EvidenceProvenance:
    return build_evidence_provenance(
        lock=lock or make_lock(),
        baseline_binary=baseline_binary or make_binary("codex", "0.150.0"),
        candidate_binary=candidate_binary or make_binary("codex", "0.151.0"),
        config=config or make_config(),
        canaries=[canary or make_canary(tmp_path)],
        result=result or make_result(),
    )


def test_build_evidence_provenance_reuses_canary_fingerprint(tmp_path: Path) -> None:
    canary = make_canary(tmp_path)
    provenance = build(tmp_path, canary=canary)
    assert provenance.canaries[0].canary_fingerprint_sha256 == canary_fingerprint(canary)


def test_build_evidence_provenance_hashes_repository_url_exactly(tmp_path: Path) -> None:
    url = "https://example.invalid/private/repo.git"
    canary = make_canary(tmp_path, url=url)
    provenance = build(tmp_path, canary=canary)
    assert provenance.canaries[0].repository_url_sha256 == sha256_canonical(url)


def test_build_evidence_provenance_hashes_run_order_canonically(tmp_path: Path) -> None:
    run_order = (("sample", "baseline", 1), ("sample", "candidate", 1))
    result = make_result(run_order=run_order)
    provenance = build(tmp_path, result=result)
    assert provenance.run_order_sha256 == sha256_canonical(run_order)


def test_build_evidence_provenance_captures_current_package_version_not_lock_version(
    tmp_path: Path,
) -> None:
    lock = make_lock(qualock_version="0.0.1-legacy")
    provenance = build(tmp_path, lock=lock)
    assert provenance.run_qualock_version == qualock.__version__
    assert provenance.run_qualock_version != lock.qualock_version


def test_build_evidence_provenance_allows_null_support_for_non_gemini(tmp_path: Path) -> None:
    provenance = build(tmp_path)
    assert provenance.baseline_identity.support_sha256 is None
    assert provenance.candidate_identity.support_sha256 is None


def test_build_evidence_provenance_requires_and_fills_gemini_support(tmp_path: Path) -> None:
    tree = AgentSupportTree(
        root=Path("/fake/gemini/package"),
        sha256="support-tree-sha",
        container_root="/opt/qualock/gemini-package",
    )
    provenance = build(
        tmp_path,
        lock=make_lock(agent_name="gemini"),
        config=make_config(agent_name="gemini"),
        baseline_binary=make_binary("gemini", "0.58.0", support_trees=(tree,)),
        candidate_binary=make_binary("gemini", "0.59.0", support_trees=(tree,)),
    )
    assert provenance.baseline_identity.support_sha256 is not None
    assert provenance.candidate_identity.support_sha256 is not None


def _valid_payload(tmp_path: Path) -> dict:
    return build(tmp_path).model_dump(mode="json")


def test_evidence_provenance_rejects_extra_fields(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["unexpected"] = "value"
    with pytest.raises(ValidationError):
        EvidenceProvenance.model_validate(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(baseline_lock_sha256="not-hex"),
        lambda p: p.update(run_order_sha256="short"),
        lambda p: p.update(repetitions=0),
        lambda p: p.__setitem__(
            "baseline_identity", {**p["baseline_identity"], "binary_sha256": "short"}
        ),
        lambda p: p.__setitem__(
            "candidate_identity", {**p["candidate_identity"], "support_sha256": "short"}
        ),
        lambda p: p["canaries"][0].update(canary_fingerprint_sha256="bad"),
        lambda p: p["canaries"][0].update(repository_url_sha256="bad"),
        lambda p: p["canaries"][0].update(repository_base_sha="a" * 39),
        lambda p: p["canaries"][0].update(prepared_image_digest="sha256:short"),
    ],
)
def test_evidence_provenance_rejects_malformed_hash_and_count_fields(tmp_path, mutate) -> None:
    payload = _valid_payload(tmp_path)
    mutate(payload)
    with pytest.raises(ValidationError):
        EvidenceProvenance.model_validate(payload)


def test_evidence_provenance_rejects_gemini_identity_missing_support(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["baseline_identity"]["name"] = "gemini"
    payload["baseline_identity"]["support_sha256"] = None
    payload["candidate_identity"]["name"] = "gemini"
    payload["candidate_identity"]["support_sha256"] = None
    with pytest.raises(ValidationError):
        EvidenceProvenance.model_validate(payload)


def test_write_evidence_provenance_is_deterministic_across_machine_paths(tmp_path: Path) -> None:
    canary_a = make_canary(tmp_path / "a")
    canary_b = make_canary(tmp_path / "b")
    provenance_a = build(tmp_path, canary=canary_a, result=make_result(qualification_id="same-q"))
    provenance_b = build(tmp_path, canary=canary_b, result=make_result(qualification_id="same-q"))

    out_a = write_evidence_provenance(tmp_path / "a" / "evidence-provenance.json", provenance_a)
    out_b = write_evidence_provenance(tmp_path / "b" / "evidence-provenance.json", provenance_b)

    assert out_a.read_bytes() == out_b.read_bytes()
    assert out_a.read_bytes().endswith(b"\n")


def test_write_evidence_provenance_refuses_overwrite(tmp_path: Path) -> None:
    provenance = build(tmp_path)
    target = tmp_path / "evidence-provenance.json"
    write_evidence_provenance(target, provenance)
    original = target.read_bytes()

    with pytest.raises(EvidenceProvenanceError):
        write_evidence_provenance(target, provenance)

    assert target.read_bytes() == original


def test_read_evidence_provenance_round_trips(tmp_path: Path) -> None:
    provenance = build(tmp_path)
    target = tmp_path / "evidence-provenance.json"
    write_evidence_provenance(target, provenance)

    assert read_evidence_provenance(target) == provenance


def test_read_evidence_provenance_enforces_bounded_read(tmp_path: Path) -> None:
    provenance = build(tmp_path)
    target = tmp_path / "evidence-provenance.json"
    write_evidence_provenance(target, provenance)
    size = target.stat().st_size

    with pytest.raises(EvidenceProvenanceError) as exc_info:
        read_evidence_provenance(target, max_bytes=size - 1)

    assert str(exc_info.value) == "evidence provenance artifact exceeds maximum size"


def test_read_evidence_provenance_collapses_invalid_payload_to_fixed_message(
    tmp_path: Path,
) -> None:
    target = tmp_path / "evidence-provenance.json"
    target.write_text('{"schema_version": 1, "sentinel-marker": true}\n', encoding="utf-8")

    with pytest.raises(EvidenceProvenanceError) as exc_info:
        read_evidence_provenance(target)

    assert str(exc_info.value) == "evidence provenance artifact is invalid"
    assert "sentinel-marker" not in str(exc_info.value)


def test_written_sidecar_bytes_never_contain_sensitive_canary_material(tmp_path: Path) -> None:
    secret_url = "https://x-access-token:super-secret-token@example.invalid/org/repo.git"
    canary = make_canary(tmp_path, url=secret_url)
    provenance = build(tmp_path, canary=canary)
    target = tmp_path / "evidence-provenance.json"
    write_evidence_provenance(target, provenance)

    raw = target.read_bytes()
    assert b"super-secret-token" not in raw
    assert secret_url.encode("utf-8") not in raw
    assert canary.task.encode("utf-8") not in raw
    for step in canary.setup:
        assert step.encode("utf-8") not in raw
    for command in canary.grader.command:
        assert command.encode("utf-8") not in raw
