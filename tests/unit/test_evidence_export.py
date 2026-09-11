import hashlib
import json
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

import qualock
from qualock.agents.base import AgentBinary, AgentSupportTree
from qualock.canary.models import CanarySpec
from qualock.commands import execute_baseline, execute_check
from qualock.config.io import write_default_config
from qualock.evidence.bundle_models import (
    CANARIES_FILENAME,
    MANIFEST_FILENAME,
    PRICING_FILENAME,
    REPORT_FILENAME,
    EvidenceBundleError,
    EvidenceBundleReason,
    EvidenceManifest,
    PublicReport,
)
from qualock.evidence.export import ExportedEvidenceBundle, export_evidence_bundle
from qualock.evidence.verify import verify_evidence_bundle
from qualock.pricing.sidecar import write_pricing_sidecar
from qualock.qualification.models import AttemptResult, Usage, Verdict
from qualock.run.models import PreparedTarget
from qualock.run.schedule import Side
from tests.unit.evidence_bundle_fixtures import _pricing_payload


class FakeResolver:
    def __init__(self, agent_name: str = "codex") -> None:
        self.agent_name = agent_name
        self.calls: list[str] = []

    def resolve(self, version: str) -> AgentBinary:
        self.calls.append(version)
        exact = "0.151.0" if version == "latest" else version
        support_trees = ()
        if self.agent_name == "gemini":
            support_trees = (
                AgentSupportTree(
                    root=Path(f"/fake/{self.agent_name}/{exact}/package"),
                    sha256=hashlib.sha256(f"support-tree-sha-{exact}".encode()).hexdigest(),
                    container_root="/opt/qualock/gemini-package",
                ),
            )
        return AgentBinary(
            self.agent_name,
            exact,
            Path(f"/fake/{self.agent_name}/{exact}/agent"),
            hashlib.sha256(f"{self.agent_name}-{exact}".encode()).hexdigest(),
            support_trees=support_trees,
        )


class FakeBackend:
    def __init__(
        self,
        success_versions: set[str] | None = None,
        events_jsonl: str = "events-data",
        invalid_reason: str | None = "some invalid reason",
        protected_path_violations: tuple[str, ...] = ("violation",),
    ) -> None:
        self.success_versions = success_versions or {"0.150.0", "0.151.0"}
        self.prepared: list[str] = []
        self.calls: list[tuple[str, str, int]] = []
        self.events_jsonl = events_jsonl
        self.invalid_reason = invalid_reason
        self.protected_path_violations = protected_path_violations

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedTarget:
        self.prepared.append(canary.id)
        digest = hashlib.sha256(f"{qualification_id}-{canary.id}".encode()).hexdigest()
        return PreparedTarget(reference="prepared", digest=f"sha256:{digest}")

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedTarget,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        self.calls.append((canary.id, side.value, repetition))
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=binary.version in self.success_versions,
            valid=True,
            duration_ms=100,
            usage=Usage(input_tokens=10, output_tokens=2, observed=True),
            events_jsonl=self.events_jsonl,
            invalid_reason=self.invalid_reason,
            protected_path_violations=self.protected_path_violations,
        )


def _setup_project(
    root: Path,
    *,
    agent_name: str = "codex",
    repository_url: str = "https://example.invalid/repo.git",
    task: str = "Fix it.",
    setup: list[str] | None = None,
    grader_command: list[str] | None = None,
    protected_paths: list[str] | None = None,
) -> None:
    ub = root / ".qualock"
    (ub / "canaries").mkdir(parents=True, exist_ok=True)
    (ub / "results").mkdir(parents=True, exist_ok=True)
    config_path = ub / "config.yaml"
    write_default_config(config_path)
    if agent_name != "codex":
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        payload["agent"]["name"] = agent_name
        if agent_name == "gemini":
            payload["model"]["id"] = "gemini-3.5-flash"
            payload["model"]["reasoning_effort"] = "provider-default"
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    grader = ub / "canaries/grader.patch"
    grader.write_text("patch", encoding="utf-8")
    canary_data: dict[str, Any] = {
        "schema_version": 1,
        "id": "sample",
        "name": "Sample",
        "repository": {
            "url": repository_url,
            "base_sha": "a" * 40,
        },
        "runtime": {"image": "python:3.12-slim"},
        "task": task,
        "setup": setup or [],
        "agent": {"timeout_seconds": 60},
        "grader": {
            "patch": "grader.patch",
            "command": grader_command or ["pytest", "-q"],
        },
        "constraints": {
            "protected_paths": protected_paths or ["tests/**"],
        },
        "critical": True,
    }
    (ub / "canaries/sample.yaml").write_text(
        yaml.safe_dump(canary_data, sort_keys=False), encoding="utf-8"
    )


def _create_synthetic_qualification(
    root: Path,
    *,
    agent_name: str = "codex",
    baseline_version: str = "0.150.0",
    candidate_version: str = "0.151.0",
    qualification_id: str = "check-q",
    repository_url: str = "https://example.invalid/repo.git",
    task: str = "Fix it.",
    setup: list[str] | None = None,
    grader_command: list[str] | None = None,
    protected_paths: list[str] | None = None,
    events_jsonl: str = "events-data",
    invalid_reason: str | None = "some invalid reason",
    protected_path_violations: tuple[str, ...] = ("violation",),
    max_attempts: int | None = None,
    max_tokens: int | None = None,
    success_versions: set[str] | None = None,
) -> None:
    _setup_project(
        root,
        agent_name=agent_name,
        repository_url=repository_url,
        task=task,
        setup=setup,
        grader_command=grader_command,
        protected_paths=protected_paths,
    )
    resolver = FakeResolver(agent_name=agent_name)
    backend = FakeBackend(
        success_versions=success_versions,
        events_jsonl=events_jsonl,
        invalid_reason=invalid_reason,
        protected_path_violations=protected_path_violations,
    )
    execute_baseline(
        root,
        f"{agent_name}@{baseline_version}",
        resolver=resolver,
        backend=backend,
        qualification_id="base-q",
        created_at="2026-09-01T00:00:00Z",
    )
    execute_check(
        root,
        f"{agent_name}@{candidate_version}",
        resolver=resolver,
        backend=backend,
        qualification_id=qualification_id,
        max_attempts=max_attempts,
        max_tokens=max_tokens,
    )


def test_export_evidence_bundle_happy_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-happy")

    dest = tmp_path / "exported-bundle"
    fixed_time = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    bundle = export_evidence_bundle(
        project_root, "check-happy", dest, created_at=fixed_time
    )

    assert isinstance(bundle, ExportedEvidenceBundle)
    assert bundle.path == dest
    assert bundle.qualification_id == "check-happy"
    assert len(bundle.manifest_sha256) == 64
    assert dest.is_dir()

    manifest_bytes = (dest / MANIFEST_FILENAME).read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == bundle.manifest_sha256

    manifest = EvidenceManifest.model_validate_json(manifest_bytes)
    assert manifest.schema_version == 1
    assert manifest.qualification_id == "check-happy"
    assert manifest.run_qualock_version == qualock.__version__
    assert manifest.exporter_qualock_version == qualock.__version__
    assert manifest.baseline_version == "0.150.0"
    assert manifest.candidate_version == "0.151.0"
    assert manifest.verdict == Verdict.PASS
    assert manifest.created_at == fixed_time.isoformat()

    verified = verify_evidence_bundle(dest)
    assert verified.manifest.qualification_id == "check-happy"


def test_export_evidence_bundle_allows_equal_run_and_exporter_version(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-equal-ver")

    dest = tmp_path / "bundle-equal"
    export_evidence_bundle(project_root, "check-equal-ver", dest)
    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert manifest.run_qualock_version == manifest.exporter_qualock_version
    assert manifest.exporter_qualock_version == qualock.__version__

    verified = verify_evidence_bundle(dest)
    assert verified.manifest.run_qualock_version == verified.manifest.exporter_qualock_version


def test_export_evidence_bundle_preserves_provenance_run_version_distinct_from_exporter(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-custom-ver")

    # Modify the persisted provenance run_qualock_version to simulate an older check run
    prov_file = project_root / ".qualock/results/check-custom-ver/evidence-provenance.json"
    prov_data = json.loads(prov_file.read_text(encoding="utf-8"))
    prov_data["run_qualock_version"] = "0.8.4"
    prov_file.write_text(json.dumps(prov_data), encoding="utf-8")

    dest = tmp_path / "bundle-custom"
    export_evidence_bundle(project_root, "check-custom-ver", dest)

    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert manifest.run_qualock_version == "0.8.4"
    assert manifest.exporter_qualock_version == qualock.__version__

    verified = verify_evidence_bundle(dest)
    assert verified.manifest.run_qualock_version == "0.8.4"
    assert verified.manifest.exporter_qualock_version == qualock.__version__


def test_export_evidence_bundle_secret_redaction_and_projection(
    tmp_path: Path,
) -> None:
    secret_event = "SUPER_SECRET_RAW_EVENT_DATA_12345"
    secret_invalid = "SUPER_SECRET_INVALID_REASON_12345"
    secret_violation = "SUPER_SECRET_VIOLATION_PATH_12345"
    secret_task = "SUPER_SECRET_CANARY_TASK_TEXT_12345"
    secret_setup = "SUPER_SECRET_SETUP_COMMAND_12345"
    secret_grader = "SUPER_SECRET_GRADER_CMD_12345"
    secret_protected = "SUPER_SECRET_PROTECTED_DIR_12345"

    project_root = tmp_path / "project"
    _create_synthetic_qualification(
        project_root,
        qualification_id="check-secrets",
        task=secret_task,
        setup=[secret_setup],
        grader_command=[secret_grader],
        protected_paths=[secret_protected],
        events_jsonl=secret_event,
        invalid_reason=secret_invalid,
        protected_path_violations=(secret_violation,),
    )

    dest = tmp_path / "secret-free-bundle"
    export_evidence_bundle(project_root, "check-secrets", dest)

    # Validate report.json attempt fields exactly
    report_bytes = (dest / REPORT_FILENAME).read_bytes()
    report_data = json.loads(report_bytes.decode("utf-8"))
    for execution in report_data["executions"]:
        for attempt in execution["attempts"]:
            assert set(attempt.keys()) == {
                "side",
                "repetition",
                "success",
                "valid",
                "duration_ms",
                "usage",
                "events_sha256",
            }
            assert attempt["events_sha256"] == hashlib.sha256(secret_event.encode()).hexdigest()
            assert "events_jsonl" not in attempt
            assert "invalid_reason" not in attempt
            assert "protected_path_violations" not in attempt

    # Validate canaries.json fields exactly
    canaries_bytes = (dest / CANARIES_FILENAME).read_bytes()
    canaries_data = json.loads(canaries_bytes.decode("utf-8"))
    for canary in canaries_data["canaries"]:
        assert set(canary.keys()) == {
            "canary_id",
            "critical",
            "repository_url",
            "repository_url_sha256",
            "base_sha",
            "canary_fingerprint_sha256",
            "prepared_image_digest",
            "repetitions",
        }
        assert "task" not in canary
        assert "setup" not in canary
        assert "grader" not in canary
        assert "constraints" not in canary

    # Full byte scan across all files in output bundle
    all_secrets = [
        secret_event,
        secret_invalid,
        secret_violation,
        secret_task,
        secret_setup,
        secret_grader,
        secret_protected,
    ]
    for child in dest.iterdir():
        file_bytes = child.read_bytes()
        for secret in all_secrets:
            assert secret.encode("utf-8") not in file_bytes, f"Secret leaked in {child.name}"

    verify_evidence_bundle(dest)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://user:pass@example.invalid/repo.git",
        "https://example.invalid/repo.git?token=secret123",
        "https://example.invalid/repo.git#frag",
        "ssh://git@example.invalid/repo.git",
        "file:///local/path",
    ],
)
def test_export_evidence_bundle_rejects_unsafe_repository_url_before_destination_creation(
    tmp_path: Path, unsafe_url: str
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(
        project_root,
        qualification_id="check-unsafe-url",
        repository_url=unsafe_url,
    )

    dest = tmp_path / "bundle-unsafe"
    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "check-unsafe-url", dest)

    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_REPOSITORY_URL
    assert not dest.exists()


def test_export_evidence_bundle_purity_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-pure")

    # Install fail-if-called sentinels on forbidden execution surfaces
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Forbidden side-effect called during evidence export")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("qualock.run.process.run_process", forbidden)
    monkeypatch.setattr("qualock.run.docker.DockerRunner.__init__", forbidden)
    monkeypatch.setattr("qualock.agents.resolver.CodexResolver.resolve", forbidden)
    monkeypatch.setattr("qualock.agents.claude_resolver.ClaudeResolver.resolve", forbidden)
    monkeypatch.setattr("qualock.agents.gemini_resolver.GeminiResolver.resolve", forbidden)
    monkeypatch.setattr("qualock.agents.antigravity_resolver.AntigravityResolver.resolve", forbidden)
    monkeypatch.setattr("qualock.agents.releases.default_agent_cache_root", forbidden)

    dest = tmp_path / "bundle-pure"
    export_evidence_bundle(project_root, "check-pure", dest)
    assert dest.is_dir()
    verify_evidence_bundle(dest)


def test_export_evidence_bundle_refuses_existing_destination(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-dest-exists")

    dest = tmp_path / "existing-dest"
    dest.mkdir(parents=True)
    sentinel_file = dest / "sentinel.txt"
    sentinel_file.write_text("keep me", encoding="utf-8")

    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "check-dest-exists", dest)

    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH
    assert sentinel_file.read_text(encoding="utf-8") == "keep me"


def test_export_evidence_bundle_rejects_destination_inside_source_qualification_dir(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-inside")

    source_dir = project_root / ".qualock/results/check-inside"
    dest = source_dir / "nested-bundle"

    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "check-inside", dest)

    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH
    assert not dest.exists()


@pytest.mark.parametrize(
    "bad_qid",
    [
        "../traversal",
        "/absolute/path",
        "..",
        ".",
        "nested/path",
        "nested\\path",
    ],
)
def test_export_evidence_bundle_rejects_unsafe_qualification_id_traversal(
    tmp_path: Path, bad_qid: str
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-safe")

    dest = tmp_path / "bundle-traversal"
    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, bad_qid, dest)

    assert exc_info.value.reason is EvidenceBundleReason.UNSAFE_PATH
    assert not dest.exists()


def test_export_evidence_bundle_rejects_nonexistent_qualification_id(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-exists")

    dest = tmp_path / "bundle-missing"
    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "nonexistent-qid", dest)

    assert exc_info.value.reason in (
        EvidenceBundleReason.UNSAFE_PATH,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    assert not dest.exists()


@pytest.mark.parametrize(
    "missing_filename",
    [
        "report.json",
        "qualification.json",
        "evidence-provenance.json",
    ],
)
def test_export_evidence_bundle_rejects_missing_required_source_artifacts(
    tmp_path: Path, missing_filename: str
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-incomplete-src")

    artifact = project_root / f".qualock/results/check-incomplete-src/{missing_filename}"
    artifact.unlink()

    dest = tmp_path / "bundle-missing-art"
    with pytest.raises(EvidenceBundleError):
        export_evidence_bundle(project_root, "check-incomplete-src", dest)

    assert not dest.exists()


def test_export_evidence_bundle_rejects_baseline_only_qualification(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _setup_project(project_root)
    resolver = FakeResolver()
    backend = FakeBackend()
    execute_baseline(
        project_root,
        "codex@0.150.0",
        resolver=resolver,
        backend=backend,
        qualification_id="baseline-only-qid",
    )

    dest = tmp_path / "bundle-baseline-only"
    with pytest.raises(EvidenceBundleError):
        export_evidence_bundle(project_root, "baseline-only-qid", dest)

    assert not dest.exists()


def test_export_evidence_bundle_rejects_stale_canary_definition(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-stale-suite")

    # Modify canary grader patch after check completed
    patch_file = project_root / ".qualock/canaries/grader.patch"
    patch_file.write_text("tampered patch content", encoding="utf-8")

    dest = tmp_path / "bundle-stale-suite"
    with pytest.raises((EvidenceBundleError, ValueError)):
        export_evidence_bundle(project_root, "check-stale-suite", dest)

    assert not dest.exists()


def test_export_evidence_bundle_rejects_stale_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-stale-cfg")

    # Modify config repetitions after check completed
    config_file = project_root / ".qualock/config.yaml"
    cfg = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    cfg["qualification"]["repetitions"] = 5
    config_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    dest = tmp_path / "bundle-stale-cfg"
    with pytest.raises((EvidenceBundleError, ValueError)):
        export_evidence_bundle(project_root, "check-stale-cfg", dest)

    assert not dest.exists()


def test_export_evidence_bundle_rejects_tampered_baseline_lock(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-tampered-lock")

    lock_file = project_root / ".qualock/baseline.lock"
    lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    lock_data["agent"]["binary_sha256"] = "b" * 64
    lock_file.write_text(json.dumps(lock_data), encoding="utf-8")

    dest = tmp_path / "bundle-tampered-lock"
    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "check-tampered-lock", dest)

    assert exc_info.value.reason in (
        EvidenceBundleReason.IDENTITY_MISMATCH,
        EvidenceBundleReason.MALFORMED_PAYLOAD,
    )
    assert not dest.exists()


def test_export_evidence_bundle_includes_valid_pricing_sidecar(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-pricing")

    # Add valid pricing sidecar in source qualification directory
    qid_dir = project_root / ".qualock/results/check-pricing"
    report_data = json.loads((qid_dir / REPORT_FILENAME).read_text(encoding="utf-8"))
    pricing_data = _pricing_payload("check-pricing", report_data["executions"])
    (qid_dir / PRICING_FILENAME).unlink(missing_ok=True)
    write_pricing_sidecar(qid_dir, pricing_data)

    dest = tmp_path / "bundle-priced"
    export_evidence_bundle(project_root, "check-pricing", dest)

    assert (dest / PRICING_FILENAME).is_file()
    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert PRICING_FILENAME in manifest.files

    verified = verify_evidence_bundle(dest)
    assert PRICING_FILENAME in verified.manifest.files


def test_export_evidence_bundle_omits_malformed_pricing_sidecar(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-malformed-pricing")

    # Write malformed pricing sidecar in source qualification directory
    qid_dir = project_root / ".qualock/results/check-malformed-pricing"
    (qid_dir / PRICING_FILENAME).write_text("{\"corrupted_json\": true", encoding="utf-8")

    dest = tmp_path / "bundle-unpriced"
    export_evidence_bundle(project_root, "check-malformed-pricing", dest)

    # Malformed pricing is omitted, export succeeds cleanly
    assert not (dest / PRICING_FILENAME).exists()
    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert PRICING_FILENAME not in manifest.files

    verified = verify_evidence_bundle(dest)
    assert PRICING_FILENAME not in verified.manifest.files


def test_export_evidence_bundle_deterministic_output_with_pinned_created_at(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-det")

    dest_a = tmp_path / "bundle-a"
    dest_b = tmp_path / "bundle-b"
    pinned_time = datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)

    bundle_a = export_evidence_bundle(project_root, "check-det", dest_a, created_at=pinned_time)
    bundle_b = export_evidence_bundle(project_root, "check-det", dest_b, created_at=pinned_time)

    assert bundle_a.manifest_sha256 == bundle_b.manifest_sha256

    files_a = sorted(p.name for p in dest_a.iterdir())
    files_b = sorted(p.name for p in dest_b.iterdir())
    assert files_a == files_b

    for filename in files_a:
        assert (dest_a / filename).read_bytes() == (dest_b / filename).read_bytes()


def test_export_evidence_bundle_without_created_at_captures_utc_timestamp(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-notime")

    dest = tmp_path / "bundle-notime"
    export_evidence_bundle(project_root, "check-notime", dest)

    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    ts = datetime.fromisoformat(manifest.created_at)
    assert ts.tzinfo is not None


def test_export_evidence_bundle_cleanup_on_verification_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-fail-verify")

    dest = tmp_path / "target-dir"

    def fail_verify(*args: Any, **kwargs: Any) -> Any:
        raise EvidenceBundleError(EvidenceBundleReason.VERDICT_MISMATCH, "verdict")

    # Patch the verifier inside export module
    monkeypatch.setattr("qualock.evidence.export.verify_evidence_bundle", fail_verify)

    with pytest.raises(EvidenceBundleError) as exc_info:
        export_evidence_bundle(project_root, "check-fail-verify", dest)

    assert exc_info.value.reason is EvidenceBundleReason.VERDICT_MISMATCH
    assert not dest.exists()

    # Confirm no sibling temp dirs left behind in destination's parent
    remaining = list(tmp_path.glob(".export-*")) + list(tmp_path.glob(".*tmp*"))
    assert not remaining


def test_export_evidence_bundle_derives_policy_and_does_not_copy_arbitrary_reasons(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(project_root, qualification_id="check-reasons")

    # Tamper local report execution reason with arbitrary text
    report_file = project_root / ".qualock/results/check-reasons/report.json"
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    report_data["executions"][0]["reason"] = "LOCAL_ARBITRARY_UNTRUSTED_REASON"
    report_data["reasons"] = ["LOCAL_ARBITRARY_UNTRUSTED_REASON"]
    report_file.write_text(json.dumps(report_data), encoding="utf-8")

    dest = tmp_path / "bundle-policy-derived"
    export_evidence_bundle(project_root, "check-reasons", dest)

    exported_report = PublicReport.model_validate_json((dest / REPORT_FILENAME).read_bytes())
    assert "LOCAL_ARBITRARY_UNTRUSTED_REASON" not in exported_report.executions[0].reason
    assert "LOCAL_ARBITRARY_UNTRUSTED_REASON" not in exported_report.reasons

    verify_evidence_bundle(dest)


def test_export_evidence_bundle_budget_stopped_incomplete(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    # max_attempts=1 causes incomplete run
    _create_synthetic_qualification(
        project_root, qualification_id="check-incomplete", max_attempts=1
    )

    dest = tmp_path / "bundle-incomplete"
    export_evidence_bundle(project_root, "check-incomplete", dest)

    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert manifest.verdict == Verdict.INCOMPLETE
    assert not manifest.completeness.all_canaries_complete
    assert manifest.completeness.max_attempts == 1

    verified = verify_evidence_bundle(dest)
    assert verified.manifest.verdict == Verdict.INCOMPLETE


def test_export_evidence_bundle_gemini_agent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _create_synthetic_qualification(
        project_root, agent_name="gemini", qualification_id="check-gemini"
    )

    dest = tmp_path / "bundle-gemini"
    export_evidence_bundle(project_root, "check-gemini", dest)

    manifest = EvidenceManifest.model_validate_json((dest / MANIFEST_FILENAME).read_bytes())
    assert manifest.baseline_identity.name == "gemini"
    assert manifest.baseline_identity.support_sha256 is not None
    assert manifest.candidate_identity.support_sha256 is not None

    verified = verify_evidence_bundle(dest)
    assert verified.manifest.baseline_identity.support_sha256 is not None
