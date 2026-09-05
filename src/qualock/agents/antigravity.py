import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from qualock.evidence.antigravity_stream_json import parse_antigravity_stream_json
from qualock.evidence.models import AgentEvidence

from .base import AgentBinary

_WORKSPACE_MOUNT = "/tmp/qualock-workspace"
_OAUTH_TOKEN_NAME = "antigravity-oauth-token"
_SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high"}

_GATE_SOURCE = r"""#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path


_FILE_PATH_KEYS = {
    "view_file": "AbsolutePath",
    "write_to_file": "TargetFile",
    "replace_file_content": "TargetFile",
    "multi_replace_file_content": "TargetFile",
    "list_dir": "DirectoryPath",
    "find_by_name": "SearchDirectory",
    "grep_search": "SearchPath",
}
_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _respond(decision: str) -> None:
    print(json.dumps({"decision": decision}, separators=(",", ":")))


def _inside_workspace(raw_path: object, workspace: Path) -> bool:
    if not isinstance(raw_path, str) or not raw_path or "\0" in raw_path:
        return False
    if _URI_PATTERN.match(raw_path):
        return False
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=False)
    try:
        return resolved == workspace or workspace in resolved.parents
    except (OSError, RuntimeError):
        return False


def _decision() -> str:
    workspace_value = os.environ.get("QUALOCK_WORKSPACE")
    if not workspace_value or not Path(workspace_value).is_absolute():
        return "deny"
    workspace = Path(workspace_value).resolve(strict=True)
    if not workspace.is_dir():
        return "deny"

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return "deny"
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return "deny"
    tool_name = tool_call.get("name")
    arguments = tool_call.get("args")
    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
        return "deny"

    path_key = _FILE_PATH_KEYS.get(tool_name)
    if path_key is not None:
        return "allow" if _inside_workspace(arguments.get(path_key), workspace) else "deny"
    if tool_name == "run_command":
        command = arguments.get("CommandLine")
        cwd = arguments.get("Cwd")
        if not isinstance(command, str) or not command:
            return "deny"
        return "allow" if _inside_workspace(cwd, workspace) else "deny"
    return "deny"


try:
    _respond(_decision())
except Exception:
    _respond("deny")
"""


class AntigravityInvocationError(ValueError):
    pass


@dataclass(frozen=True)
class AntigravityInvocation:
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    config_root: Path
    app_data_root: Path
    oauth_token_path: Path
    workspace_mount: str


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class AntigravityAdapter:
    def __init__(self, auth_app_data: Path) -> None:
        self.auth_app_data = auth_app_data

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
        del workspace
        if reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
            raise AntigravityInvocationError(
                "Antigravity reasoning effort must be low, medium, or high"
            )
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise AntigravityInvocationError("Antigravity timeout must be a positive integer")

        with tempfile.TemporaryDirectory(prefix="qualock-antigravity-") as temporary:
            root = Path(temporary)
            config_root = root / "config"
            app_data_root = root / "appdata"
            plugin_root = config_root / "plugins" / "qualock-security"
            plugin_root.mkdir(parents=True)
            app_data_root.mkdir()

            _write_json(config_root / "import_manifest.json", {"imports": None})
            _write_json(plugin_root / "plugin.json", {"name": "qualock-security"})
            _write_json(
                plugin_root / "hooks.json",
                {
                    "qualock-security": {
                        "PreToolUse": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            'python3 "$HOME/.gemini/config/plugins/'
                                            'qualock-security/qualock_gate.py"'
                                        ),
                                        "timeout": 10,
                                    }
                                ],
                            }
                        ]
                    }
                },
            )
            gate_path = plugin_root / "qualock_gate.py"
            gate_path.write_text(_GATE_SOURCE, encoding="utf-8")
            gate_path.chmod(0o700)

            _write_json(
                app_data_root / "settings.json",
                {
                    "enableTerminalSandbox": True,
                    "toolPermission": "proceed-in-sandbox",
                },
            )
            (app_data_root / _OAUTH_TOKEN_NAME).touch(mode=0o600)

            argv = (
                str(binary.path),
                "--new-project",
                "-p",
                prompt,
                "--model",
                model,
                "--effort",
                reasoning_effort,
                "--sandbox",
                "--disable-slash-commands",
                "--output-format",
                "stream-json",
                "--print-timeout",
                f"{timeout_seconds}s",
            )
            yield AntigravityInvocation(
                argv=argv,
                environment=(
                    ("AGY_CLI_DISABLE_AUTO_UPDATE", "true"),
                    ("QUALOCK_WORKSPACE", _WORKSPACE_MOUNT),
                ),
                config_root=config_root,
                app_data_root=app_data_root,
                oauth_token_path=self.auth_app_data / _OAUTH_TOKEN_NAME,
                workspace_mount=_WORKSPACE_MOUNT,
            )

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        del stderr
        return parse_antigravity_stream_json(stdout.splitlines())
