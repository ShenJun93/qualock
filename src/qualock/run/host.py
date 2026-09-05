import os
import shlex
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from qualock.agents.antigravity import AntigravityInvocation

from .models import AgentStateEvidence, GradeResult
from .process import ProcessResult, run_process_tree

_WORKSPACE_MOUNT = "/tmp/qualock-workspace"
_OAUTH_TOKEN_NAME = "antigravity-oauth-token"
_ACCOUNT_ENV_PREFIXES = ("AGY_", "ANTIGRAVITY_", "CLOUDSDK_", "GEMINI_", "GOOGLE_")
_AGENT_ENV_NAMES = {"QUALOCK_WORKSPACE"}
_FORBIDDEN_AGENT_ARGS = {
    "--dangerously-skip-permissions",
    "--share-net",
    "--unshare-net",
}


@dataclass(frozen=True)
class HostAgentState:
    workspace: Path
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_ms: int


class HostCommandError(RuntimeError):
    pass


def _parse_nul_paths(raw: str) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw.split("\0"):
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


def _require_success(result: ProcessResult, operation: str) -> None:
    if result.timed_out:
        raise HostCommandError(f"{operation} timed out")
    if result.exit_code != 0:
        raise HostCommandError(result.stderr.strip() or f"{operation} failed")


class LinuxHostRunner:
    def __init__(self, bwrap_executable: str = "bwrap", *, home: Path | None = None) -> None:
        self.bwrap_executable = bwrap_executable
        self.home = home if home is not None else Path.home()

    def build_agent_argv(
        self,
        *,
        workspace: Path,
        invocation: AntigravityInvocation,
    ) -> list[str]:
        if invocation.workspace_mount != _WORKSPACE_MOUNT:
            raise ValueError(
                f"unsupported Antigravity workspace mount: {invocation.workspace_mount}"
            )
        forbidden = _FORBIDDEN_AGENT_ARGS.intersection(invocation.argv)
        if forbidden:
            raise ValueError(f"forbidden Antigravity argument: {min(forbidden)}")

        config_target = self.home / ".gemini" / "config"
        app_data_target = self.home / ".gemini" / "antigravity-cli"
        argv = [
            self.bwrap_executable,
            "--unshare-user",
            "--bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--dir",
            _WORKSPACE_MOUNT,
            "--bind",
            str(workspace.resolve()),
            _WORKSPACE_MOUNT,
            "--bind",
            str(invocation.config_root.resolve()),
            str(config_target),
            "--bind",
            str(invocation.app_data_root.resolve()),
            str(app_data_target),
            "--ro-bind",
            str(invocation.oauth_token_path),
            str(app_data_target / _OAUTH_TOKEN_NAME),
            "--dev",
            "/dev",
            "--chdir",
            _WORKSPACE_MOUNT,
        ]
        for key, value in invocation.environment:
            argv.extend(["--setenv", key, value])
        argv.extend(invocation.argv)
        return argv

    def run_agent(
        self,
        workspace: Path,
        invocation: AntigravityInvocation,
        timeout_seconds: float,
    ) -> HostAgentState:
        result = run_process_tree(
            self.build_agent_argv(workspace=workspace, invocation=invocation),
            timeout_seconds=timeout_seconds,
        )
        return HostAgentState(
            workspace=workspace,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=None if result.timed_out else result.exit_code,
            elapsed_ms=int(result.elapsed_seconds * 1000),
        )

    def run_setup(
        self,
        workspace: Path,
        commands: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> None:
        for command in commands:
            result = run_process_tree(
                ["sh", "-lc", command],
                cwd=workspace,
                timeout_seconds=timeout_seconds,
            )
            _require_success(result, "host setup")

    def inspect_agent_state(self, workspace: Path) -> AgentStateEvidence:
        commands = (
            ["git", "diff", "--name-only", "-z", "HEAD"],
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            ["git", "diff", "--binary", "HEAD"],
        )
        results: list[ProcessResult] = []
        for command in commands:
            result = run_process_tree(command, cwd=workspace, timeout_seconds=60)
            _require_success(result, "host Git inspection")
            results.append(result)
        return AgentStateEvidence(
            changed_paths=_parse_nul_paths(results[0].stdout + results[1].stdout),
            patch=results[2].stdout,
        )

    def run_grader(
        self,
        *,
        workspace: Path,
        grader_patch: Path,
        commands: Sequence[str],
        timeout_seconds: float = 600,
    ) -> GradeResult:
        with tempfile.TemporaryDirectory(prefix="qualock-grader-") as temporary:
            grader_root = Path(temporary)
            grader_workspace = grader_root / "workspace"
            grader_home = grader_root / "home"
            shutil.copytree(workspace, grader_workspace, symlinks=True)
            grader_home.mkdir()
            environment = self._grader_environment(grader_home)
            command = " && ".join(
                [f"git apply {shlex.quote(str(grader_patch.absolute()))}", *commands]
            )
            result = run_process_tree(
                ["sh", "-lc", command],
                cwd=grader_workspace,
                env=environment,
                timeout_seconds=timeout_seconds,
            )
            return GradeResult(
                exit_code=None if result.timed_out else result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
            )

    @staticmethod
    def _grader_environment(grader_home: Path) -> Mapping[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in _AGENT_ENV_NAMES and not key.startswith(_ACCOUNT_ENV_PREFIXES)
        }
        environment.update(
            {
                "HOME": str(grader_home),
                "XDG_CACHE_HOME": str(grader_home / ".cache"),
                "XDG_CONFIG_HOME": str(grader_home / ".config"),
                "XDG_DATA_HOME": str(grader_home / ".local" / "share"),
                "XDG_STATE_HOME": str(grader_home / ".local" / "state"),
            }
        )
        return environment
