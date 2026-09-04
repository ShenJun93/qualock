import shlex
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from qualock.canary.models import CanarySpec

from .models import AgentStateEvidence, FrozenAgentState, GradeResult, PreparedImage
from .process import ProcessResult, run_process


def parse_nul_paths(raw: str) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw.split("\0"):
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


class DockerUnavailableError(RuntimeError):
    pass


class DockerCommandError(RuntimeError):
    pass


class DockerRunner:
    def __init__(self, docker_executable: str = "docker") -> None:
        self.docker_executable = docker_executable

    def available(self) -> bool:
        return shutil.which(self.docker_executable) is not None

    def daemon_ready(self) -> bool:
        if not self.available():
            return False
        result = run_process(
            [self.docker_executable, "info", "--format", "{{.ServerVersion}}"],
            timeout_seconds=10,
        )
        return not result.timed_out and result.exit_code == 0

    def _require(self) -> None:
        if not self.available():
            raise DockerUnavailableError(f"Docker CLI not found: {self.docker_executable}")

    def _run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProcessResult:
        self._require()
        result = run_process(argv, timeout_seconds=timeout_seconds)
        if result.timed_out:
            return result
        return result

    def _inspect_image_id(self, reference: str) -> str:
        result = self._run(
            [
                self.docker_executable,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                reference,
            ],
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            raise DockerCommandError(result.stderr.strip() or f"cannot inspect image {reference}")
        return result.stdout.strip()

    def prepare(
        self,
        source_dir: Path,
        canary: CanarySpec,
        *,
        image_tag: str,
        timeout_seconds: float = 1200,
    ) -> PreparedImage:
        self._require()
        dockerfile_lines = [
            f"FROM {canary.runtime.image}",
            "WORKDIR /workspace",
            "COPY . /workspace",
            (
                "RUN if command -v bwrap >/dev/null 2>&1; then :; "
                "elif command -v apt-get >/dev/null 2>&1; then "
                "apt-get update && apt-get install -y --no-install-recommends "
                "bubblewrap=0.8.0-2+deb12u1 && rm -rf /var/lib/apt/lists/*; "
                "else echo 'Qualock agent runner requires bubblewrap in the runtime image' >&2; "
                "exit 127; fi"
            ),
        ]
        dockerfile_lines.extend(f"RUN {command}" for command in canary.setup)
        with tempfile.TemporaryDirectory(prefix="qualock-dockerfile-") as temp:
            dockerfile = Path(temp) / "Dockerfile"
            dockerfile.write_text("\n".join(dockerfile_lines) + "\n", encoding="utf-8")
            result = self._run(
                [
                    self.docker_executable,
                    "build",
                    "--quiet",
                    "--tag",
                    image_tag,
                    "--file",
                    str(dockerfile),
                    str(source_dir.resolve()),
                ],
                timeout_seconds=timeout_seconds,
            )
        if result.timed_out or result.exit_code != 0:
            raise DockerCommandError(result.stderr.strip() or "failed to prepare canary image")
        return PreparedImage(reference=image_tag, digest=self._inspect_image_id(image_tag))

    def build_agent_create_argv(
        self,
        *,
        prepared_image: str,
        container_name: str,
        agent_binary: Path,
        agent_argv: Sequence[str],
        environment: Mapping[str, str],
        extra_mounts: Sequence[tuple[Path, str, str]] = (),
        tmpfs_mounts: Sequence[str] = (),
        bootstrap_copy: tuple[str, str] | None = None,
        agent_container_path: str = "/opt/qualock/agent",
        replace_agent_binary: bool = True,
    ) -> list[str]:
        argv = [
            self.docker_executable,
            "create",
            "--name",
            container_name,
            "--workdir",
            "/workspace",
            "--security-opt",
            "seccomp=unconfined",
        ]
        for key, value in sorted(environment.items()):
            argv.extend(["--env", f"{key}={value}"])
        for container_path in tmpfs_mounts:
            if not container_path.startswith("/"):
                raise ValueError(f"tmpfs mount must be absolute: {container_path}")
            argv.extend(
                [
                    "--tmpfs",
                    f"{container_path}:rw,nosuid,nodev,noexec,mode=0700",
                ]
            )
        for host_path, container_path, mode in extra_mounts:
            if mode not in {"ro", "rw"}:
                raise ValueError(f"invalid mount mode: {mode}")
            argv.extend(["--volume", f"{host_path.resolve()}:{container_path}:{mode}"])
        command = list(agent_argv)
        if replace_agent_binary:
            if not agent_container_path.startswith("/"):
                raise ValueError("agent container path must be absolute")
            argv.extend(
                [
                    "--volume",
                    f"{agent_binary.resolve()}:{agent_container_path}:ro",
                ]
            )
            if not command:
                raise ValueError("agent_argv must not be empty")
            command[0] = agent_container_path
        if bootstrap_copy is not None:
            source, destination = bootstrap_copy
            if not source.startswith("/") or not destination.startswith("/"):
                raise ValueError("bootstrap copy paths must be absolute")
            command = [
                "sh",
                "-c",
                'set -eu; umask 077; cat "$1" > "$2"; shift 2; exec "$@"',
                "qualock-bootstrap",
                source,
                destination,
                *command,
            ]
        argv.append(prepared_image)
        argv.extend(command)
        return argv

    def run_agent(
        self,
        *,
        prepared: PreparedImage,
        container_name: str,
        agent_binary: Path,
        agent_argv: Sequence[str],
        environment: Mapping[str, str],
        extra_mounts: Sequence[tuple[Path, str, str]] = (),
        tmpfs_mounts: Sequence[str] = (),
        bootstrap_copy: tuple[str, str] | None = None,
        agent_container_path: str = "/opt/qualock/agent",
        frozen_tag: str,
        timeout_seconds: float,
    ) -> FrozenAgentState:
        create = self._run(
            self.build_agent_create_argv(
                prepared_image=prepared.digest,
                container_name=container_name,
                agent_binary=agent_binary,
                agent_argv=agent_argv,
                environment=environment,
                extra_mounts=extra_mounts,
                tmpfs_mounts=tmpfs_mounts,
                bootstrap_copy=bootstrap_copy,
                agent_container_path=agent_container_path,
            ),
            timeout_seconds=30,
        )
        if create.exit_code != 0:
            raise DockerCommandError(create.stderr.strip() or "failed to create agent container")

        started = self._run(
            [self.docker_executable, "start", "--attach", container_name],
            timeout_seconds=timeout_seconds,
        )
        committed = self._run(
            [self.docker_executable, "commit", container_name, frozen_tag],
            timeout_seconds=120,
        )
        if committed.exit_code != 0:
            raise DockerCommandError(committed.stderr.strip() or "failed to freeze agent state")
        digest = self._inspect_image_id(frozen_tag)
        return FrozenAgentState(
            reference=frozen_tag,
            digest=digest,
            container_name=container_name,
            stdout=started.stdout,
            stderr=started.stderr,
            exit_code=started.exit_code if not started.timed_out else None,
            elapsed_ms=int(started.elapsed_seconds * 1000),
        )

    def inspect_agent_state(self, state: FrozenAgentState) -> AgentStateEvidence:
        changed = self._run(
            [
                self.docker_executable,
                "run",
                "--rm",
                "--workdir",
                "/workspace",
                state.digest,
                "sh",
                "-lc",
                "git diff --name-only -z HEAD; git ls-files --others --exclude-standard -z",
            ],
            timeout_seconds=60,
        )
        if changed.exit_code != 0:
            raise DockerCommandError(changed.stderr.strip() or "failed to inspect changed paths")
        patch = self._run(
            [
                self.docker_executable,
                "run",
                "--rm",
                "--workdir",
                "/workspace",
                state.digest,
                "git",
                "diff",
                "--binary",
                "HEAD",
            ],
            timeout_seconds=60,
        )
        if patch.exit_code != 0:
            raise DockerCommandError(patch.stderr.strip() or "failed to inspect agent patch")
        return AgentStateEvidence(changed_paths=parse_nul_paths(changed.stdout), patch=patch.stdout)

    def build_grader_run_argv(
        self,
        *,
        frozen_image: str,
        grader_root: Path,
        command: str,
    ) -> list[str]:
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--workdir",
            "/workspace",
            "--volume",
            f"{grader_root.resolve()}:/private/grader:ro",
            frozen_image,
            "sh",
            "-lc",
            command,
        ]

    def run_grader(
        self,
        *,
        state: FrozenAgentState,
        grader_patch: Path,
        commands: Sequence[str],
        timeout_seconds: float = 600,
    ) -> GradeResult:
        grader_root = grader_patch.resolve().parent
        mounted_patch = f"/private/grader/{shlex.quote(grader_patch.name)}"
        command = " && ".join(
            [f"git apply {mounted_patch}", *commands]
        )
        result = self._run(
            self.build_grader_run_argv(
                frozen_image=state.digest,
                grader_root=grader_root,
                command=command,
            ),
            timeout_seconds=timeout_seconds,
        )
        return GradeResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    def remove_container(self, container_name: str) -> None:
        if not self.available():
            return
        run_process(
            [self.docker_executable, "rm", "--force", container_name],
            timeout_seconds=30,
        )
