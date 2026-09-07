import hashlib
import platform
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from qualock.agents.antigravity import AntigravityAdapter
from qualock.agents.base import AgentBinary
from qualock.canary.models import CanarySpec
from qualock.evidence.models import AgentEvidence, AgentEvidenceError
from qualock.qualification.models import AttemptResult, Usage
from qualock.source.git import GitSourceManager

from .backend import IntegrityPolicy, UnsupportedRuntimeError
from .host import HostAgentState, HostCommandError, LinuxHostRunner
from .integrity import IntegrityPathError, protected_path_violations
from .models import PreparedTarget
from .schedule import Side


def _usage_from_evidence(evidence: AgentEvidence) -> Usage:
    return Usage(
        input_tokens=evidence.input_tokens,
        cached_input_tokens=evidence.cached_input_tokens,
        cache_write_input_tokens=evidence.cache_write_input_tokens,
        output_tokens=evidence.output_tokens,
        reasoning_output_tokens=evidence.reasoning_output_tokens,
        observed=evidence.usage_observed,
    )


_ATTEMPTS_DIRNAME = "attempts"
_PREPARED_DIRNAME = "prepared"
_WORKSPACE_DIRNAME = "workspace"
_SUPPORTED_PLATFORM = "Linux"
_LAUNCH_FAILURES = (
    HostCommandError,
    OSError,
    ValueError,
)


def prepared_digest(*, repository_url: str, base_sha: str, setup: Sequence[str]) -> str:
    payload = "\0".join((repository_url, base_sha, str(len(setup)), *setup))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class LinuxHostQualificationBackend:
    def __init__(
        self,
        *,
        source_manager: GitSourceManager,
        host_runner: LinuxHostRunner,
        agent_adapter: AntigravityAdapter,
        model: str,
        reasoning_effort: str,
        work_root: Path,
        integrity_policy: IntegrityPolicy,
        platform_system: str | None = None,
    ) -> None:
        self.source_manager = source_manager
        self.host_runner = host_runner
        self.agent_adapter = agent_adapter
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.work_root = work_root
        self.integrity_policy = integrity_policy
        self.platform_system = platform_system or platform.system()

    def prepare(self, canary: CanarySpec, qualification_id: str) -> PreparedTarget:
        self._require_linux_platform()
        self._require_host_runtime(canary)
        source_dir = self.work_root / qualification_id / canary.id / _PREPARED_DIRNAME
        self.source_manager.materialize(
            canary.repository.url,
            canary.repository.base_sha,
            source_dir,
        )
        try:
            self.host_runner.run_setup(
                source_dir,
                canary.setup,
                timeout_seconds=canary.agent.timeout_seconds,
            )
        except BaseException:
            shutil.rmtree(source_dir, ignore_errors=True)
            raise
        return PreparedTarget(
            reference=str(source_dir),
            digest=prepared_digest(
                repository_url=canary.repository.url,
                base_sha=canary.repository.base_sha,
                setup=canary.setup,
            ),
        )

    def run_attempt(
        self,
        *,
        canary: CanarySpec,
        prepared: PreparedTarget,
        binary: AgentBinary,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        self._require_linux_platform()
        self._require_host_runtime(canary)
        attempt_root = self._create_attempt_root(canary, side, repetition)
        try:
            workspace = attempt_root / _WORKSPACE_DIRNAME
            shutil.copytree(Path(prepared.reference), workspace, symlinks=True)
            try:
                with self.agent_adapter.invocation(
                    binary,
                    model=self.model,
                    reasoning_effort=self.reasoning_effort,
                    prompt=canary.task,
                    workspace=workspace,
                    timeout_seconds=canary.agent.timeout_seconds,
                ) as invocation:
                    state = self.host_runner.run_agent(
                        workspace,
                        invocation,
                        canary.agent.timeout_seconds,
                    )
            except _LAUNCH_FAILURES as exc:
                return self._invalid_attempt(
                    side, repetition, 0, f"agent launch failed: {exc}", ""
                )
            return self._judge_attempt(
                canary=canary,
                state=state,
                side=side,
                repetition=repetition,
            )
        finally:
            shutil.rmtree(attempt_root, ignore_errors=True)

    def _judge_attempt(
        self,
        *,
        canary: CanarySpec,
        state: HostAgentState,
        side: Side,
        repetition: int,
    ) -> AttemptResult:
        try:
            evidence = self.agent_adapter.parse_evidence(state.stdout, state.stderr)
        except AgentEvidenceError as exc:
            return self._invalid_attempt(side, repetition, state.elapsed_ms, str(exc), state.stdout)

        try:
            agent_state = self.host_runner.inspect_agent_state(state.workspace)
            violations = tuple(
                protected_path_violations(
                    agent_state.changed_paths,
                    canary.constraints.protected_paths,
                )
            )
        except (HostCommandError, IntegrityPathError) as exc:
            return self._invalid_attempt(side, repetition, state.elapsed_ms, str(exc), state.stdout)

        reasons: list[str] = []
        if state.exit_code is None:
            reasons.append("agent timed out")
        elif state.exit_code != 0:
            reasons.append(f"agent exited with code {state.exit_code}")
        if evidence.errors:
            reasons.append("agent reported error")
        if self.integrity_policy.reject_web_search and evidence.web_searches:
            reasons.append("web search detected")
        if self.integrity_policy.reject_mcp_calls and evidence.mcp_calls:
            reasons.append("MCP call detected")
        if self.integrity_policy.reject_protected_path_changes and violations:
            reasons.append("protected path modified")
        if reasons:
            return self._completed_attempt(
                side=side,
                repetition=repetition,
                state=state,
                evidence=evidence,
                violations=violations,
                success=False,
                invalid_reason="; ".join(reasons),
            )

        try:
            grade = self.host_runner.run_grader(
                workspace=state.workspace,
                grader_patch=canary.grader.patch,
                commands=canary.grader.command,
                timeout_seconds=canary.agent.timeout_seconds,
            )
        except HostCommandError as exc:
            return self._completed_attempt(
                side=side,
                repetition=repetition,
                state=state,
                evidence=evidence,
                violations=violations,
                success=False,
                invalid_reason=str(exc),
            )
        return self._completed_attempt(
            side=side,
            repetition=repetition,
            state=state,
            evidence=evidence,
            violations=violations,
            success=grade.exit_code == 0 and not grade.timed_out,
        )

    def _create_attempt_root(self, canary: CanarySpec, side: Side, repetition: int) -> Path:
        attempts_root = self.work_root / _ATTEMPTS_DIRNAME
        attempts_root.mkdir(parents=True, exist_ok=True)
        safe_canary = "".join(ch if ch.isalnum() else "-" for ch in canary.id)[:32]
        return Path(
            tempfile.mkdtemp(
                prefix=f"{safe_canary}-{side.value[:1]}-{repetition}-",
                dir=attempts_root,
            )
        )

    def _require_linux_platform(self) -> None:
        if self.platform_system != _SUPPORTED_PLATFORM:
            raise UnsupportedRuntimeError(
                f"LinuxHostQualificationBackend requires a Linux platform, "
                f"got {self.platform_system!r}"
            )

    @staticmethod
    def _require_host_runtime(canary: CanarySpec) -> None:
        if canary.runtime.execution != "linux-host":
            raise UnsupportedRuntimeError(
                f"LinuxHostQualificationBackend requires execution=linux-host, "
                f"got {canary.runtime.execution!r}"
            )
        if canary.runtime.image is not None:
            raise UnsupportedRuntimeError(
                f"linux-host runtime must not declare a container image, "
                f"got {canary.runtime.image!r}"
            )

    @staticmethod
    def _completed_attempt(
        *,
        side: Side,
        repetition: int,
        state: HostAgentState,
        evidence: AgentEvidence,
        violations: tuple[str, ...],
        success: bool,
        invalid_reason: str | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            side=side.value,
            repetition=repetition,
            success=success,
            valid=invalid_reason is None,
            duration_ms=state.elapsed_ms,
            usage=_usage_from_evidence(evidence),
            invalid_reason=invalid_reason,
            events_jsonl=state.stdout,
            protected_path_violations=violations,
        )

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
