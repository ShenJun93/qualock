import json
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from qualock.evidence.claude_stream_json import parse_claude_stream_json
from qualock.evidence.models import AgentEvidence

from .base import AgentBinary, AgentInvocation, AgentMount, AgentRuntimeDependency

_SETTINGS_CONTAINER_PATH = "/opt/qualock/claude-settings.json"
_CONFIG_DIR = "/opt/qualock/claude-home"
_TOOLS = "Bash,Read,Edit,Write"
_AUTH_ENV_NAMES = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def select_claude_automation_credential(
    environment: Mapping[str, str],
) -> tuple[str, str] | None:
    for name in _AUTH_ENV_NAMES:
        value = environment.get(name)
        if value:
            return name, value
    return None


class ClaudeAdapter:
    @property
    def runtime_dependencies(self) -> tuple[AgentRuntimeDependency, ...]:
        return (AgentRuntimeDependency(command="socat", apt_package="socat"),)

    def __init__(self, automation_credential: tuple[str, str] | None = None) -> None:
        self.automation_credential = automation_credential

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
            "--restricted",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
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
                            "enableWeakerNestedSandbox": True,
                            "network": {
                                "deniedDomains": ["*"],
                                "strictAllowlist": True,
                            },
                            "credentials": {
                                "envVars": [
                                    {"name": name, "mode": "deny"}
                                    for name in _AUTH_ENV_NAMES
                                ]
                            },
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            mounts = [AgentMount(settings_file, _SETTINGS_CONTAINER_PATH, "ro")]

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
                stdin_secret_env=self.automation_credential,
                container_binary_path="/opt/qualock/claude",
            )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        del stderr
        return parse_claude_stream_json(stdout.splitlines())
