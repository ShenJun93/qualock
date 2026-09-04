from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from platformdirs import user_cache_dir

from qualock import __version__
from qualock.agents.base import AgentBinary
from qualock.agents.codex import CodexAdapter
from qualock.agents.resolver import CodexResolver
from qualock.baseline.io import (
    BaselineStaleError,
    assert_suite_fresh,
    read_baseline_lock,
    write_baseline_lock,
)
from qualock.baseline.models import AgentPin, BaselineLock, CanaryStability, ModelPin
from qualock.config.models import QualockConfig
from qualock.evidence.storage import write_baseline_artifacts, write_qualification_artifacts
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import AttemptResult, QualificationResult
from qualock.run.backend import DockerQualificationBackend, IntegrityPolicy
from qualock.run.docker import DockerRunner
from qualock.run.executor import QualificationBackend, QualificationExecutor
from qualock.run.schedule import Side
from qualock.source.git import GitSourceManager


class Resolver(Protocol):
    def resolve(self, version: str) -> AgentBinary: ...


class CommandError(ValueError):
    pass


class BaselineUnstableError(RuntimeError):
    pass


def parse_agent_spec(spec: str) -> tuple[str, str]:
    if "@" not in spec:
        raise CommandError("agent spec must look like codex@<version>")
    name, version = spec.rsplit("@", 1)
    if name != "codex" or not version:
        raise CommandError("v0.1 supports only codex@<version>")
    return name, version


def _qualification_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _default_resolver() -> CodexResolver:
    return CodexResolver(Path(user_cache_dir("qualock")))


def _default_backend(root: Path, config: QualockConfig) -> DockerQualificationBackend:
    cache = Path(user_cache_dir("qualock"))
    auth_home = Path.home() / ".codex"
    return DockerQualificationBackend(
        source_manager=GitSourceManager(cache),
        docker_runner=DockerRunner(),
        agent_adapter=CodexAdapter(auth_home=auth_home if auth_home.exists() else None),
        model=config.model.effective_model,
        reasoning_effort=config.model.reasoning_effort,
        work_root=project_dir(root) / "work",
        integrity_policy=IntegrityPolicy(
            reject_web_search=config.integrity.reject_web_search,
            reject_mcp_calls=config.integrity.reject_mcp_calls,
            reject_protected_path_changes=config.integrity.reject_protected_path_changes,
        ),
    )


def execute_baseline(
    root: Path,
    agent_spec: str,
    *,
    resolver: Resolver | None = None,
    backend: QualificationBackend | None = None,
    qualification_id: str | None = None,
    created_at: str | None = None,
) -> BaselineLock:
    _name, version = parse_agent_spec(agent_spec)
    config, canaries = load_project(root)
    if not canaries:
        raise CommandError("no canaries found")
    resolver = resolver or _default_resolver()
    binary = resolver.resolve(version)
    backend = backend or _default_backend(root, config)
    qid = qualification_id or _qualification_id("baseline")
    stability: dict[str, CanaryStability] = {}
    attempt_evidence: dict[str, list[AttemptResult]] = {}

    for canary in canaries:
        prepared = backend.prepare(canary, qid)
        attempts = [
            backend.run_attempt(
                canary=canary,
                prepared=prepared,
                binary=binary,
                side=Side.BASELINE,
                repetition=repetition,
            )
            for repetition in range(1, config.qualification.repetitions + 1)
        ]
        attempt_evidence[canary.id] = attempts
        valid_runs = sum(item.valid for item in attempts)
        successes = sum(item.valid and item.success for item in attempts)
        stability[canary.id] = CanaryStability(valid_runs=valid_runs, successes=successes)
        if canary.critical and (
            valid_runs != config.qualification.repetitions
            or successes != config.qualification.repetitions
        ):
            write_baseline_artifacts(
                project_dir(root) / "results",
                qid,
                binary.version,
                attempt_evidence,
            )
            raise BaselineUnstableError(
                f"critical canary {canary.id} is not stable: {successes}/{valid_runs}"
            )

    write_baseline_artifacts(
        project_dir(root) / "results",
        qid,
        binary.version,
        attempt_evidence,
    )
    lock = BaselineLock(
        schema_version=1,
        created_at=created_at or datetime.now(UTC).isoformat(),
        agent=AgentPin(name="codex", version=binary.version, binary_sha256=binary.sha256),
        model=ModelPin(
            id=config.model.id,
            snapshot=config.model.snapshot,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version=__version__,
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries=stability,
    )
    write_baseline_lock(project_dir(root) / "baseline.lock", lock)
    return lock


def execute_check(
    root: Path,
    candidate_spec: str,
    *,
    resolver: Resolver | None = None,
    backend: QualificationBackend | None = None,
    qualification_id: str | None = None,
) -> QualificationResult:
    _name, candidate_version = parse_agent_spec(candidate_spec)
    config, canaries = load_project(root)
    if not canaries:
        raise CommandError("no canaries found")
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))

    resolver = resolver or _default_resolver()
    baseline_binary = resolver.resolve(lock.agent.version)
    if baseline_binary.sha256 != lock.agent.binary_sha256:
        raise BaselineStaleError("baseline binary fingerprint changed")
    candidate_binary = resolver.resolve(candidate_version)
    backend = backend or _default_backend(root, config)
    qid = qualification_id or _qualification_id("check")

    result = QualificationExecutor(
        backend=backend,
        repetitions=config.qualification.repetitions,
    ).run(
        baseline_binary,
        candidate_binary,
        canaries,
        qualification_id=qid,
    )
    write_qualification_artifacts(project_dir(root) / "results", result)
    return result
