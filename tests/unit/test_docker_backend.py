from pathlib import Path

from qualock.agents.base import AgentBinary, AgentCapabilities
from qualock.canary.models import CanarySpec
from qualock.qualification.models import Verdict
from qualock.run.backend import DockerQualificationBackend, IntegrityPolicy
from qualock.run.models import AgentStateEvidence, FrozenAgentState, GradeResult, PreparedImage
from qualock.run.schedule import Side


class FakeSource:
    def materialize(self, url: str, sha: str, destination: Path) -> Path:
        destination.mkdir(parents=True)
        return destination


class FakeAdapter:
    def detect_capabilities(self, binary: Path) -> AgentCapabilities:
        return AgentCapabilities(True, True, True, True, True, True, True)

    def build_exec_argv(
        self,
        binary: AgentBinary,
        capabilities: AgentCapabilities,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> list[str]:
        assert reasoning_effort == "high"
        return [str(binary.path), "exec", "--json", prompt]


class FakeDocker:
    def __init__(self, *, stdout: str, changed_paths: tuple[str, ...] = ()) -> None:
        self.stdout = stdout
        self.changed_paths = changed_paths
        self.grader_calls = 0
        self.auth_mount: tuple[Path, str, str] | None = None
        self.auth_json_contents: str | None = None

    def prepare(self, source_dir: Path, canary: CanarySpec, *, image_tag: str, timeout_seconds: float = 1200) -> PreparedImage:
        return PreparedImage(reference=image_tag, digest="sha256:prepared")

    def run_agent(self, **kwargs: object) -> FrozenAgentState:
        mounts = kwargs.get("extra_mounts", ())
        if mounts:
            mount = mounts[0]
            assert isinstance(mount, tuple)
            self.auth_mount = mount
            self.auth_json_contents = (mount[0] / "auth.json").read_text(encoding="utf-8")
        return FrozenAgentState(
            reference="frozen",
            digest="sha256:frozen",
            container_name=str(kwargs["container_name"]),
            stdout=self.stdout,
            stderr="",
            exit_code=0,
            elapsed_ms=123,
        )

    def inspect_agent_state(self, state: FrozenAgentState) -> AgentStateEvidence:
        return AgentStateEvidence(changed_paths=self.changed_paths, patch="diff")

    def run_grader(self, **kwargs: object) -> GradeResult:
        self.grader_calls += 1
        return GradeResult(exit_code=0, stdout="1 passed", stderr="", timed_out=False)

    def remove_container(self, container_name: str) -> None:
        pass


def canary(tmp_path: Path) -> CanarySpec:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate({
        "schema_version": 1,
        "id": "sample",
        "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"},
        "task": "Fix it",
        "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(grader), "command": ["pytest -q"]},
        "constraints": {"protected_paths": ["tests/**"]},
        "critical": True,
    })


def binary(tmp_path: Path) -> AgentBinary:
    path = tmp_path / "cache/agents/codex/0.150.0/node_modules/.bin/codex"
    path.parent.mkdir(parents=True)
    path.write_text("fake", encoding="utf-8")
    return AgentBinary("codex", "0.150.0", path, "abc")


def backend(
    tmp_path: Path,
    docker: FakeDocker,
    *,
    auth_home: Path | None = None,
) -> DockerQualificationBackend:
    return DockerQualificationBackend(
        source_manager=FakeSource(),
        docker_runner=docker,
        codex_adapter=FakeAdapter(),
        model="gpt-5.3-codex",
        reasoning_effort="high",
        work_root=tmp_path / "work",
        auth_home=auth_home,
        integrity_policy=IntegrityPolicy(reject_web_search=True, reject_mcp_calls=True, reject_protected_path_changes=True),
    )


def test_valid_agent_evidence_is_graded_and_usage_is_recorded(tmp_path: Path) -> None:
    docker = FakeDocker(stdout='{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}\n')
    service = backend(tmp_path, docker)
    spec = canary(tmp_path)
    prepared = service.prepare(spec, "q1")
    result = service.run_attempt(canary=spec, prepared=prepared, binary=binary(tmp_path), side=Side.BASELINE, repetition=1)
    assert result.valid is True
    assert result.success is True
    assert result.duration_ms == 123
    assert result.usage.input_tokens == 12
    assert docker.grader_calls == 1


def test_codex_auth_uses_ephemeral_writable_home(tmp_path: Path) -> None:
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text('{"token":"test-only"}', encoding="utf-8")
    docker = FakeDocker(stdout='{"type":"turn.completed","usage":{}}\n')
    service = backend(tmp_path, docker, auth_home=auth_home)
    spec = canary(tmp_path)

    service.run_attempt(
        canary=spec,
        prepared=PreparedImage("p", "sha256:p"),
        binary=binary(tmp_path),
        side=Side.BASELINE,
        repetition=1,
    )

    assert docker.auth_mount is not None
    mounted_home, container_path, mode = docker.auth_mount
    assert mounted_home != auth_home
    assert container_path == "/opt/qualock/auth"
    assert mode == "rw"
    assert docker.auth_json_contents == '{"token":"test-only"}'
    assert not mounted_home.exists()


def test_web_search_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    docker = FakeDocker(stdout='{"type":"item.completed","item":{"type":"web_search"}}\n')
    service = backend(tmp_path, docker)
    spec = canary(tmp_path)
    result = service.run_attempt(canary=spec, prepared=PreparedImage("p", "sha256:p"), binary=binary(tmp_path), side=Side.CANDIDATE, repetition=1)
    assert result.valid is False
    assert "web search" in (result.invalid_reason or "")
    assert docker.grader_calls == 0


def test_protected_path_change_invalidates_attempt(tmp_path: Path) -> None:
    docker = FakeDocker(stdout='{"type":"turn.completed","usage":{}}\n', changed_paths=("tests/test_hidden.py",))
    service = backend(tmp_path, docker)
    spec = canary(tmp_path)
    result = service.run_attempt(canary=spec, prepared=PreparedImage("p", "sha256:p"), binary=binary(tmp_path), side=Side.CANDIDATE, repetition=1)
    assert result.valid is False
    assert result.protected_path_violations == ("tests/test_hidden.py",)
    assert docker.grader_calls == 0
