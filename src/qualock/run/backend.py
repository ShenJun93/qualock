import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from qualock.agents.base import AgentBinary, AgentCapabilities
from qualock.canary.models import CanarySpec
from qualock.evidence.codex_jsonl import CodexEvidenceError, parse_codex_jsonl
from qualock.qualification.models import AttemptResult, Usage
from qualock.source.git import GitSourceManager

from .docker import DockerRunner
from .integrity import IntegrityPathError, protected_path_violations
from .models import PreparedImage
from .schedule import Side


class CodexLikeAdapter(Protocol):
    def detect_capabilities(self, binary: Path) -> AgentCapabilities: ...

    def build_exec_argv(
        self,
        binary: AgentBinary,
        capabilities: AgentCapabilities,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> list[str]: ...


@dataclass(frozen=True)
class IntegrityPolicy:
    reject_web_search: bool = True
    reject_mcp_calls: bool = True
    reject_protected_path_changes: bool = True


class DockerQualificationBackend:
    def __init__(
        self,
        *,
        source_manager: GitSourceManager,
        docker_runner: DockerRunner,
        codex_adapter: CodexLikeAdapter,
        model: str,
        reasoning_effort: str,
        work_root: Path,
        auth_home: Path | None,
        integrity_policy: IntegrityPolicy,
    ) -> None:
        self.source_manager = source_manager
        self.docker_runner = docker_runner
        self.codex_adapter = codex_adapter
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.work_root = work_root
        self.auth_home = auth_home
        self.integrity_policy = integrity_policy

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedImage:
        source_dir = self.work_root / qualification_id / canary.id / "source"
        self.source_manager.materialize(
            canary.repository.url,
            canary.repository.base_sha,
            source_dir,
        )
        key = hashlib.sha256(
            f"{qualification_id}:{canary.id}:{canary.repository.base_sha}".encode("utf-8")
        ).hexdigest()[:16]
        return self.docker_runner.prepare(
            source_dir,
            canary,
            image_tag=f"qualock-prepared-{key}",
        )

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedImage,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        capabilities = self.codex_adapter.detect_capabilities(binary.path)
        agent_argv = self.codex_adapter.build_exec_argv(
            binary,
            capabilities,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt=canary.task,
        )
        safe_canary = "".join(ch if ch.isalnum() else "-" for ch in canary.id)[:32]
        container_name = f"ub-{safe_canary}-{side.value[:1]}-{repetition}-{binary.version.replace('.', '-')}"
        frozen_tag = f"qualock-frozen-{hashlib.sha256(container_name.encode()).hexdigest()[:16]}"
        environment: dict[str, str] = {}
        mounts: list[tuple[Path, str, str]] = []

        if self.auth_home is not None:
            with tempfile.TemporaryDirectory(prefix="qualock-codex-home-") as temp:
                temp_home = Path(temp)
                auth_file = self.auth_home / "auth.json"
                if auth_file.is_file():
                    shutil.copy2(auth_file, temp_home / "auth.json")
                environment["CODEX_HOME"] = "/opt/qualock/auth"
                mounts.append((temp_home, "/opt/qualock/auth", "rw"))
                state = self.docker_runner.run_agent(
                    prepared=prepared,
                    container_name=container_name,
                    agent_binary=binary.path,
                    agent_argv=agent_argv,
                    environment=environment,
                    extra_mounts=mounts,
                    frozen_tag=frozen_tag,
                    timeout_seconds=canary.agent.timeout_seconds,
                )
        else:
            state = self.docker_runner.run_agent(
                prepared=prepared,
                container_name=container_name,
                agent_binary=binary.path,
                agent_argv=agent_argv,
                environment=environment,
                extra_mounts=mounts,
                frozen_tag=frozen_tag,
                timeout_seconds=canary.agent.timeout_seconds,
            )
        try:
            try:
                evidence = parse_codex_jsonl(state.stdout.splitlines())
            except CodexEvidenceError as exc:
                return self._invalid_attempt(side, repetition, state.elapsed_ms, str(exc), state.stdout)

            agent_state = self.docker_runner.inspect_agent_state(state)
            try:
                violations = tuple(
                    protected_path_violations(
                        agent_state.changed_paths,
                        canary.constraints.protected_paths,
                    )
                )
            except IntegrityPathError as exc:
                return self._invalid_attempt(side, repetition, state.elapsed_ms, str(exc), state.stdout)

            reasons: list[str] = []
            if self.integrity_policy.reject_web_search and evidence.web_searches:
                reasons.append("web search detected")
            if self.integrity_policy.reject_mcp_calls and evidence.mcp_calls:
                reasons.append("MCP call detected")
            if self.integrity_policy.reject_protected_path_changes and violations:
                reasons.append("protected path modified")
            if reasons:
                return AttemptResult(
                    side=side.value,
                    repetition=repetition,
                    success=False,
                    valid=False,
                    duration_ms=state.elapsed_ms,
                    usage=Usage(
                        input_tokens=evidence.input_tokens,
                        cached_input_tokens=evidence.cached_input_tokens,
                        output_tokens=evidence.output_tokens,
                        reasoning_output_tokens=evidence.reasoning_output_tokens,
                    ),
                    invalid_reason="; ".join(reasons),
                    events_jsonl=state.stdout,
                    protected_path_violations=violations,
                )

            grade = self.docker_runner.run_grader(
                state=state,
                grader_patch=canary.grader.patch,
                commands=canary.grader.command,
                timeout_seconds=canary.agent.timeout_seconds,
            )
            success = grade.exit_code == 0 and not grade.timed_out
            return AttemptResult(
                side=side.value,
                repetition=repetition,
                success=success,
                valid=True,
                duration_ms=state.elapsed_ms,
                usage=Usage(
                    input_tokens=evidence.input_tokens,
                    cached_input_tokens=evidence.cached_input_tokens,
                    output_tokens=evidence.output_tokens,
                    reasoning_output_tokens=evidence.reasoning_output_tokens,
                ),
                events_jsonl=state.stdout,
                protected_path_violations=violations,
            )
        finally:
            self.docker_runner.remove_container(state.container_name)

    @staticmethod
    def _invalid_attempt(
        side: Side,
        repetition: int,
        duration_ms: int,
        reason: str,
        events_jsonl: str,
    ) -> AttemptResult:
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=False,
            valid=False,
            duration_ms=duration_ms,
            invalid_reason=reason,
            events_jsonl=events_jsonl,
        )
