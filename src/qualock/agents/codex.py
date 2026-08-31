from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary, AgentCapabilities


class IncompatibleCodexError(RuntimeError):
    pass


class CodexAdapter:
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
