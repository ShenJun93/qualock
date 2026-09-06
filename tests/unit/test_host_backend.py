import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from qualock.agents.antigravity import AntigravityInvocation
from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec, RuntimeSpec
from qualock.evidence.models import AgentEvidence, AgentEvidenceError
from qualock.run.backend import IntegrityPolicy, UnsupportedRuntimeError
from qualock.run.host import HostAgentState, HostCommandError
from qualock.run.host_backend import LinuxHostQualificationBackend
from qualock.run.models import AgentStateEvidence, GradeResult, PreparedTarget
from qualock.run.schedule import Side

TEMPLATE_FILE = "app.py"
MUTATION_FILE = "agent-scratch.txt"


class FakeSource:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, Path]] = []
        self.error = error

    def materialize(self, url: str, sha: str, destination: Path) -> Path:
        self.calls.append((url, sha, destination))
        if self.error is not None:
            raise self.error
        destination.mkdir(parents=True)
        (destination / TEMPLATE_FILE).write_text("template", encoding="utf-8")
        (destination / ".git").mkdir()
        return destination


class FakeAdapter:
    def __init__(
        self,
        *,
        evidence: AgentEvidence | None = None,
        parse_error: str | None = None,
    ) -> None:
        self.evidence = evidence or AgentEvidence(input_tokens=12, output_tokens=3)
        self.parse_error = parse_error
        self.invocation_kwargs: dict[str, object] = {}
        self.profile_roots: list[Path] = []
        self.invocations: list[AntigravityInvocation] = []

    @contextmanager
    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
        workspace: Path,
        timeout_seconds: int,
    ) -> Iterator[AntigravityInvocation]:
        self.invocation_kwargs = {
            "binary": binary,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "prompt": prompt,
            "workspace": workspace,
            "timeout_seconds": timeout_seconds,
        }
        with tempfile.TemporaryDirectory(prefix="fake-antigravity-") as temporary:
            root = Path(temporary)
            self.profile_roots.append(root)
            config_root = root / "config"
            app_data_root = root / "appdata"
            config_root.mkdir()
            app_data_root.mkdir()
            token = root / "antigravity-oauth-token"
            token.touch()
            invocation = AntigravityInvocation(
                argv=(str(binary.path), "-p", prompt),
                environment=(("QUALOCK_WORKSPACE", "/tmp/qualock-workspace"),),
                config_root=config_root,
                app_data_root=app_data_root,
                oauth_token_path=token,
                workspace_mount="/tmp/qualock-workspace",
            )
            self.invocations.append(invocation)
            yield invocation

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        if self.parse_error is not None:
            raise AgentEvidenceError(self.parse_error)
        return self.evidence


class FakeHostRunner:
    def __init__(
        self,
        *,
        stdout: str = "opaque agent output",
        stderr: str = "",
        exit_code: int | None = 0,
        changed_paths: tuple[str, ...] = (),
        setup_error: Exception | None = None,
        agent_error: Exception | None = None,
        inspect_error: Exception | None = None,
        grader_error: Exception | None = None,
        grade_exit_code: int | None = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.changed_paths = changed_paths
        self.setup_error = setup_error
        self.agent_error = agent_error
        self.inspect_error = inspect_error
        self.grader_error = grader_error
        self.grade_exit_code = grade_exit_code
        self.setup_calls: list[tuple[Path, tuple[str, ...], float]] = []
        self.agent_workspaces: list[Path] = []
        self.agent_invocations: list[AntigravityInvocation] = []
        self.agent_timeouts: list[float] = []
        self.workspace_snapshots: list[tuple[str, ...]] = []
        self.workspace_symlinks: list[tuple[str, ...]] = []
        self.grader_calls: list[dict[str, object]] = []
        self.inspect_calls: list[Path] = []
        self.trace: list[str] = []

    def run_setup(
        self, workspace: Path, commands: Sequence[str], *, timeout_seconds: float
    ) -> None:
        self.setup_calls.append((workspace, tuple(commands), timeout_seconds))
        self.trace.append("setup")
        if self.setup_error is not None:
            raise self.setup_error

    def run_agent(
        self, workspace: Path, invocation: AntigravityInvocation, timeout_seconds: float
    ) -> HostAgentState:
        self.agent_workspaces.append(workspace)
        self.agent_invocations.append(invocation)
        self.agent_timeouts.append(timeout_seconds)
        self.workspace_snapshots.append(
            tuple(sorted(item.name for item in workspace.iterdir()))
        )
        self.workspace_symlinks.append(
            tuple(sorted(item.name for item in workspace.iterdir() if item.is_symlink()))
        )
        self.trace.append("agent")
        if self.agent_error is not None:
            raise self.agent_error
        (workspace / MUTATION_FILE).write_text("mutated by agent", encoding="utf-8")
        return HostAgentState(
            workspace=workspace,
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            elapsed_ms=123,
        )

    def inspect_agent_state(self, workspace: Path) -> AgentStateEvidence:
        self.inspect_calls.append(workspace)
        self.trace.append("inspect")
        if self.inspect_error is not None:
            raise self.inspect_error
        return AgentStateEvidence(changed_paths=self.changed_paths, patch="diff")

    def run_grader(
        self,
        *,
        workspace: Path,
        grader_patch: Path,
        commands: Sequence[str],
        timeout_seconds: float,
    ) -> GradeResult:
        self.grader_calls.append(
            {
                "workspace": workspace,
                "grader_patch": grader_patch,
                "commands": tuple(commands),
                "timeout_seconds": timeout_seconds,
            }
        )
        self.trace.append("grader")
        if self.grader_error is not None:
            raise self.grader_error
        return GradeResult(
            exit_code=self.grade_exit_code,
            stdout="1 passed",
            stderr="",
            timed_out=False,
        )


def canary(
    tmp_path: Path,
    *,
    setup: Sequence[str] = ("pip install -e .",),
    base_sha: str = "a" * 40,
    url: str = "https://example.invalid/repo.git",
) -> CanarySpec:
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    return CanarySpec.model_validate(
        {
            "schema_version": 1,
            "id": "sample",
            "name": "Sample",
            "repository": {"url": url, "base_sha": base_sha},
            "runtime": {"execution": "linux-host"},
            "task": "Fix it",
            "setup": list(setup),
            "agent": {"timeout_seconds": 60},
            "grader": {"patch": str(grader), "command": ["pytest -q"]},
            "constraints": {"protected_paths": ["tests/**"]},
            "critical": True,
        }
    )


def binary(tmp_path: Path) -> AgentBinary:
    path = tmp_path / "cache/agents/antigravity/0.5.0/agy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fake", encoding="utf-8")
    return AgentBinary("antigravity", "0.5.0", path, "abc")


def backend(
    tmp_path: Path,
    runner: FakeHostRunner,
    *,
    adapter: FakeAdapter | None = None,
    source: FakeSource | None = None,
    integrity_policy: IntegrityPolicy | None = None,
) -> LinuxHostQualificationBackend:
    return LinuxHostQualificationBackend(
        source_manager=source or FakeSource(),
        host_runner=runner,
        agent_adapter=adapter or FakeAdapter(),
        model="gemini-3-pro",
        reasoning_effort="high",
        work_root=tmp_path / "work",
        integrity_policy=integrity_policy or IntegrityPolicy(),
    )


def prepared_template(tmp_path: Path) -> Path:
    template = tmp_path / "template"
    template.mkdir()
    (template / TEMPLATE_FILE).write_text("template", encoding="utf-8")
    (template / ".git").mkdir()
    return template


def run_once(
    tmp_path: Path,
    service: LinuxHostQualificationBackend,
    *,
    side: Side = Side.BASELINE,
    repetition: int = 1,
    spec: CanarySpec | None = None,
    template: Path | None = None,
):
    reference = template or prepared_template(tmp_path)
    return service.run_attempt(
        canary=spec or canary(tmp_path),
        prepared=PreparedTarget(str(reference), "sha256:prepared"),
        binary=binary(tmp_path),
        side=side,
        repetition=repetition,
    )


def test_prepare_rejects_container_runtime(tmp_path: Path) -> None:
    source = FakeSource()
    service = backend(tmp_path, FakeHostRunner(), source=source)
    spec = canary(tmp_path).model_copy(
        update={"runtime": RuntimeSpec(execution="container", image="python:3.12-slim")}
    )

    with pytest.raises(UnsupportedRuntimeError):
        service.prepare(spec, "q1")

    assert source.calls == []


def test_prepare_rejects_container_image_on_host_runtime(tmp_path: Path) -> None:
    source = FakeSource()
    service = backend(tmp_path, FakeHostRunner(), source=source)
    runtime = RuntimeSpec.model_construct(execution="linux-host", image="python:3.12-slim")
    spec = canary(tmp_path).model_copy(update={"runtime": runtime})

    with pytest.raises(UnsupportedRuntimeError):
        service.prepare(spec, "q1")

    assert source.calls == []


def test_prepare_materializes_base_sha_and_runs_setup(tmp_path: Path) -> None:
    source = FakeSource()
    runner = FakeHostRunner()
    service = backend(tmp_path, runner, source=source)
    spec = canary(tmp_path)

    prepared = service.prepare(spec, "q1")

    expected = tmp_path / "work" / "q1" / "sample" / "prepared"
    assert source.calls == [(spec.repository.url, spec.repository.base_sha, expected)]
    assert runner.setup_calls == [(expected, ("pip install -e .",), 60)]
    assert prepared.reference == str(expected)
    assert prepared.digest.startswith("sha256:")


def test_prepared_digest_is_derived_from_repository_and_setup_inputs(tmp_path: Path) -> None:
    cases = iter(range(10))

    def digest_for(**overrides: object) -> str:
        root = tmp_path / f"case-{next(cases)}"
        root.mkdir()
        service = backend(root, FakeHostRunner())
        spec = canary(root, **overrides)  # type: ignore[arg-type]
        return service.prepare(spec, "q1").digest

    baseline = digest_for()
    assert digest_for() == baseline
    assert digest_for(setup=("make build",)) != baseline
    assert digest_for(base_sha="b" * 40) != baseline
    assert digest_for(url="https://example.invalid/other.git") != baseline


def test_prepared_digest_is_stable_across_qualification_ids(tmp_path: Path) -> None:
    spec = canary(tmp_path)
    first = backend(tmp_path / "one", FakeHostRunner()).prepare(spec, "q1")
    second = backend(tmp_path / "two", FakeHostRunner()).prepare(spec, "q2")

    assert first.digest == second.digest
    assert first.reference != second.reference


def test_prepare_removes_partial_template_when_setup_fails(tmp_path: Path) -> None:
    runner = FakeHostRunner(setup_error=HostCommandError("setup failed"))
    service = backend(tmp_path, runner)

    with pytest.raises(HostCommandError):
        service.prepare(canary(tmp_path), "q1")

    assert not (tmp_path / "work" / "q1" / "sample" / "prepared").exists()


def test_run_attempt_rejects_container_runtime(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner)
    spec = canary(tmp_path).model_copy(
        update={"runtime": RuntimeSpec(execution="container", image="python:3.12-slim")}
    )

    with pytest.raises(UnsupportedRuntimeError):
        run_once(tmp_path, service, spec=spec)

    assert runner.agent_workspaces == []


def test_repetitions_start_from_the_same_clean_template_in_distinct_directories(
    tmp_path: Path,
) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner)
    template = prepared_template(tmp_path)

    run_once(tmp_path, service, repetition=1, template=template)
    run_once(tmp_path, service, side=Side.CANDIDATE, repetition=2, template=template)

    first, second = runner.agent_workspaces
    assert first != second
    assert runner.workspace_snapshots == [(".git", TEMPLATE_FILE), (".git", TEMPLATE_FILE)]
    assert sorted(item.name for item in template.iterdir()) == [".git", TEMPLATE_FILE]


def test_attempt_directories_are_removed_after_each_attempt(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner)

    run_once(tmp_path, service)

    workspace = runner.agent_workspaces[0]
    assert (tmp_path / "work") in workspace.parents
    assert not workspace.exists()
    assert not workspace.parent.exists()


def test_attempt_directory_and_agent_profile_are_cleaned_when_agent_raises(
    tmp_path: Path,
) -> None:
    runner = FakeHostRunner(agent_error=HostCommandError("bwrap missing"))
    adapter = FakeAdapter()
    service = backend(tmp_path, runner, adapter=adapter)

    with pytest.raises(HostCommandError):
        run_once(tmp_path, service)

    assert not runner.agent_workspaces[0].exists()
    assert not adapter.profile_roots[0].exists()
    assert runner.grader_calls == []


def test_agent_runs_through_the_adapter_invocation_and_host_runner(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    adapter = FakeAdapter()
    service = backend(tmp_path, runner, adapter=adapter)

    run_once(tmp_path, service)

    workspace = runner.agent_workspaces[0]
    assert adapter.invocation_kwargs["model"] == "gemini-3-pro"
    assert adapter.invocation_kwargs["reasoning_effort"] == "high"
    assert adapter.invocation_kwargs["prompt"] == "Fix it"
    assert adapter.invocation_kwargs["workspace"] == workspace
    assert adapter.invocation_kwargs["timeout_seconds"] == 60
    assert runner.agent_invocations == [adapter.invocations[0]]
    assert runner.agent_timeouts == [60]
    assert not adapter.profile_roots[0].exists()


def test_successful_attempt_grades_only_after_evidence_and_integrity_checks(
    tmp_path: Path,
) -> None:
    runner = FakeHostRunner()
    adapter = FakeAdapter(evidence=AgentEvidence(input_tokens=12, output_tokens=3))
    service = backend(tmp_path, runner, adapter=adapter)

    result = run_once(tmp_path, service)

    assert result.valid is True
    assert result.success is True
    assert result.side == Side.BASELINE.value
    assert result.repetition == 1
    assert result.duration_ms == 123
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.events_jsonl == "opaque agent output"
    assert runner.trace == ["agent", "inspect", "grader"]
    call = runner.grader_calls[0]
    assert call["workspace"] == runner.agent_workspaces[0]
    assert call["grader_patch"] == canary(tmp_path).grader.patch
    assert call["commands"] == ("pytest -q",)
    assert call["timeout_seconds"] == 60


def test_failing_grader_marks_valid_attempt_as_unsuccessful(tmp_path: Path) -> None:
    runner = FakeHostRunner(grade_exit_code=1)
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is True
    assert result.success is False


def test_agent_timeout_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner(exit_code=None)
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert result.invalid_reason == "agent timed out"
    assert runner.grader_calls == []


def test_agent_non_zero_exit_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner(exit_code=1)
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert "agent exited with code 1" in (result.invalid_reason or "")
    assert runner.grader_calls == []


def test_terminal_evidence_error_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    service = backend(
        tmp_path, runner, adapter=FakeAdapter(evidence=AgentEvidence(errors=["quota exceeded"]))
    )

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert "agent reported error" in (result.invalid_reason or "")
    assert runner.grader_calls == []


def test_evidence_parse_failure_invalidates_attempt_before_git_inspection(
    tmp_path: Path,
) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner, adapter=FakeAdapter(parse_error="bad evidence"))

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert result.invalid_reason == "bad evidence"
    assert runner.inspect_calls == []
    assert runner.grader_calls == []


def test_web_search_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner, adapter=FakeAdapter(evidence=AgentEvidence(web_searches=1)))

    result = run_once(tmp_path, service, side=Side.CANDIDATE)

    assert result.valid is False
    assert "web search" in (result.invalid_reason or "")
    assert runner.grader_calls == []


def test_mcp_call_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner()
    service = backend(tmp_path, runner, adapter=FakeAdapter(evidence=AgentEvidence(mcp_calls=1)))

    result = run_once(tmp_path, service, side=Side.CANDIDATE)

    assert result.valid is False
    assert "MCP call" in (result.invalid_reason or "")
    assert runner.grader_calls == []


def test_protected_path_modification_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner(changed_paths=("tests/test_hidden.py", "app.py"))
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service, side=Side.CANDIDATE)

    assert result.valid is False
    assert "protected path modified" in (result.invalid_reason or "")
    assert result.protected_path_violations == ("tests/test_hidden.py",)
    assert runner.grader_calls == []


def test_integrity_policy_toggles_are_honored(tmp_path: Path) -> None:
    runner = FakeHostRunner(changed_paths=("tests/test_hidden.py",))
    service = backend(
        tmp_path,
        runner,
        adapter=FakeAdapter(evidence=AgentEvidence(web_searches=1, mcp_calls=1)),
        integrity_policy=IntegrityPolicy(
            reject_web_search=False,
            reject_mcp_calls=False,
            reject_protected_path_changes=False,
        ),
    )

    result = run_once(tmp_path, service)

    assert result.valid is True
    assert result.protected_path_violations == ("tests/test_hidden.py",)
    assert len(runner.grader_calls) == 1


def test_path_escaping_change_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner(changed_paths=("../outside.py",))
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert "escapes repository" in (result.invalid_reason or "")
    assert runner.grader_calls == []


def test_git_inspection_failure_invalidates_attempt_before_grader(tmp_path: Path) -> None:
    runner = FakeHostRunner(inspect_error=HostCommandError("missing .git directory"))
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert result.invalid_reason == "missing .git directory"
    assert runner.grader_calls == []


def test_grader_containment_failure_invalidates_attempt(tmp_path: Path) -> None:
    runner = FakeHostRunner(grader_error=HostCommandError("grader symlink resolves outside"))
    service = backend(tmp_path, runner)

    result = run_once(tmp_path, service)

    assert result.valid is False
    assert result.success is False
    assert "grader symlink resolves outside" in (result.invalid_reason or "")
    assert not runner.agent_workspaces[0].exists()


def test_attempt_copies_symlinks_without_following_them(tmp_path: Path) -> None:
    template = prepared_template(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (template / "link").symlink_to(outside)
    runner = FakeHostRunner()
    service = backend(tmp_path, runner)

    run_once(tmp_path, service, template=template)

    assert runner.workspace_symlinks == [("link",)]
