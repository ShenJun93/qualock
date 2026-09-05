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
        "--bind",
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
    calls: list[tuple[list[str], Path | None, float]] = []
    results = iter(
        [
            _result(stdout="src/a.py\0tests/a.py\0"),
            _result(stdout="new.txt\0src/a.py\0"),
            _result(stdout="binary patch"),
        ]
    )

    def fake_run_process_tree(
        argv: list[str], *, cwd: Path | None = None, timeout_seconds: float
    ) -> ProcessResult:
        calls.append((argv, cwd, timeout_seconds))
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
