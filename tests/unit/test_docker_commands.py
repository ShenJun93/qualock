from pathlib import Path

import pytest

from qualock.agents.base import AgentRuntimeDependency
from qualock.run.docker import DockerRunner


def test_agent_phase_never_mounts_or_mentions_grader(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    agent_root = tmp_path / "agent-package"
    grader = tmp_path / "secret-grader"
    grader.mkdir()
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-1",
        agent_binary=agent_root / "codex",
        agent_argv=["/host/codex", "exec", "--json", "fix it"],
        environment={"CODEX_HOME": "/auth"},
        agent_container_path="/opt/qualock/codex",
    )
    rendered = " ".join(argv)
    assert str(grader) not in rendered
    assert "/private/grader" not in rendered
    assert f"{(agent_root / 'codex').resolve()}:/opt/qualock/codex:ro" in argv
    assert "/opt/qualock/codex" in argv


def test_grader_phase_mounts_private_grader_read_only(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    grader = tmp_path / "secret-grader"
    grader.mkdir()
    argv = runner.build_grader_run_argv(
        frozen_image="sha256:frozen",
        grader_root=grader,
        command="python /private/grader/grade.py",
    )
    assert f"{grader.resolve()}:/private/grader:ro" in argv
    assert argv[-3:] == ["sh", "-lc", "python /private/grader/grade.py"]


def test_agent_container_is_not_auto_removed_before_freeze(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-1",
        agent_binary=tmp_path / "agent-package/codex",
        agent_argv=["/host/codex", "exec", "task"],
        environment={},
    )
    assert "--rm" not in argv
    assert argv[:2] == ["docker", "create"]


def test_agent_phase_can_mount_auth_read_only_without_mounting_grader(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    auth = tmp_path / "auth"
    auth.mkdir()
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-auth",
        agent_binary=tmp_path / "agent-package/codex",
        agent_argv=["/host/codex", "exec", "task"],
        environment={"CODEX_HOME": "/opt/qualock/auth"},
        extra_mounts=[(auth, "/opt/qualock/auth", "ro")],
    )
    assert f"{auth.resolve()}:/opt/qualock/auth:ro" in argv
    assert "/private/grader" not in " ".join(argv)


def test_agent_phase_can_seed_tmpfs_before_exec(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-auth",
        agent_binary=tmp_path / "agent-package/codex",
        agent_argv=["/host/codex", "exec", "task"],
        environment={"CODEX_HOME": "/opt/qualock/auth"},
        extra_mounts=[(auth_file, "/opt/qualock/auth-seed.json", "ro")],
        tmpfs_mounts=["/opt/qualock/auth"],
        bootstrap_copy=("/opt/qualock/auth-seed.json", "/opt/qualock/auth/auth.json"),
        agent_container_path="/opt/qualock/codex",
    )
    tmpfs_index = argv.index("--tmpfs")
    assert argv[tmpfs_index + 1] == "/opt/qualock/auth:rw,nosuid,nodev,noexec,mode=0700"
    assert f"{auth_file.resolve()}:/opt/qualock/auth-seed.json:ro" in argv
    assert not any("/opt/qualock/auth/auth.json:ro" in item for item in argv)
    image_index = argv.index("sha256:prepared")
    command = argv[image_index + 1 :]
    assert command[:2] == ["sh", "-c"]
    assert 'cat "$1" > "$2"' in command[2]
    assert 'exec "$@"' in command[2]
    assert command[4:6] == ["/opt/qualock/auth-seed.json", "/opt/qualock/auth/auth.json"]
    assert command[-3:] == ["/opt/qualock/codex", "exec", "task"]


def test_agent_phase_can_inject_secret_environment_from_stdin_without_secret_argv(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-stdin",
        agent_binary=tmp_path / "agent-package/claude",
        agent_argv=["/host/claude", "-p", "task"],
        environment={"CLAUDE_CONFIG_DIR": "/opt/qualock/claude-home"},
        stdin_secret_env_name="CLAUDE_CODE_OAUTH_TOKEN",
        agent_container_path="/opt/qualock/claude",
    )
    image_index = argv.index("sha256:prepared")
    assert "--interactive" in argv[:image_index]
    command = argv[image_index + 1 :]
    assert command[:2] == ["sh", "-c"]
    assert 'export "$1=$(cat)"' in command[2]
    assert "secret=$(cat)" not in command[2]
    assert command[4] == "CLAUDE_CODE_OAUTH_TOKEN"
    assert command[-3:] == ["/opt/qualock/claude", "-p", "task"]
    assert "secret-value" not in " ".join(argv)


def test_run_agent_streams_secret_only_to_docker_start(tmp_path: Path, monkeypatch) -> None:
    from qualock.run.models import PreparedImage
    from qualock.run.process import ProcessResult

    calls: list[tuple[list[str], str | None]] = []
    runner = DockerRunner(docker_executable="docker")

    def fake_run(argv, *, timeout_seconds, input_text=None):
        calls.append((list(argv), input_text))
        return ProcessResult(0, "", "", 0.01, False)

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_inspect_image_id", lambda reference: "sha256:frozen")
    runner.run_agent(
        prepared=PreparedImage("prepared", "sha256:prepared"),
        container_name="ub-agent-stdin",
        agent_binary=tmp_path / "claude",
        agent_argv=[str(tmp_path / "claude"), "-p", "task"],
        environment={},
        stdin_secret_env=("CLAUDE_CODE_OAUTH_TOKEN", "secret-value"),
        agent_container_path="/opt/qualock/claude",
        frozen_tag="frozen",
        timeout_seconds=60,
    )
    start_call = next((argv, data) for argv, data in calls if "start" in argv)
    assert "--interactive" in start_call[0]
    assert start_call[1] == "secret-value"
    assert all("secret-value" not in " ".join(argv) for argv, _ in calls)
    create_call = next(argv for argv, _ in calls if "create" in argv)
    assert not any("CLAUDE_CODE_OAUTH_TOKEN=secret-value" in item for item in create_call)


def test_daemon_ready_requires_successful_docker_info(monkeypatch) -> None:
    from qualock.run.process import ProcessResult

    runner = DockerRunner(docker_executable="docker")
    daemon_ready = getattr(runner, "daemon_ready", None)
    assert daemon_ready is not None, "DockerRunner must distinguish CLI presence from daemon readiness"
    monkeypatch.setattr(runner, "available", lambda: True)

    monkeypatch.setattr(
        "qualock.run.docker.run_process",
        lambda argv, timeout_seconds: ProcessResult(
            exit_code=1,
            stdout="",
            stderr="cannot connect to daemon",
            elapsed_seconds=0.01,
            timed_out=False,
        ),
    )
    assert daemon_ready() is False

    monkeypatch.setattr(
        "qualock.run.docker.run_process",
        lambda argv, timeout_seconds: ProcessResult(
            exit_code=0,
            stdout="29.6.1",
            stderr="",
            elapsed_seconds=0.01,
            timed_out=False,
        ),
    )
    assert daemon_ready() is True


def test_prepare_bootstraps_runtime_bubblewrap(tmp_path: Path, monkeypatch) -> None:
    from qualock.canary.models import CanarySpec
    from qualock.run.process import ProcessResult
    source = tmp_path / "source"
    source.mkdir()
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    spec = CanarySpec.model_validate({
        "schema_version": 1, "id": "sample", "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"}, "task": "Fix it", "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(grader), "command": ["pytest -q"]},
        "constraints": {"protected_paths": []}, "critical": True,
    })
    seen = {}
    runner = DockerRunner()
    def fake_run(argv, *, timeout_seconds):
        if "build" in argv:
            path = Path(argv[argv.index("--file") + 1])
            seen["dockerfile"] = path.read_text(encoding="utf-8")
        return ProcessResult(0, "", "", 0.01, False)
    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_inspect_image_id", lambda reference: "sha256:prepared")
    runner.prepare(source, spec, image_tag="prepared")
    assert "apt-get install -y --no-install-recommends bubblewrap" in seen["dockerfile"]
    assert "bubblewrap=" not in seen["dockerfile"]
    assert "socat" not in seen["dockerfile"]



def test_prepare_installs_adapter_runtime_dependencies(tmp_path: Path, monkeypatch) -> None:
    from qualock.canary.models import CanarySpec
    from qualock.run.process import ProcessResult

    source = tmp_path / "source"
    source.mkdir()
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    spec = CanarySpec.model_validate({
        "schema_version": 1, "id": "sample", "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"}, "task": "Fix it", "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(grader), "command": ["pytest -q"]},
        "constraints": {"protected_paths": []}, "critical": True,
    })
    seen: dict[str, str] = {}
    runner = DockerRunner()

    def fake_run(argv, *, timeout_seconds):
        if "build" in argv:
            path = Path(argv[argv.index("--file") + 1])
            seen["dockerfile"] = path.read_text(encoding="utf-8")
        return ProcessResult(0, "", "", 0.01, False)

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_inspect_image_id", lambda reference: "sha256:prepared")
    runner.prepare(
        source,
        spec,
        image_tag="prepared",
        runtime_dependencies=(
            AgentRuntimeDependency(command="socat", apt_package="socat"),
        ),
    )

    assert "command -v bwrap" in seen["dockerfile"]
    assert "command -v socat" in seen["dockerfile"]
    assert "apt-get install -y --no-install-recommends bubblewrap socat" in seen["dockerfile"]
    assert "bubblewrap=" not in seen["dockerfile"]
    assert "socat=" not in seen["dockerfile"]


def test_prepare_quotes_runtime_dependency_name_in_error_message(
    tmp_path: Path, monkeypatch
) -> None:
    from qualock.canary.models import CanarySpec
    from qualock.run.process import ProcessResult

    source = tmp_path / "source"
    source.mkdir()
    grader = tmp_path / "grader.patch"
    grader.write_text("patch", encoding="utf-8")
    spec = CanarySpec.model_validate({
        "schema_version": 1, "id": "sample", "name": "Sample",
        "repository": {"url": "https://example.invalid/repo.git", "base_sha": "a" * 40},
        "runtime": {"image": "python:3.12-slim"}, "task": "Fix it", "setup": [],
        "agent": {"timeout_seconds": 60},
        "grader": {"patch": str(grader), "command": ["pytest -q"]},
        "constraints": {"protected_paths": []}, "critical": True,
    })
    seen: dict[str, str] = {}
    runner = DockerRunner()

    def fake_run(argv, *, timeout_seconds):
        del timeout_seconds
        if "build" in argv:
            path = Path(argv[argv.index("--file") + 1])
            seen["dockerfile"] = path.read_text(encoding="utf-8")
        return ProcessResult(0, "", "", 0.01, False)

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_inspect_image_id", lambda reference: "sha256:prepared")
    runner.prepare(
        source, spec, image_tag="prepared",
        runtime_dependencies=(AgentRuntimeDependency(command="odd'name", apt_package="socat"),),
    )
    assert (
        "else echo 'Qualock agent runner requires bwrap and odd'\"'\"'name "
        "in the runtime image'"
    ) in seen["dockerfile"]


def test_agent_container_relaxes_seccomp_only_for_inner_bubblewrap(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-seccomp",
        agent_binary=tmp_path / "agent-package/codex",
        agent_argv=["/host/codex", "exec", "task"],
        environment={},
    )
    assert "--security-opt" in argv
    index = argv.index("--security-opt")
    assert argv[index + 1] == "seccomp=unconfined"

    grader = runner.build_grader_run_argv(
        frozen_image="sha256:frozen",
        grader_root=tmp_path,
        command="pytest -q",
    )
    assert "seccomp=unconfined" not in grader


def test_agent_binary_can_use_adapter_selected_container_path(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    binary = tmp_path / "agent"
    binary.write_text("x", encoding="utf-8")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="q1",
        agent_binary=binary,
        agent_argv=[str(binary), "run"],
        environment={},
        agent_container_path="/opt/qualock/claude",
    )
    assert f"{binary.resolve()}:/opt/qualock/claude:ro" in argv
    assert argv[-2:] == ["/opt/qualock/claude", "run"]


def test_agent_container_path_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agent container path must be absolute"):
        DockerRunner().build_agent_create_argv(
            prepared_image="p",
            container_name="q",
            agent_binary=tmp_path / "a",
            agent_argv=["a"],
            environment={},
            agent_container_path="relative/agent",
        )
