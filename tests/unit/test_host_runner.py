import os
import subprocess
from pathlib import Path

import pytest

from qualock.agents.antigravity import AntigravityInvocation
from qualock.run.host import HostCommandError, LinuxHostRunner
from qualock.run.process import ProcessResult


def fake_invocation(tmp_path: Path) -> AntigravityInvocation:
    config_root = tmp_path / "private-config"
    app_data_root = tmp_path / "private-app-data"
    token = tmp_path / "real-antigravity-oauth-token"
    config_root.mkdir()
    app_data_root.mkdir()
    token.touch()
    return AntigravityInvocation(
        argv=("/opt/agy", "--output-format", "stream-json"),
        environment=(
            ("AGY_CLI_DISABLE_AUTO_UPDATE", "true"),
            ("QUALOCK_WORKSPACE", "/tmp/qualock-workspace"),
        ),
        config_root=config_root,
        app_data_root=app_data_root,
        oauth_token_path=token,
        workspace_mount="/tmp/qualock-workspace",
    )


def _result(
    *,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=0.125,
        timed_out=timed_out,
    )


def test_build_agent_argv_has_exact_private_mount_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    home = tmp_path / "home"
    invocation = fake_invocation(tmp_path)

    argv = LinuxHostRunner(home=home).build_agent_argv(
        workspace=workspace,
        invocation=invocation,
    )

    config_target = home / ".gemini" / "config"
    app_data_target = home / ".gemini" / "antigravity-cli"
    assert argv == [
        "bwrap",
        "--unshare-user",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/qualock-workspace",
        "--bind",
        str(workspace.resolve()),
        "/tmp/qualock-workspace",
        "--bind",
        str(invocation.config_root.resolve()),
        str(config_target),
        "--bind",
        str(invocation.app_data_root.resolve()),
        str(app_data_target),
        "--ro-bind",
        str(invocation.oauth_token_path),
        str(app_data_target / "antigravity-oauth-token"),
        "--ro-bind",
        str((workspace / ".git").resolve()),
        "/tmp/qualock-workspace/.git",
        "--ro-bind",
        str((workspace / ".git").resolve()),
        str((workspace / ".git").resolve()),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        "/tmp/qualock-workspace",
        "--setenv",
        "AGY_CLI_DISABLE_AUTO_UPDATE",
        "true",
        "--setenv",
        "QUALOCK_WORKSPACE",
        "/tmp/qualock-workspace",
        "/opt/agy",
        "--output-format",
        "stream-json",
    ]
    assert "--share-net" not in argv
    assert "--unshare-net" not in argv
    assert "--dangerously-skip-permissions" not in argv


def _bindings(argv: list[str], flag: str) -> set[tuple[str, str]]:
    return {
        (argv[index + 1], argv[index + 2])
        for index, argument in enumerate(argv)
        if argument == flag
    }


def test_build_agent_argv_mounts_host_root_read_only_with_explicit_writable_mounts(
    tmp_path: Path,
) -> None:
    workspace = Path("/home/qualock/.qualock/work/attempt")
    home = tmp_path / "home"
    invocation = fake_invocation(tmp_path)

    argv = LinuxHostRunner(home=home).build_agent_argv(
        workspace=workspace,
        invocation=invocation,
    )

    assert argv[1:5] == ["--unshare-user", "--ro-bind", "/", "/"]
    read_write = _bindings(argv, "--bind")
    assert read_write == {
        (str(workspace.resolve()), "/tmp/qualock-workspace"),
        (str(invocation.config_root.resolve()), str(home / ".gemini" / "config")),
        (str(invocation.app_data_root.resolve()), str(home / ".gemini" / "antigravity-cli")),
    }
    assert ("/", "/") not in read_write
    assert ("/", "/") in _bindings(argv, "--ro-bind")
    assert argv[argv.index("--tmpfs") : argv.index("--tmpfs") + 2] == ["--tmpfs", "/tmp"]
    assert argv.index("--tmpfs") < argv.index("--dir") < argv.index("--chdir")


def test_build_agent_argv_mounts_private_procfs_over_the_read_only_root(
    tmp_path: Path,
) -> None:
    # The agent's own terminal sandbox re-execs itself into a nested user
    # namespace, which requires writing /proc/self/{setgroups,uid_map,gid_map}.
    # `--ro-bind / /` makes the inherited /proc read-only, so those writes fail
    # EROFS and the nested sandbox never starts.  A private procfs must be
    # mounted over the read-only root without making the root itself writable.
    workspace = Path("/home/qualock/.qualock/work/attempt")
    invocation = fake_invocation(tmp_path)

    argv = LinuxHostRunner(home=tmp_path / "home").build_agent_argv(
        workspace=workspace,
        invocation=invocation,
    )

    assert argv[argv.index("--proc") : argv.index("--proc") + 2] == ["--proc", "/proc"]
    assert argv.index("--ro-bind") < argv.index("--proc") < argv.index("--chdir")
    assert ("/proc", "/proc") not in _bindings(argv, "--bind")
    assert ("/", "/") not in _bindings(argv, "--bind")


def test_build_agent_argv_protects_git_through_original_workspace_path(tmp_path: Path) -> None:
    workspace = Path("/home/qualock/.qualock/work/attempt")
    invocation = fake_invocation(tmp_path)

    argv = LinuxHostRunner(home=tmp_path / "home").build_agent_argv(
        workspace=workspace,
        invocation=invocation,
    )

    git_dir = str((workspace / ".git").resolve())
    read_only_bindings = _bindings(argv, "--ro-bind")
    assert (git_dir, "/tmp/qualock-workspace/.git") in read_only_bindings
    assert (git_dir, git_dir) in read_only_bindings


def test_run_agent_uses_process_tree_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    invocation = fake_invocation(tmp_path)
    seen: dict[str, object] = {}

    def fake_run_process_tree(argv: list[str], *, timeout_seconds: float) -> ProcessResult:
        seen["argv"] = argv
        seen["timeout_seconds"] = timeout_seconds
        return _result(exit_code=9, stdout="events", stderr="diagnostic")

    monkeypatch.setattr("qualock.run.host.run_process_tree", fake_run_process_tree)

    state = LinuxHostRunner(home=tmp_path / "home").run_agent(
        workspace, invocation, timeout_seconds=17
    )

    assert seen["argv"][0] == "bwrap"  # type: ignore[index]
    assert seen["timeout_seconds"] == 17
    assert state.workspace == workspace
    assert state.stdout == "events"
    assert state.stderr == "diagnostic"
    assert state.exit_code == 9
    assert state.elapsed_ms == 125


def test_run_setup_executes_each_command_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Path | None, float]] = []

    def fake_run_process_tree(
        argv: list[str], *, cwd: Path | None = None, timeout_seconds: float
    ) -> ProcessResult:
        calls.append((argv, cwd, timeout_seconds))
        return _result()

    monkeypatch.setattr("qualock.run.host.run_process_tree", fake_run_process_tree)

    LinuxHostRunner().run_setup(tmp_path, ["first command", "second command"], timeout_seconds=23)

    assert calls == [
        (["sh", "-lc", "first command"], tmp_path, 23),
        (["sh", "-lc", "second command"], tmp_path, 23),
    ]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_result(exit_code=None, timed_out=True), "timed out"),
        (_result(exit_code=4, stderr="setup broke"), "setup broke"),
    ],
)
def test_run_setup_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: ProcessResult,
    message: str,
) -> None:
    monkeypatch.setattr("qualock.run.host.run_process_tree", lambda *args, **kwargs: result)

    with pytest.raises(HostCommandError, match=message):
        LinuxHostRunner().run_setup(tmp_path, ["bad command"], timeout_seconds=5)


def test_inspect_agent_state_uses_exact_qualock_owned_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    calls: list[tuple[list[str], Path | None, float]] = []
    environments: list[dict[str, str] | None] = []
    results = iter(
        [
            _result(stdout="src/a.py\0tests/a.py\0"),
            _result(stdout="new.txt\0src/a.py\0"),
            _result(stdout="binary patch"),
        ]
    )

    def fake_run_process_tree(
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float,
    ) -> ProcessResult:
        calls.append((argv, cwd, timeout_seconds))
        environments.append(env)
        return next(results)

    monkeypatch.setattr("qualock.run.host.run_process_tree", fake_run_process_tree)

    evidence = LinuxHostRunner().inspect_agent_state(tmp_path)

    assert calls == [
        (["git", "diff", "--name-only", "-z", "HEAD"], tmp_path, 60),
        (
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            tmp_path,
            60,
        ),
        (["git", "diff", "--binary", "HEAD"], tmp_path, 60),
    ]
    assert evidence.changed_paths == ("src/a.py", "tests/a.py", "new.txt")
    assert evidence.patch == "binary patch"
    safe_environment = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }
    assert len(environments) == 3
    assert all(environment is not None for environment in environments)
    first_environment = environments[0]
    assert first_environment is not None
    assert first_environment["GIT_WORK_TREE"] == str(tmp_path.resolve())
    assert Path(first_environment["GIT_DIR"]).name == "git"
    assert not Path(first_environment["GIT_DIR"]).exists()
    assert {
        key: value
        for key, value in first_environment.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE"}
    } == safe_environment
    assert environments == [first_environment, first_environment, first_environment]


def test_inspect_agent_state_does_not_execute_local_diff_configuration(tmp_path: Path) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "QuaLock Test"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "qualock@example.invalid"],
        cwd=workspace,
        check=True,
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace, check=True)

    sentinel = tmp_path / "external-diff-executed"
    payload = tmp_path / "external-diff"
    payload.write_text(f"#!/bin/sh\nprintf executed > {sentinel}\n", encoding="utf-8")
    payload.chmod(0o700)
    subprocess.run(
        ["git", "config", "diff.external", str(payload)],
        cwd=workspace,
        check=True,
    )
    tracked.write_text("changed\n", encoding="utf-8")

    evidence = LinuxHostRunner().inspect_agent_state(workspace)

    assert not sentinel.exists()
    assert evidence.changed_paths == ("tracked.txt",)
    assert "diff --git a/tracked.txt b/tracked.txt" in evidence.patch


def test_run_grader_uses_copy_applies_patch_and_scrubs_account_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    (workspace / "agent.txt").write_text("agent\n", encoding="utf-8")
    grader_patch = tmp_path / "hidden.patch"
    grader_patch.write_text(
        "diff --git a/hidden.txt b/hidden.txt\nnew file mode 100644\nindex 0000000..e69de29\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGY_CLI_DISABLE_AUTO_UPDATE", "secret-ish")
    monkeypatch.setenv("ANTIGRAVITY_ACCOUNT", "account")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "google")
    monkeypatch.setenv("CLOUDSDK_AUTH_ACCESS_TOKEN", "cloud")
    monkeypatch.setenv("QUALOCK_WORKSPACE", "/tmp/agent-workspace")

    result = LinuxHostRunner().run_grader(
        workspace=workspace,
        grader_patch=grader_patch,
        commands=[
            "test -f hidden.txt",
            'test -z "${AGY_CLI_DISABLE_AUTO_UPDATE:-}"',
            'test -z "${ANTIGRAVITY_ACCOUNT:-}"',
            'test -z "${GEMINI_API_KEY:-}"',
            'test -z "${GOOGLE_API_KEY:-}"',
            'test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"',
            'test -z "${QUALOCK_WORKSPACE:-}"',
            "printf grader > agent.txt",
            "pwd",
        ],
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "qualock-grader-" in result.stdout
    assert (workspace / "agent.txt").read_text(encoding="utf-8") == "agent\n"
    assert not (workspace / "hidden.txt").exists()


def test_run_grader_constructs_a_minimal_allowlisted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    grader_patch = tmp_path / "hidden.patch"
    grader_patch.write_text(
        "diff --git a/hidden.txt b/hidden.txt\nnew file mode 100644\nindex 0000000..e69de29\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/qualock/test/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/inject.so")
    monkeypatch.setenv("PYTHONPATH", "/tmp/inject-python")
    captured: dict[str, str] = {}

    def fake_run_process_tree(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
    ) -> ProcessResult:
        del argv, cwd, timeout_seconds
        captured.update(env)
        return _result()

    monkeypatch.setattr("qualock.run.host.run_process_tree", fake_run_process_tree)

    LinuxHostRunner().run_grader(
        workspace=workspace,
        grader_patch=grader_patch,
        commands=["true"],
        timeout_seconds=10,
    )

    assert captured["PATH"] == "/qualock/test/bin"
    assert captured["LANG"] == "C.UTF-8"
    assert set(captured) <= {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "TMPDIR",
        "TZ",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
    assert "GITHUB_TOKEN" not in captured
    assert "AWS_SECRET_ACCESS_KEY" not in captured
    assert "OPENAI_API_KEY" not in captured
    assert "ANTHROPIC_API_KEY" not in captured
    assert "SSH_AUTH_SOCK" not in captured
    assert "LD_PRELOAD" not in captured
    assert "PYTHONPATH" not in captured


def test_run_grader_rejects_symlink_that_escapes_grader_copy(tmp_path: Path) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (workspace / "grade-target").symlink_to(outside)
    grader_patch = tmp_path / "hidden.patch"
    grader_patch.write_text(
        "diff --git a/hidden.txt b/hidden.txt\nnew file mode 100644\nindex 0000000..e69de29\n",
        encoding="utf-8",
    )

    with pytest.raises(HostCommandError, match="symlink.*outside grader workspace"):
        LinuxHostRunner().run_grader(
            workspace=workspace,
            grader_patch=grader_patch,
            commands=["printf mutated > grade-target"],
            timeout_seconds=10,
        )

    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_run_grader_reports_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    grader_patch = tmp_path / "hidden.patch"
    grader_patch.write_text(
        "diff --git a/hidden.txt b/hidden.txt\nnew file mode 100644\nindex 0000000..e69de29\n",
        encoding="utf-8",
    )

    result = LinuxHostRunner().run_grader(
        workspace=workspace,
        grader_patch=grader_patch,
        commands=["sleep 60"],
        timeout_seconds=0.05,
    )

    assert result.exit_code is None
    assert result.timed_out is True
