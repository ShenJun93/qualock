import json
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from qualock.evidence.gemini_stream_json import parse_gemini_stream_json
from qualock.evidence.models import AgentEvidence

from .base import (
    AgentBinary,
    AgentInvocation,
    AgentMount,
    AgentRuntimeDependency,
    AgentRuntimeOverlay,
)

_CREDENTIAL_ENV_NAME = "GEMINI_API_KEY"
_PROVIDER_DEFAULT_EFFORT = "provider-default"

_HOME_DIR = "/opt/qualock/gemini-home"
_SETTINGS_CONTAINER_PATH = "/opt/qualock/gemini-settings.json"
_PROJECT_GEMINI_CONTAINER_PATH = "/workspace/.gemini"
_WRAPPER_CONTAINER_PATH = "/opt/qualock/bin/bash"
_PATH_ENV = (
    "/opt/qualock/bin:/opt/qualock/node-runtime/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)

_NODE_OVERLAY = AgentRuntimeOverlay(
    image=(
        "node:22.23.2-bookworm@sha256:"
        "8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d"
    ),
    source_path="/usr/local",
    destination_path="/opt/qualock/node-runtime",
    validation_command=("/opt/qualock/node-runtime/bin/node", "--version"),
)

_ENFORCED_SETTINGS: dict[str, object] = {
    "mcpServers": {},
    "policyPaths": [],
    "adminPolicyPaths": [],
    "general": {
        "enableAutoUpdate": False,
        "enableAutoUpdateNotification": False,
        "checkpointing": {"enabled": False},
    },
    "privacy": {"usageStatisticsEnabled": False},
    "advanced": {"ignoreLocalEnv": True},
    "context": {
        "fileName": [],
        "includeDirectories": [],
        "loadMemoryFromIncludeDirectories": False,
    },
    "tools": {
        "shell": {"enableInteractiveShell": False},
        "core": [
            "read_file",
            "read_many_files",
            "list_directory",
            "glob",
            "grep_search",
            "write_file",
            "replace",
            "run_shell_command",
        ],
    },
    "security": {
        "auth": {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"},
        "blockedEnvironmentVariables": ["GEMINI_API_KEY"],
    },
    "skills": {"enabled": False},
    "hooksConfig": {"enabled": False},
    "hooks": {},
    "telemetry": {"enabled": False, "logPrompts": False},
    "experimental": {"enableAgents": False},
}

_WRAPPER_SCRIPT = (
    "#!/bin/sh\n"
    "set -eu\n"
    "unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS \\\n"
    "  GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION\n"
    "if ! /usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/true; then\n"
    "  echo QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE >&2\n"
    "  exit 125\n"
    "fi\n"
    "set +e\n"
    '/usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/bash "$@"\n'
    "qualock_status=$?\n"
    "printf '\\nQUALOCK_GEMINI_EXIT_CODE=%s\\n' \"$qualock_status\" >&2\n"
    'exit "$qualock_status"\n'
)


def select_gemini_automation_credential(
    environment: Mapping[str, str],
) -> tuple[str, str] | None:
    value = environment.get(_CREDENTIAL_ENV_NAME)
    if value:
        return _CREDENTIAL_ENV_NAME, value
    return None


class GeminiAdapter:
    def __init__(self, automation_credential: tuple[str, str] | None = None) -> None:
        if automation_credential is not None:
            name, value = automation_credential
            if name != _CREDENTIAL_ENV_NAME or not value:
                raise ValueError("Gemini automation credential must be a non-empty GEMINI_API_KEY")
        self.automation_credential = automation_credential

    @property
    def runtime_dependencies(self) -> tuple[AgentRuntimeDependency, ...]:
        return ()

    @property
    def runtime_overlays(self) -> tuple[AgentRuntimeOverlay, ...]:
        return (_NODE_OVERLAY,)

    def _build_argv(
        self,
        binary: AgentBinary,
        *,
        model: str,
        prompt: str,
    ) -> tuple[str, ...]:
        return (
            str(binary.path),
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--approval-mode",
            "yolo",
            "-e",
            "none",
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
        if reasoning_effort != _PROVIDER_DEFAULT_EFFORT:
            raise ValueError(
                f"Gemini reasoning effort must be {_PROVIDER_DEFAULT_EFFORT!r}, "
                f"got {reasoning_effort!r}"
            )

        argv = self._build_argv(binary, model=model, prompt=prompt)

        with tempfile.TemporaryDirectory(prefix="qualock-gemini-") as temp:
            temp_root = Path(temp)

            settings_file = temp_root / "gemini-settings.json"
            settings_file.write_text(
                json.dumps(_ENFORCED_SETTINGS, sort_keys=True), encoding="utf-8"
            )

            wrapper_file = temp_root / "bash"
            wrapper_file.write_text(_WRAPPER_SCRIPT, encoding="utf-8")
            wrapper_file.chmod(wrapper_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            project_gemini_dir = temp_root / "project-gemini"
            project_gemini_dir.mkdir()

            yield AgentInvocation(
                argv=argv,
                environment=(
                    ("HOME", _HOME_DIR),
                    ("GEMINI_CLI_HOME", _HOME_DIR),
                    ("GEMINI_CLI_SYSTEM_SETTINGS_PATH", _SETTINGS_CONTAINER_PATH),
                    ("GEMINI_SANDBOX", "false"),
                    ("PATH", _PATH_ENV),
                ),
                mounts=(
                    AgentMount(settings_file, _SETTINGS_CONTAINER_PATH, "ro"),
                    AgentMount(project_gemini_dir, _PROJECT_GEMINI_CONTAINER_PATH, "ro"),
                    AgentMount(wrapper_file, _WRAPPER_CONTAINER_PATH, "ro"),
                ),
                tmpfs_mounts=(_HOME_DIR,),
                stdin_secret_env=self.automation_credential,
                container_binary_path="/opt/qualock/gemini",
            )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        del stderr
        return parse_gemini_stream_json(stdout.splitlines())
