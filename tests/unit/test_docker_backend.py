from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from qualock.agents.base import (
    AgentBinary,
    AgentInvocation,
    AgentMount,
    AgentRuntimeDependency,
    AgentSupportBinary,
)
from qualock.canary.models import CanarySpec
from qualock.evidence.models import AgentEvidence, AgentEvidenceError
from qualock.run.backend import DockerQualificationBackend, IntegrityPolicy
from qualock.run.models import AgentStateEvidence, FrozenAgentState, GradeResult, PreparedImage
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
    ) -> None:
        self.evidence = evidence or AgentEvidence(input_tokens=12, output_tokens=3)
        self.parse_error = parse_error
        self._invocation = invocation
        self.runtime_dependencies = runtime_dependencies

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

    def prepare(
        self,
        source_dir: Path,
        canary: CanarySpec,
        *,
        image_tag: str,
        runtime_dependencies: tuple[AgentRuntimeDependency, ...] = (),
        timeout_seconds: float = 1200,
    ) -> PreparedImage:
        self.runtime_dependencies = runtime_dependencies
        return PreparedImage(reference=image_tag, digest="sha256:prepared")

    def run_agent(self, **kwargs: object) -> FrozenAgentState:
        mounts = kwargs.get("extra_mounts", ())
        environment = kwargs.get("environment", {})
        tmpfs_mounts = kwargs.get("tmpfs_mounts", ())
        bootstrap_copy = kwargs.get("bootstrap_copy")
        stdin_bootstrap = kwargs.get("stdin_bootstrap")
        stdin_secret_env = kwargs.get("stdin_secret_env")
        agent_container_path = kwargs.get("agent_container_path")
        assert isinstance(mounts, (tuple, list))
        assert isinstance(environment, dict)
        assert isinstance(tmpfs_mounts, (tuple, list))
        assert isinstance(agent_container_path, str)
        self.mounts = tuple(mounts)
        self.environment = {str(key): str(value) for key, value in environment.items()}
        self.tmpfs_mounts = tuple(str(item) for item in tmpfs_mounts)
        self.bootstrap_copy = bootstrap_copy if isinstance(bootstrap_copy, tuple) else None
        self.stdin_bootstrap = stdin_bootstrap if isinstance(stdin_bootstrap, tuple) else None
        self.stdin_secret_env = stdin_secret_env if isinstance(stdin_secret_env, tuple) else None
        self.agent_container_path = agent_container_path
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


def run_once(tmp_path: Path, service: DockerQualificationBackend, *, side: Side = Side.BASELINE):
    spec = canary(tmp_path)
    return service.run_attempt(
        canary=spec,
        prepared=PreparedImage("p", "sha256:p"),
        binary=binary(tmp_path),
        side=side,
        repetition=1,
    )


def test_normalized_agent_evidence_is_graded_and_usage_is_recorded(tmp_path: Path) -> None:
    docker = FakeDocker(stdout="this is intentionally not Codex JSONL")
    service = backend(
        tmp_path,
        docker,
        adapter=FakeAdapter(evidence=AgentEvidence(input_tokens=12, output_tokens=3)),
    )
    result = run_once(tmp_path, service)
    assert result.valid is True
    assert result.success is True
    assert result.duration_ms == 123
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
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
    result = run_once(tmp_path, backend(tmp_path, docker, adapter=FakeAdapter(invocation=invocation)))
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
                sha256="host-sha",
                container_path="/opt/qualock/codex-code-mode-host",
            ),
        ),
    )
    docker = FakeDocker()
    service = backend(tmp_path, docker)
    spec = canary(tmp_path)
    result = service.run_attempt(
        canary=spec,
        prepared=PreparedImage("p", "sha256:p"),
        binary=binary_with_host,
        side=Side.BASELINE,
        repetition=1,
    )
    assert result.valid is True
    assert (host, "/opt/qualock/codex-code-mode-host", "ro") in docker.mounts
