from pathlib import Path

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


def test_agent_phase_can_use_tmpfs_for_ephemeral_state(tmp_path: Path) -> None:
    runner = DockerRunner(docker_executable="docker")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="ub-agent-auth",
        agent_binary=tmp_path / "agent-package/codex",
        agent_argv=["/host/codex", "exec", "task"],
        environment={"CODEX_HOME": "/opt/qualock/auth"},
        extra_mounts=[(auth_file, "/opt/qualock/auth/auth.json", "ro")],
        tmpfs_mounts=["/opt/qualock/auth"],
    )
    assert ["--tmpfs", "/opt/qualock/auth:rw,nosuid,nodev,noexec,mode=0700"] == argv[5:7]
    assert f"{auth_file.resolve()}:/opt/qualock/auth/auth.json:ro" in argv


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
