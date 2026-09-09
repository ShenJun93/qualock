import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from qualock.agents.base import (
    AgentBinary,
    AgentInvocation,
    AgentMount,
    AgentRuntimeDependency,
    AgentRuntimeOverlay,
    AgentSupportBinary,
    AgentSupportTree,
)
from qualock.agents.support_integrity import (
    AgentSupportIntegrityError,
    fingerprint_support_tree,
)
from qualock.canary.models import CanarySpec, RuntimeSpec
from qualock.evidence.claude_stream_json import parse_claude_stream_json
from qualock.evidence.models import AgentEvidence, AgentEvidenceError
from qualock.qualification.models import Usage
from qualock.run.backend import DockerQualificationBackend, IntegrityPolicy, UnsupportedRuntimeError
from qualock.run.models import AgentStateEvidence, FrozenAgentState, GradeResult, PreparedTarget
from qualock.run.schedule import Side


class FakeSource:
    def materialize(self, url: str, sha: str, destination: Path) -> Path:
        destination.mkdir(parents=True)
        return destination


class FakeAdapter:
    def __init__(
        self,
        *,
        evidence: AgentEvidence | None = None,
        parse_error: str | None = None,
        invocation: AgentInvocation | None = None,
        runtime_dependencies: tuple[AgentRuntimeDependency, ...] = (),
        runtime_overlays: tuple[AgentRuntimeOverlay, ...] = (),
    ) -> None:
        self.evidence = evidence or AgentEvidence(
            input_tokens=20,
            cached_input_tokens=7,
            cache_write_input_tokens=5,
            output_tokens=9,
            reasoning_output_tokens=3,
            usage_observed=True,
        )
        self.parse_error = parse_error
        self._invocation = invocation
        self.runtime_dependencies = runtime_dependencies
        self.runtime_overlays = runtime_overlays

    @contextmanager
    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> Iterator[AgentInvocation]:
        assert model == "gpt-5.3-codex"
        assert reasoning_effort == "high"
        yield self._invocation or AgentInvocation(
            argv=(str(binary.path), "run", prompt),
            container_binary_path="/opt/qualock/fake",
        )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        if self.parse_error is not None:
            raise AgentEvidenceError(self.parse_error)
        return self.evidence


class FakeDocker:
    def __init__(
        self,
        *,
        stdout: str = "opaque agent output",
        stderr: str = "",
        changed_paths: tuple[str, ...] = (),
        exit_code: int | None = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.changed_paths = changed_paths
        self.exit_code = exit_code
        self.grader_calls = 0
        self.mounts: tuple[tuple[Path, str, str], ...] = ()
        self.environment: dict[str, str] = {}
        self.tmpfs_mounts: tuple[str, ...] = ()
        self.bootstrap_copy: tuple[str, str] | None = None
        self.stdin_bootstrap: tuple[str, str] | None = None
        self.stdin_secret_env: tuple[str, str] | None = None
        self.agent_container_path: str | None = None
        self.removed_containers: list[str] = []
        self.runtime_dependencies: tuple[AgentRuntimeDependency, ...] = ()
        self.runtime_overlays: tuple[AgentRuntimeOverlay, ...] = ()
        self.run_agent_calls = 0
        self.agent_binary: Path | None = None
        self.agent_binary_bytes: bytes | None = None
        self.mount_bytes: dict[str, dict[str, bytes] | bytes] = {}

    def prepare(
        self,
        source_dir: Path,
        canary: CanarySpec,
        *,
        image_tag: str,
        runtime_dependencies: tuple[AgentRuntimeDependency, ...] = (),
        runtime_overlays: tuple[AgentRuntimeOverlay, ...] = (),
        timeout_seconds: float = 1200,
    ) -> PreparedTarget:
        self.runtime_dependencies = runtime_dependencies
        self.runtime_overlays = runtime_overlays
        return PreparedTarget(reference=image_tag, digest="sha256:prepared")

    def run_agent(self, **kwargs: object) -> FrozenAgentState:
        self.run_agent_calls += 1
        mounts = kwargs.get("extra_mounts", ())
        environment = kwargs.get("environment", {})
        tmpfs_mounts = kwargs.get("tmpfs_mounts", ())
        bootstrap_copy = kwargs.get("bootstrap_copy")
        stdin_bootstrap = kwargs.get("stdin_bootstrap")
        stdin_secret_env = kwargs.get("stdin_secret_env")
        agent_container_path = kwargs.get("agent_container_path")
        agent_binary = kwargs.get("agent_binary")
        assert isinstance(mounts, (tuple, list))
        assert isinstance(environment, dict)
        assert isinstance(tmpfs_mounts, (tuple, list))
        assert isinstance(agent_container_path, str)
        assert isinstance(agent_binary, Path)
        self.mounts = tuple(mounts)
        self.environment = {str(key): str(value) for key, value in environment.items()}
        self.tmpfs_mounts = tuple(str(item) for item in tmpfs_mounts)
        self.bootstrap_copy = bootstrap_copy if isinstance(bootstrap_copy, tuple) else None
        self.stdin_bootstrap = stdin_bootstrap if isinstance(stdin_bootstrap, tuple) else None
        self.stdin_secret_env = stdin_secret_env if isinstance(stdin_secret_env, tuple) else None
        self.agent_container_path = agent_container_path
        self.agent_binary = agent_binary
        self.agent_binary_bytes = agent_binary.read_bytes()
        self.mount_bytes = {}
        for host_path, container_path, _mode in self.mounts:
            if host_path.is_dir():
                self.mount_bytes[container_path] = {
                    path.relative_to(host_path).as_posix(): path.read_bytes()
                    for path in host_path.rglob("*")
                    if path.is_file()
                }
            else:
                self.mount_bytes[container_path] = host_path.read_bytes()
        return FrozenAgentState(
            reference="frozen",
            digest="sha256:frozen",
            container_name=str(kwargs["container_name"]),
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            elapsed_ms=123,
        )

    def inspect_agent_state(self, state: FrozenAgentState) -> AgentStateEvidence:
        return AgentStateEvidence(changed_paths=self.changed_paths, patch="diff")

    def run_grader(self, **kwargs: object) -> GradeResult:
        self.grader_calls += 1
        return GradeResult(exit_code=0, stdout="1 passed", stderr="", timed_out=False)

    def remove_container(self, container_name: str) -> None:
        self.removed_containers.append(container_name)


def canary(tmp_path: Path) -> CanarySpec:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": "sample",
            "name": "Sample",
            "repository": {
                "url": "https://example.invalid/repo.git",
                "base_sha": "a" * 40,
            },
            "runtime": {"image": "python:3.12-slim"},
            "task": "Fix it",
            "setup": [],
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(grader), "command": ["pytest -q"]},
            "constraints": {"protected_paths": ["tests/**"]},
            "critical": True,
        }
    )


def binary(tmp_path: Path) -> AgentBinary:
    path = tmp_path / "cache/agents/codex/0.150.0/node_modules/.bin/codex"
    path.parent.mkdir(parents=True)
    path.write_text("fake", encoding="utf-8")
    return AgentBinary("codex", "0.150.0", path, "abc")


def binary_with_support_tree(tmp_path: Path) -> AgentBinary:
    package_root = tmp_path / "gemini-package"
    entrypoint = package_root / "bundle/gemini.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("entry", encoding="utf-8")
    (package_root / "nested.js").write_text("nested", encoding="utf-8")
    return AgentBinary(
        "gemini",
        "0.58.0",
        entrypoint,
        hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
        support_trees=(
            AgentSupportTree(
                package_root,
                fingerprint_support_tree(package_root),
                "/opt/qualock/gemini-package",
            ),
        ),
    )


def backend(
    tmp_path: Path,
    docker: FakeDocker,
    *,
    adapter: FakeAdapter | None = None,
) -> DockerQualificationBackend:
    return DockerQualificationBackend(
        source_manager=FakeSource(),
        docker_runner=docker,
        agent_adapter=adapter or FakeAdapter(),
        model="gpt-5.3-codex",
        reasoning_effort="high",
        work_root=tmp_path / "work",
        integrity_policy=IntegrityPolicy(
            reject_web_search=True,
            reject_mcp_calls=True,
            reject_protected_path_changes=True,
        ),
    )


def test_prepared_target_is_backend_neutral() -> None:
    assert PreparedTarget("ref", "sha256:x").digest == "sha256:x"


def test_prepare_rejects_non_container_runtime(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker)
    spec = canary(tmp_path).model_copy(update={"runtime": RuntimeSpec(execution="linux-host")})

    with pytest.raises(UnsupportedRuntimeError):
        service.prepare(spec, "q1")


def test_prepare_forwards_agent_runtime_dependencies(tmp_path: Path) -> None:
    dependency = AgentRuntimeDependency(command="socat", apt_package="socat")
    docker = FakeDocker()
    service = backend(
        tmp_path,
        docker,
        adapter=FakeAdapter(runtime_dependencies=(dependency,)),
    )

    service.prepare(canary(tmp_path), "q1")

    assert docker.runtime_dependencies == (dependency,)


def test_prepare_forwards_agent_runtime_overlays(tmp_path: Path) -> None:
    overlay = AgentRuntimeOverlay(
        image="node:22.23.2-bookworm@sha256:" + "a" * 64,
        source_path="/usr/local",
        destination_path="/opt/qualock/node-runtime",
        validation_command=("/opt/qualock/node-runtime/bin/node", "--version"),
    )
    docker = FakeDocker()
    service = backend(
        tmp_path,
        docker,
        adapter=FakeAdapter(runtime_overlays=(overlay,)),
    )

    service.prepare(canary(tmp_path), "q1")

    assert docker.runtime_overlays == (overlay,)


def run_once(tmp_path: Path, service: DockerQualificationBackend, *, side: Side = Side.BASELINE):
    spec = canary(tmp_path)
    return service.run_attempt(
        canary=spec,
        prepared=PreparedTarget("p", "sha256:p"),
        binary=binary(tmp_path),
        side=side,
        repetition=1,
    )


def test_normalized_agent_evidence_is_graded_and_usage_is_recorded(tmp_path: Path) -> None:
    docker = FakeDocker(stdout="this is intentionally not Codex JSONL")
    service = backend(tmp_path, docker)
    result = run_once(tmp_path, service)
    assert result.valid is True
    assert result.success is True
    assert result.duration_ms == 123
    assert result.usage == Usage(
        input_tokens=20,
        cached_input_tokens=7,
        cache_write_input_tokens=5,
        output_tokens=9,
        reasoning_output_tokens=3,
        observed=True,
    )
    assert result.usage.total_tokens == 29
    assert docker.grader_calls == 1


def test_invalid_attempt_after_successful_parse_preserves_usage_exactly(
    tmp_path: Path,
) -> None:
    docker = FakeDocker(changed_paths=("tests/test_hidden.py",))
    service = backend(tmp_path, docker)
    result = run_once(tmp_path, service, side=Side.CANDIDATE)

    assert result.valid is False
    assert result.usage == Usage(
        input_tokens=20,
        cached_input_tokens=7,
        cache_write_input_tokens=5,
        output_tokens=9,
        reasoning_output_tokens=3,
        observed=True,
    )
    assert result.usage.total_tokens == 29
    assert docker.grader_calls == 0


def test_early_invalid_attempts_retain_default_unobserved_usage(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker, adapter=FakeAdapter(parse_error="bad evidence"))
    result = run_once(tmp_path, service)

    assert result.valid is False
    assert result.usage == Usage()
    assert result.usage.observed is False

    escaping_root = tmp_path / "escaping"
    escaping_root.mkdir()
    docker_escaping = FakeDocker(changed_paths=("../outside.py",))
    escaping_result = run_once(escaping_root, backend(escaping_root, docker_escaping))
    assert escaping_result.valid is False
    assert "escapes repository" in (escaping_result.invalid_reason or "")
    assert escaping_result.usage == Usage()
    assert escaping_result.usage.observed is False


def test_timeout_and_reported_errors_after_successful_parse_carry_evidence_usage(
    tmp_path: Path,
) -> None:
    docker = FakeDocker(exit_code=None)
    result = run_once(tmp_path, backend(tmp_path, docker))

    assert result.valid is False
    assert result.invalid_reason == "agent timed out"
    assert result.usage == Usage(
        input_tokens=20,
        cached_input_tokens=7,
        cache_write_input_tokens=5,
        output_tokens=9,
        reasoning_output_tokens=3,
        observed=True,
    )


def test_real_claude_tool_failure_does_not_invalidate_successful_attempt(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/claude/stream_json_bash_failure_success_2_1_260.jsonl")
    evidence = parse_claude_stream_json(fixture.read_text(encoding="utf-8").splitlines())
    docker = FakeDocker()
    result = run_once(tmp_path, backend(tmp_path, docker, adapter=FakeAdapter(evidence=evidence)))

    assert [(item.command, item.exit_code) for item in evidence.commands] == [("false", 1)]
    assert evidence.errors == []
    assert result.valid is True
    assert result.success is True
    assert docker.grader_calls == 1


def test_backend_forwards_generic_invocation_runtime(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text("seed", encoding="utf-8")
    invocation = AgentInvocation(
        argv=(str(tmp_path / "agent"), "run", "Fix it"),
        environment=(("FAKE_HOME", "/opt/fake/home"),),
        mounts=(AgentMount(seed, "/opt/fake/seed.json", "ro"),),
        tmpfs_mounts=("/opt/fake/home",),
        bootstrap_copy=("/opt/fake/seed.json", "/opt/fake/home/config.json"),
        stdin_secret_env=("FAKE_SECRET", "secret"),
        container_binary_path="/opt/qualock/fake",
    )
    docker = FakeDocker()
    result = run_once(
        tmp_path, backend(tmp_path, docker, adapter=FakeAdapter(invocation=invocation))
    )
    assert result.valid is True
    assert docker.environment == {"FAKE_HOME": "/opt/fake/home"}
    assert (seed, "/opt/fake/seed.json", "ro") in docker.mounts
    assert docker.tmpfs_mounts == ("/opt/fake/home",)
    assert docker.bootstrap_copy == ("/opt/fake/seed.json", "/opt/fake/home/config.json")
    assert docker.stdin_secret_env == ("FAKE_SECRET", "secret")
    assert docker.agent_container_path == "/opt/qualock/fake"


def test_timeout_invalidates_attempt_and_cleans_container_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker(exit_code=None)
    service = backend(tmp_path, docker)
    result = run_once(tmp_path, service)
    assert result.valid is False
    assert result.invalid_reason == "agent timed out"
    assert docker.grader_calls == 0
    assert len(docker.removed_containers) == 1


def test_evidence_parse_failure_cleans_container(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker, adapter=FakeAdapter(parse_error="bad evidence"))
    result = run_once(tmp_path, service)
    assert result.valid is False
    assert result.invalid_reason == "bad evidence"
    assert len(docker.removed_containers) == 1


def test_agent_failure_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker(exit_code=1)
    service = backend(
        tmp_path,
        docker,
        adapter=FakeAdapter(evidence=AgentEvidence(errors=["model unsupported"])),
    )
    result = run_once(tmp_path, service)
    assert result.valid is False
    assert "agent exited with code 1" in (result.invalid_reason or "")
    assert "agent reported error" in (result.invalid_reason or "")
    assert docker.grader_calls == 0


def test_web_search_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker, adapter=FakeAdapter(evidence=AgentEvidence(web_searches=1)))
    result = run_once(tmp_path, service, side=Side.CANDIDATE)
    assert result.valid is False
    assert "web search" in (result.invalid_reason or "")
    assert docker.grader_calls == 0


def test_mcp_call_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker, adapter=FakeAdapter(evidence=AgentEvidence(mcp_calls=1)))
    result = run_once(tmp_path, service, side=Side.CANDIDATE)
    assert result.valid is False
    assert "MCP call" in (result.invalid_reason or "")
    assert docker.grader_calls == 0


def test_protected_path_change_invalidates_attempt(tmp_path: Path) -> None:
    docker = FakeDocker(changed_paths=("tests/test_hidden.py",))
    service = backend(tmp_path, docker)
    result = run_once(tmp_path, service, side=Side.CANDIDATE)
    assert result.valid is False
    assert result.protected_path_violations == ("tests/test_hidden.py",)
    assert docker.grader_calls == 0


def test_adapter_evidence_error_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker()
    service = backend(tmp_path, docker, adapter=FakeAdapter(parse_error="bad evidence"))
    result = run_once(tmp_path, service)
    assert result.valid is False
    assert result.invalid_reason == "bad evidence"
    assert docker.grader_calls == 0


def test_agent_support_binary_is_mounted_read_only(tmp_path: Path) -> None:
    main = binary(tmp_path)
    host = main.path.with_name("codex-code-mode-host")
    host.write_text("host", encoding="utf-8")
    binary_with_host = AgentBinary(
        main.name,
        main.version,
        main.path,
        main.sha256,
        support_binaries=(
            AgentSupportBinary(
                name="codex-code-mode-host",
                path=host,
                sha256=hashlib.sha256(host.read_bytes()).hexdigest(),
                container_path="/opt/qualock/codex-code-mode-host",
            ),
        ),
    )
    docker = FakeDocker()
    service = backend(tmp_path, docker)
    spec = canary(tmp_path)
    result = service.run_attempt(
        canary=spec,
        prepared=PreparedTarget("p", "sha256:p"),
        binary=binary_with_host,
        side=Side.BASELINE,
        repetition=1,
    )
    assert result.valid is True
    assert len(docker.mounts) == 1
    snapshot_host, destination, mode = docker.mounts[0]
    assert snapshot_host != host
    assert destination == "/opt/qualock/codex-code-mode-host"
    assert mode == "ro"
    assert docker.mount_bytes[destination] == b"host"


def test_backend_forwards_support_tree_and_nested_entrypoint_once(tmp_path: Path) -> None:
    gemini = binary_with_support_tree(tmp_path)
    invocation = AgentInvocation(
        argv=(str(gemini.path), "--version"),
        container_binary_path="/opt/qualock/gemini-package/bundle/gemini.js",
    )
    docker = FakeDocker(stdout="0.58.0\n")
    service = backend(tmp_path, docker, adapter=FakeAdapter(invocation=invocation))

    result = service.run_attempt(
        canary=canary(tmp_path),
        prepared=PreparedTarget("p", "sha256:p"),
        binary=gemini,
        side=Side.BASELINE,
        repetition=1,
    )

    tree = gemini.support_trees[0]
    assert result.valid is True
    assert len(docker.mounts) == 1
    snapshot_root, destination, mode = docker.mounts[0]
    assert snapshot_root != tree.root
    assert destination == tree.container_root
    assert mode == "ro"
    assert docker.mount_bytes[destination] == {
        "bundle/gemini.js": b"entry",
        "nested.js": b"nested",
    }
    assert docker.agent_binary != gemini.path
    assert docker.agent_binary_bytes == gemini.path.read_bytes()
    assert docker.agent_container_path == "/opt/qualock/gemini-package/bundle/gemini.js"


def test_backend_rejects_support_tree_tamper_before_run_agent(tmp_path: Path) -> None:
    gemini = binary_with_support_tree(tmp_path)
    (gemini.support_trees[0].root / "nested.js").write_text("tampered", encoding="utf-8")
    docker = FakeDocker()
    service = backend(tmp_path, docker)

    with pytest.raises(AgentSupportIntegrityError, match="fingerprint"):
        service.run_attempt(
            canary=canary(tmp_path),
            prepared=PreparedTarget("p", "sha256:p"),
            binary=gemini,
            side=Side.BASELINE,
            repetition=1,
        )

    assert docker.run_agent_calls == 0


def test_backend_snapshot_is_immune_to_post_verification_source_mutation(
    tmp_path: Path,
) -> None:
    gemini = binary_with_support_tree(tmp_path)
    original_nested = gemini.support_trees[0].root / "nested.js"
    invocation = AgentInvocation(
        argv=(str(gemini.path), "--version"),
        container_binary_path="/opt/qualock/gemini-package/bundle/gemini.js",
    )

    class MutatingDocker(FakeDocker):
        def run_agent(self, **kwargs: object) -> FrozenAgentState:
            original_nested.write_text("mutated-after-snapshot", encoding="utf-8")
            return super().run_agent(**kwargs)

    docker = MutatingDocker(stdout="0.58.0\n")
    service = backend(tmp_path, docker, adapter=FakeAdapter(invocation=invocation))
    result = service.run_attempt(
        canary=canary(tmp_path),
        prepared=PreparedTarget("p", "sha256:p"),
        binary=gemini,
        side=Side.BASELINE,
        repetition=1,
    )

    assert result.valid is True
    assert docker.mount_bytes["/opt/qualock/gemini-package"] == {
        "bundle/gemini.js": b"entry",
        "nested.js": b"nested",
    }
