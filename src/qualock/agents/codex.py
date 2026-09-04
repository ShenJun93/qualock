import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from qualock.evidence.codex_jsonl import parse_codex_jsonl
from qualock.evidence.models import AgentEvidence
from qualock.run.process import run_process

from .base import (
    AgentBinary,
    AgentCapabilities,
    AgentInvocation,
    AgentMount,
    AgentRuntimeDependency,
)


class IncompatibleCodexError(RuntimeError):
    pass


class CodexAdapter:
    def __init__(self, auth_home: Path | None = None) -> None:
        self.auth_home = auth_home

    @property
    def runtime_dependencies(self) -> tuple[AgentRuntimeDependency, ...]:
        return ()

    def detect_capabilities(self, binary: Path) -> AgentCapabilities:
        result = run_process(
            [str(binary), "exec", "--help"],
            timeout_seconds=10,
        )
        text = f"{result.stdout}\n{result.stderr}"
        exec_supported = result.exit_code == 0 and not result.timed_out
        return AgentCapabilities(
            exec=exec_supported,
            json="--json" in text or "--experimental-json" in text,
            ephemeral="--ephemeral" in text,
            ignore_user_config="--ignore-user-config" in text,
            ignore_rules="--ignore-rules" in text,
            workspace_write="--sandbox" in text,
            model="--model" in text or "-m" in text,
        )

    def build_exec_argv(
        self,
        binary: AgentBinary,
        capabilities: AgentCapabilities,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> list[str]:
        if not capabilities.common_contract:
            raise IncompatibleCodexError(
                f"Codex {binary.version} cannot satisfy Qualock v0.1 execution contract"
            )
        return [
            str(binary.path),
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--sandbox",
            "workspace-write",
            "--model",
            model,
            "-c",
            f"model_reasoning_effort={reasoning_effort}",
            "-c",
            "sandbox_workspace_write.network_access=false",
            "-c",
            "web_search=disabled",
            "--json",
            prompt,
        ]

    @contextmanager
    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> Iterator[AgentInvocation]:
        capabilities = self.detect_capabilities(binary.path)
        argv = tuple(
            self.build_exec_argv(
                binary,
                capabilities,
                model=model,
                reasoning_effort=reasoning_effort,
                prompt=prompt,
            )
        )
        if self.auth_home is None:
            yield AgentInvocation(argv=argv, container_binary_path="/opt/qualock/codex")
            return

        environment = (("CODEX_HOME", "/opt/qualock/auth"),)
        tmpfs_mounts = ("/opt/qualock/auth",)
        auth_file = self.auth_home / "auth.json"
        if not auth_file.is_file():
            yield AgentInvocation(
                argv=argv,
                environment=environment,
                tmpfs_mounts=tmpfs_mounts,
                container_binary_path="/opt/qualock/codex",
            )
            return

        with tempfile.TemporaryDirectory(prefix="qualock-codex-auth-") as temp:
            temp_auth = Path(temp) / "auth.json"
            shutil.copy2(auth_file, temp_auth)
            seed_path = "/opt/qualock/auth-seed.json"
            target_path = "/opt/qualock/auth/auth.json"
            yield AgentInvocation(
                argv=argv,
                environment=environment,
                mounts=(AgentMount(temp_auth, seed_path, "ro"),),
                tmpfs_mounts=tmpfs_mounts,
                bootstrap_copy=(seed_path, target_path),
                container_binary_path="/opt/qualock/codex",
            )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        del stderr
        return parse_codex_jsonl(stdout.splitlines())
