import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from qualock.evidence.claude_stream_json import parse_claude_stream_json
from qualock.evidence.models import AgentEvidence

from .base import AgentBinary, AgentInvocation, AgentMount

_SETTINGS_CONTAINER_PATH = "/opt/qualock/claude-settings.json"
_CONFIG_DIR = "/opt/qualock/claude-home"
_CREDENTIAL_SEED = "/opt/qualock/claude-credentials-seed.json"
_CREDENTIAL_TARGET = f"{_CONFIG_DIR}/.credentials.json"
_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"


class ClaudeAdapter:
    def __init__(self, auth_home: Path | None = None) -> None:
        self.auth_home = auth_home

    def _build_argv(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> tuple[str, ...]:
        return (
            str(binary.path),
            "-p",
            "--safe-mode",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--model",
            model,
            "--effort",
            reasoning_effort,
            "--tools",
            _TOOLS,
            "--allowed-tools",
            _TOOLS,
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--settings",
            _SETTINGS_CONTAINER_PATH,
            prompt,
        )

    @contextmanager
    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> Iterator[AgentInvocation]:
        with tempfile.TemporaryDirectory(prefix="qualock-claude-") as temp:
            temp_root = Path(temp)
            settings_file = temp_root / "settings.json"
            settings_file.write_text(
                json.dumps(
                    {
                        "sandbox": {
                            "enabled": True,
                            "failIfUnavailable": True,
                            "autoAllowBashIfSandboxed": True,
                            "allowUnsandboxedCommands": False,
                        }
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            mounts = [AgentMount(settings_file, _SETTINGS_CONTAINER_PATH, "ro")]
            bootstrap_copy: tuple[str, str] | None = None

            if self.auth_home is not None:
                source = self.auth_home / ".credentials.json"
                if source.is_file():
                    credential_copy = temp_root / ".credentials.json"
                    shutil.copy2(source, credential_copy)
                    mounts.append(AgentMount(credential_copy, _CREDENTIAL_SEED, "ro"))
                    bootstrap_copy = (_CREDENTIAL_SEED, _CREDENTIAL_TARGET)

            yield AgentInvocation(
                argv=self._build_argv(
                    binary,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    prompt=prompt,
                ),
                environment=(("CLAUDE_CONFIG_DIR", _CONFIG_DIR), ("DISABLE_AUTOUPDATER", "1")),
                mounts=tuple(mounts),
                tmpfs_mounts=(_CONFIG_DIR,),
                bootstrap_copy=bootstrap_copy,
                container_binary_path="/opt/qualock/claude",
            )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        del stderr
        return parse_claude_stream_json(stdout.splitlines())
